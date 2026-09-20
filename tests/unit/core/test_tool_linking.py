"""Unit tests for tool call <-> tool result linking mechanisms across providers and ContextGraph."""

from src.core.ast.normalizers import (
    GeminiASTNormalizer,
    OpenAIASTNormalizer,
)
from src.core.graph.turn_tree import ContextGraph
from src.schema.ast import BlockType, CanonicalTurn, ContextBlock


def test_tool_invocation_exact_call_id_matching():
    """Verify tool calls and results with matching call_id pair correctly into ToolInvocations."""
    call_block = ContextBlock(
        block_id="call_blk_1",
        block_type=BlockType.ASSISTANT_MSG,
        content_hash="hash_call",
        token_count=15,
        content='{"name": "read_file", "path": "test.txt"}',
        metadata={"name": "read_file", "type": "tool_call"},
        call_id="call_abc_123",
    )
    result_block = ContextBlock(
        block_id="res_blk_1",
        block_type=BlockType.TOOL_RESULT,
        content_hash="hash_res",
        token_count=20,
        content="file contents here",
        metadata={"name": "read_file", "is_error": False},
        call_id="call_abc_123",
    )

    turn = CanonicalTurn(
        turn_id="turn_1",
        correlation_id="corr_1",
        session_id="sess_1",
        turn_index=0,
        timestamp=1000.0,
        provider="anthropic",
        model="claude-3-5-sonnet",
        conversation_history=[call_block],
        tool_results=[result_block],
    )

    invocations = turn.tool_invocations
    assert len(invocations) == 1
    inv = invocations[0]
    assert inv.call_id == "call_abc_123"
    assert inv.tool_name == "read_file"
    assert inv.call_block_id == "call_blk_1"
    assert inv.result_block_id == "res_blk_1"
    assert inv.is_error is False

    turn_dict = turn.to_dict()
    assert "tool_invocations" in turn_dict
    assert len(turn_dict["tool_invocations"]) == 1
    assert turn_dict["tool_invocations"][0]["call_id"] == "call_abc_123"
    assert turn_dict["tool_invocations"][0]["call_block_id"] == "call_blk_1"
    assert turn_dict["tool_invocations"][0]["result_block_id"] == "res_blk_1"


def test_tool_invocation_fallback_by_tool_name():
    """Verify pairing falls back to matching tool name when call_id is missing or unassigned."""
    call_block = ContextBlock(
        block_id="call_blk_name",
        block_type=BlockType.ASSISTANT_MSG,
        content_hash="hash_call_2",
        token_count=10,
        content='{"name": "calculator"}',
        metadata={"name": "calculator", "type": "tool_use"},
    )
    result_block = ContextBlock(
        block_id="res_blk_name",
        block_type=BlockType.TOOL_RESULT,
        content_hash="hash_res_2",
        token_count=5,
        content="42",
        metadata={"name": "calculator", "is_error": False},
    )

    turn = CanonicalTurn(
        turn_id="turn_2",
        correlation_id="corr_2",
        session_id="sess_1",
        turn_index=1,
        timestamp=1001.0,
        provider="gemini",
        model="gemini-1.5-pro",
        assistant_blocks=[call_block],
        tool_results=[result_block],
    )

    invocations = turn.tool_invocations
    assert len(invocations) == 1
    inv = invocations[0]
    assert inv.tool_name == "calculator"
    assert inv.call_block_id == "call_blk_name"
    assert inv.result_block_id == "res_blk_name"


def test_tool_invocation_orphaned_calls_and_results():
    """Verify orphaned calls (unanswered in turn) and orphaned results (from prior turn) are tracked."""
    orphan_call = ContextBlock(
        block_id="call_orphan",
        block_type=BlockType.ASSISTANT_MSG,
        content_hash="hash_c3",
        token_count=10,
        content="call",
        metadata={"name": "execute_command", "type": "tool_call"},
        call_id="call_pending",
    )
    orphan_result = ContextBlock(
        block_id="res_orphan",
        block_type=BlockType.TOOL_RESULT,
        content_hash="hash_r3",
        token_count=12,
        content="finished",
        metadata={"name": "prior_tool", "is_error": True},
        call_id="call_prior",
    )

    turn = CanonicalTurn(
        turn_id="turn_3",
        correlation_id="corr_3",
        session_id="sess_1",
        turn_index=2,
        timestamp=1002.0,
        provider="openai",
        model="gpt-4o",
        assistant_blocks=[orphan_call],
        tool_results=[orphan_result],
    )

    invocations = turn.tool_invocations
    assert len(invocations) == 2

    # One is call with no result
    call_inv = next(i for i in invocations if i.call_block_id == "call_orphan")
    assert call_inv.call_id == "call_pending"
    assert call_inv.result_block_id is None

    # One is result with no call in this turn
    res_inv = next(i for i in invocations if i.result_block_id == "res_orphan")
    assert res_inv.call_id == "call_prior"
    assert res_inv.call_block_id is None
    assert res_inv.is_error is True


def test_openai_normalizer_populates_call_id():
    """Verify OpenAIASTNormalizer links tool_call and role=tool via call_id."""
    normalizer = OpenAIASTNormalizer()
    payload = {
        "correlation_id": "openai-tool-1",
        "session_id": "sess-oai",
        "turn_index": 0,
        "timestamp": 1000.0,
        "provider": "openai",
        "request_payload": {
            "model": "gpt-4o",
            "messages": [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_openai_999",
                            "type": "function",
                            "function": {
                                "name": "get_weather",
                                "arguments": '{"location": "Tokyo"}',
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_openai_999",
                    "name": "get_weather",
                    "content": '{"temp": "18C", "condition": "sunny"}',
                },
            ],
        },
        "response_payload": {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "The weather in Tokyo is 18C and sunny.",
                    }
                }
            ]
        },
    }

    turn = normalizer.normalize(payload)
    # Check history tool call
    call_blks = [b for b in turn.conversation_history if b.call_id == "call_openai_999"]
    assert len(call_blks) == 1
    assert call_blks[0].metadata["tool_use_id"] == "call_openai_999"

    # Check tool result
    assert len(turn.tool_results) == 1
    res_blk = turn.tool_results[0]
    assert res_blk.call_id == "call_openai_999"

    # Check turn-level invocation pairing
    assert len(turn.tool_invocations) == 1
    inv = turn.tool_invocations[0]
    assert inv.call_id == "call_openai_999"
    assert inv.tool_name == "get_weather"
    assert inv.call_block_id == call_blks[0].block_id
    assert inv.result_block_id == res_blk.block_id


def test_gemini_normalizer_populates_call_id():
    """Verify GeminiASTNormalizer links functionCall and functionResponse via call_id."""
    normalizer = GeminiASTNormalizer()
    payload = {
        "correlation_id": "gemini-tool-1",
        "session_id": "sess-gemini",
        "turn_index": 0,
        "timestamp": 1000.0,
        "provider": "gemini",
        "request_payload": {
            "contents": [
                {
                    "role": "model",
                    "parts": [
                        {
                            "functionCall": {
                                "id": "call_gemini_888",
                                "name": "search_database",
                                "args": {"query": "test"},
                            }
                        }
                    ],
                },
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "id": "call_gemini_888",
                                "name": "search_database",
                                "response": {"results": ["item 1", "item 2"]},
                            }
                        }
                    ],
                },
            ]
        },
        "response_payload": {
            "candidates": [{"content": {"parts": [{"text": "Here are the search results."}]}}]
        },
    }

    turn = normalizer.normalize(payload)
    call_blks = [b for b in turn.conversation_history if b.call_id == "call_gemini_888"]
    assert len(call_blks) == 1
    assert call_blks[0].metadata["name"] == "search_database"

    assert len(turn.tool_results) == 1
    res_blk = turn.tool_results[0]
    assert res_blk.call_id == "call_gemini_888"

    assert len(turn.tool_invocations) == 1
    inv = turn.tool_invocations[0]
    assert inv.call_id == "call_gemini_888"
    assert inv.call_block_id == call_blks[0].block_id
    assert inv.result_block_id == res_blk.block_id


def test_context_graph_cross_turn_tool_linking():
    """Verify ContextGraph can look up a tool call made in Turn N from its result in Turn N+1."""
    graph = ContextGraph(session_id="sess_cross_turn")

    # Turn 0: Model produces a tool call in response
    turn0_call_block = ContextBlock(
        block_id="turn0_resp_call_1",
        block_type=BlockType.ASSISTANT_MSG,
        content_hash="hash_call_t0",
        token_count=18,
        content='{"name": "fetch_data", "id": "call_cross_777"}',
        metadata={"type": "tool_call", "name": "fetch_data"},
        call_id="call_cross_777",
    )
    turn0 = CanonicalTurn(
        turn_id="turn_0",
        correlation_id="corr_0",
        session_id="sess_cross_turn",
        turn_index=0,
        timestamp=1000.0,
        provider="anthropic",
        model="claude-3-5-sonnet",
        assistant_blocks=[turn0_call_block],
    )
    graph.add_turn(turn0)

    # Turn 1: Result is sent back in request
    turn1_res_block = ContextBlock(
        block_id="turn1_res_call_1",
        block_type=BlockType.TOOL_RESULT,
        content_hash="hash_res_t1",
        token_count=50,
        content='{"data": [1, 2, 3]}',
        metadata={"name": "fetch_data"},
        call_id="call_cross_777",
    )
    turn1 = CanonicalTurn(
        turn_id="turn_1",
        correlation_id="corr_1",
        session_id="sess_cross_turn",
        turn_index=1,
        timestamp=1001.0,
        provider="anthropic",
        model="claude-3-5-sonnet",
        tool_results=[turn1_res_block],
    )
    graph.add_turn(turn1)

    # Query: from Turn 1 result -> find Turn 0 call
    linked_call = graph.get_tool_call_for_result(turn1_res_block)
    assert linked_call is not None
    orig_turn, orig_call_block = linked_call
    assert orig_turn.turn_index == 0
    assert orig_call_block.block_id == "turn0_resp_call_1"
    assert orig_call_block.call_id == "call_cross_777"

    # Query: from Turn 0 call -> find Turn 1 result
    linked_res = graph.get_tool_result_for_call(turn0_call_block)
    assert linked_res is not None
    res_turn, res_block = linked_res
    assert res_turn.turn_index == 1
    assert res_block.block_id == "turn1_res_call_1"
    assert res_block.call_id == "call_cross_777"
