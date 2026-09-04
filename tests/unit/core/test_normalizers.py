"""Unit tests for Anthropic, OpenAI, and Gemini AST normalizers."""

import pytest

from src.core.ast.normalizers import (
    AnthropicASTNormalizer,
    GeminiASTNormalizer,
    OpenAIASTNormalizer,
    get_normalizer,
)
from src.core.graph.hasher import compute_block_hash
from src.schema.ast import BlockType
from src.schema.wire import Provider

# ---------------------------------------------------------------------------
# Anthropic Tests
# ---------------------------------------------------------------------------


def test_anthropic_normalizer_full_turn():
    normalizer = AnthropicASTNormalizer()

    raw_turn = {
        "correlation_id": "turn-ant-001",
        "session_id": "sess-ant-1",
        "turn_index": 0,
        "timestamp": 1710000000.0,
        "provider": "anthropic",
        "request_payload": {
            "model": "claude-3-5-sonnet-20241022",
            "system": "You are an expert Python engineer.",
            "tools": [
                {
                    "name": "bash",
                    "description": "Run a bash command",
                    "input_schema": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
            "messages": [
                {
                    "role": "user",
                    "content": "Run pytest please",
                },
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_01ABC",
                            "name": "bash",
                            "input": {"command": "pytest"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_01ABC",
                            "content": "5 passed in 0.2s",
                            "is_error": False,
                        }
                    ],
                },
            ],
        },
        "response_payload": {
            "id": "msg_01XYZ",
            "content": [
                {
                    "type": "text",
                    "text": "All 5 tests passed successfully!",
                }
            ],
        },
        "usage": {
            "input_tokens": 150,
            "output_tokens": 30,
            "cache_read_input_tokens": 50,
            "cache_creation_input_tokens": 10,
        },
        "timing": {
            "duration_ms": 420.5,
            "ttft_ms": 65.0,
        },
    }

    turn = normalizer.normalize(raw_turn)

    # Basic metadata
    assert turn.turn_id == "turn-ant-001"
    assert turn.session_id == "sess-ant-1"
    assert turn.turn_index == 0
    assert turn.provider == "anthropic"
    assert turn.model == "claude-3-5-sonnet-20241022"

    # System prompt
    assert len(turn.system_blocks) == 1
    sys_blk = turn.system_blocks[0]
    assert sys_blk.block_type == BlockType.SYSTEM
    assert sys_blk.content == "You are an expert Python engineer."
    assert sys_blk.content_hash == compute_block_hash("You are an expert Python engineer.")
    assert sys_blk.token_count > 0

    # Tool definitions
    assert len(turn.tool_defs) == 1
    tool_blk = turn.tool_defs[0]
    assert tool_blk.block_type == BlockType.TOOL_DEF
    assert tool_blk.metadata["name"] == "bash"

    # Conversation history (user msg + assistant tool_use)
    assert len(turn.conversation_history) == 2
    assert turn.conversation_history[0].block_type == BlockType.USER_MSG
    assert turn.conversation_history[0].content == "Run pytest please"
    assert turn.conversation_history[1].block_type == BlockType.ASSISTANT_MSG
    assert "toolu_01ABC" in turn.conversation_history[1].metadata["tool_use_id"]

    # Tool results
    assert len(turn.tool_results) == 1
    res_blk = turn.tool_results[0]
    assert res_blk.block_type == BlockType.TOOL_RESULT
    assert res_blk.content == "5 passed in 0.2s"
    assert res_blk.metadata["tool_use_id"] == "toolu_01ABC"
    assert res_blk.metadata["is_error"] is False

    # Assistant response blocks
    assert len(turn.assistant_blocks) == 1
    assert turn.assistant_blocks[0].block_type == BlockType.ASSISTANT_MSG
    assert turn.assistant_blocks[0].content == "All 5 tests passed successfully!"

    # Usage & timing
    assert turn.input_tokens == 150
    assert turn.output_tokens == 30
    assert turn.cached_read_tokens == 50
    assert turn.cached_created_tokens == 10
    assert turn.duration_ms == 420.5
    assert turn.ttft_ms == 65.0


def test_anthropic_system_list():
    normalizer = AnthropicASTNormalizer()
    payload = {
        "requestPayload": {
            "model": "claude-3-haiku",
            "system": [
                {"type": "text", "text": "Base instructions."},
                {"type": "text", "text": "Tool guidelines."},
            ],
            "messages": [{"role": "user", "content": "Hi"}],
        }
    }
    turn = normalizer.normalize(payload, turn_index=1)
    assert turn.turn_index == 1
    assert len(turn.system_blocks) == 2
    assert turn.system_blocks[0].content == "Base instructions."
    assert turn.system_blocks[1].content == "Tool guidelines."


# ---------------------------------------------------------------------------
# OpenAI Tests
# ---------------------------------------------------------------------------


def test_openai_normalizer_full_turn():
    normalizer = OpenAIASTNormalizer()

    raw_turn = {
        "correlation_id": "turn-oai-001",
        "session_id": "sess-oai-1",
        "turn_index": 0,
        "request_payload": {
            "model": "gpt-4o",
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "description": "Read file contents",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                }
            ],
            "messages": [
                {"role": "system", "content": "You are a code refactoring agent."},
                {"role": "user", "content": "Read src/main.py"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_123",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path": "src/main.py"}',
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_123",
                    "name": "read_file",
                    "content": "def main(): pass",
                },
            ],
        },
        "response_payload": {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "The file contains a dummy main function.",
                    }
                }
            ]
        },
        "usage": {
            "prompt_tokens": 200,
            "completion_tokens": 15,
            "prompt_tokens_details": {"cached_tokens": 80},
        },
        "timing": {
            "duration_ms": 300.0,
            "ttft_ms": 40.0,
        },
    }

    turn = normalizer.normalize(raw_turn)

    assert turn.provider == "openai"
    assert turn.model == "gpt-4o"

    # System block
    assert len(turn.system_blocks) == 1
    assert turn.system_blocks[0].block_type == BlockType.SYSTEM
    assert turn.system_blocks[0].content == "You are a code refactoring agent."

    # Tool definitions
    assert len(turn.tool_defs) == 1
    assert turn.tool_defs[0].metadata["name"] == "read_file"

    # History (user message + assistant tool_call)
    assert len(turn.conversation_history) == 2
    assert turn.conversation_history[0].block_type == BlockType.USER_MSG
    assert turn.conversation_history[1].block_type == BlockType.ASSISTANT_MSG
    assert turn.conversation_history[1].metadata["tool_use_id"] == "call_123"

    # Tool result
    assert len(turn.tool_results) == 1
    assert turn.tool_results[0].block_type == BlockType.TOOL_RESULT
    assert turn.tool_results[0].content == "def main(): pass"
    assert turn.tool_results[0].metadata["tool_use_id"] == "call_123"

    # Assistant response
    assert len(turn.assistant_blocks) == 1
    assert turn.assistant_blocks[0].content == "The file contains a dummy main function."

    # Usage
    assert turn.input_tokens == 200
    assert turn.output_tokens == 15
    assert turn.cached_read_tokens == 80


# ---------------------------------------------------------------------------
# Gemini Tests
# ---------------------------------------------------------------------------


def test_gemini_normalizer_full_turn():
    normalizer = GeminiASTNormalizer()

    raw_turn = {
        "correlation_id": "turn-gem-001",
        "session_id": "sess-gem-1",
        "turn_index": 0,
        "request_payload": {
            "model": "gemini-1.5-pro",
            "systemInstruction": {
                "parts": [{"text": "You are a search agent."}]
            },
            "tools": [
                {
                    "functionDeclarations": [
                        {
                            "name": "search_db",
                            "description": "Search internal database",
                            "parameters": {
                                "type": "object",
                                "properties": {"query": {"type": "string"}},
                            },
                        }
                    ]
                }
            ],
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": "Search for users"}],
                },
                {
                    "role": "model",
                    "parts": [
                        {
                            "functionCall": {
                                "name": "search_db",
                                "args": {"query": "users"},
                            }
                        }
                    ],
                },
                {
                    "role": "function",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": "search_db",
                                "response": {"results": ["Alice", "Bob"]},
                            }
                        }
                    ],
                },
            ],
        },
        "response_payload": {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [{"text": "Found users: Alice, Bob."}],
                    }
                }
            ]
        },
        "usage": {
            "promptTokenCount": 110,
            "candidatesTokenCount": 12,
            "cachedContentTokenCount": 45,
        },
        "timing": {
            "duration_ms": 250.0,
            "ttft_ms": 30.0,
        },
    }

    turn = normalizer.normalize(raw_turn)

    assert turn.provider == "gemini"
    assert turn.model == "gemini-1.5-pro"

    # System instruction
    assert len(turn.system_blocks) == 1
    assert turn.system_blocks[0].block_type == BlockType.SYSTEM
    assert turn.system_blocks[0].content == "You are a search agent."

    # Tool definitions
    assert len(turn.tool_defs) == 1
    assert turn.tool_defs[0].metadata["name"] == "search_db"

    # Contents (user msg + model call)
    assert len(turn.conversation_history) == 2
    assert turn.conversation_history[0].block_type == BlockType.USER_MSG
    assert turn.conversation_history[0].content == "Search for users"
    assert turn.conversation_history[1].block_type == BlockType.ASSISTANT_MSG
    assert turn.conversation_history[1].metadata["name"] == "search_db"

    # Tool result
    assert len(turn.tool_results) == 1
    assert turn.tool_results[0].block_type == BlockType.TOOL_RESULT
    assert "Alice" in turn.tool_results[0].content
    assert turn.tool_results[0].metadata["name"] == "search_db"

    # Assistant candidate response
    assert len(turn.assistant_blocks) == 1
    assert turn.assistant_blocks[0].content == "Found users: Alice, Bob."

    # Usage
    assert turn.input_tokens == 110
    assert turn.output_tokens == 12
    assert turn.cached_read_tokens == 45


def test_gemini_normalizer_wrapped_cloudcode_payload():
    """Verify GeminiASTNormalizer unwrap logic for Cloud Code / AI Code (agy)."""
    normalizer = GeminiASTNormalizer()

    raw_turn = {
        "correlation_id": "turn-agy-001",
        "session_id": "sess-agy-1",
        "turn_index": 0,
        "request_payload": {
            "model": "gemini-3.1-flash-lite",
            "request": {
                "systemInstruction": {
                    "parts": [{"text": "You are Antigravity CLI."}]
                },
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": "Hello agy!"}],
                    }
                ],
                "sessionId": "-4090532296711904797",
            },
        },
        "response_payload": {
            "response": {
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [{"text": "Hello! How can I help you today?"}],
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 13760,
                    "candidatesTokenCount": 15,
                    "thoughtsTokenCount": 27,
                },
            }
        },
    }

    turn = normalizer.normalize(raw_turn)

    assert turn.provider == "gemini"
    assert turn.model == "gemini-3.1-flash-lite"
    assert len(turn.system_blocks) == 1
    assert turn.system_blocks[0].content == "You are Antigravity CLI."
    assert len(turn.conversation_history) == 1
    assert turn.conversation_history[0].content == "Hello agy!"
    assert len(turn.assistant_blocks) == 1
    assert turn.assistant_blocks[0].content == "Hello! How can I help you today?"
    assert turn.input_tokens == 13760
    assert turn.output_tokens == 15


# ---------------------------------------------------------------------------
# Factory & Hash Verification
# ---------------------------------------------------------------------------


def test_get_normalizer_factory():
    assert isinstance(get_normalizer("anthropic"), AnthropicASTNormalizer)
    assert isinstance(get_normalizer("claude"), AnthropicASTNormalizer)
    assert isinstance(get_normalizer(Provider.ANTHROPIC), AnthropicASTNormalizer)

    assert isinstance(get_normalizer("openai"), OpenAIASTNormalizer)
    assert isinstance(get_normalizer(Provider.OPENAI), OpenAIASTNormalizer)
    assert isinstance(get_normalizer("azure_openai"), OpenAIASTNormalizer)

    assert isinstance(get_normalizer("gemini"), GeminiASTNormalizer)
    assert isinstance(get_normalizer(Provider.GEMINI), GeminiASTNormalizer)

    with pytest.raises(ValueError, match="No AST normalizer implemented"):
        get_normalizer("unknown_provider_xyz")


def test_content_hashing_consistency():
    normalizer = AnthropicASTNormalizer()
    payload = {
        "requestPayload": {
            "system": "Identical instruction text.",
            "messages": [
                {"role": "user", "content": "Identical instruction text."}
            ],
        }
    }
    turn = normalizer.normalize(payload)
    sys_hash = turn.system_blocks[0].content_hash
    user_hash = turn.conversation_history[0].content_hash

    # Hashing identical text produces identical hashes
    assert sys_hash == user_hash
    assert sys_hash == compute_block_hash("Identical instruction text.")
