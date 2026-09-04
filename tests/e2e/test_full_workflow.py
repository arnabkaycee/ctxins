"""End-to-end integration and verification suite for ctxins.

Simulates complete multi-turn coding task sessions running through:
MockLLMServer -> CtxinsAddon -> UDSClient -> UDSFrameServer ->
ProviderNormalizer -> ContextGraph -> PollutionAnalyzer -> SessionStore -> JsoncExporter.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import uuid
from typing import Any, Dict, List, Optional

import pytest

from src.core.analyzer.engine import PollutionAnalyzer
from src.core.ast.normalizers import get_normalizer
from src.core.graph.diff import TurnDiffEngine
from src.core.server.uds_server import UDSFrameServer
from src.core.store.jsonc_exporter import JSONC_SCHEMA_URI, JsoncExporter
from src.core.store.session_store import SessionStore
from src.interceptor.addon import CtxinsAddon
from src.interceptor.egress.ring_buffer import BoundedRingBuffer
from src.interceptor.egress.uds_client import UDSClient
from src.schema.ast import CanonicalTurn, ViolationSeverity
from src.schema.wire import WireEnvelope, WireEventType
from tests.mocks.mock_llm_server import MockLLMServer

# ---------------------------------------------------------------------------
# Test Mocks for Mitmproxy Flow Environment
# ---------------------------------------------------------------------------


class MockRequest:
    def __init__(
        self,
        host: str = "api.anthropic.com",
        path: str = "/v1/messages",
        port: int = 443,
        method: str = "POST",
        headers: Optional[Dict[str, str]] = None,
        content: bytes = b"",
    ) -> None:
        self.host = host
        self.pretty_host = host
        self.path = path
        self.port = port
        self.method = method
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.content = content

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


class MockResponse:
    def __init__(
        self,
        status_code: int = 200,
        headers: Optional[Dict[str, str]] = None,
        content: bytes = b"",
        stream: Any = None,
    ) -> None:
        self.status_code = status_code
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.content = content
        self.stream = stream

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


class MockClientConn:
    def __init__(self, peername: tuple[str, int] = ("127.0.0.1", 54321)) -> None:
        self.peername = peername


class MockFlow:
    def __init__(
        self,
        request: MockRequest,
        response: Optional[MockResponse] = None,
        flow_id: str = "flow-test-1",
    ) -> None:
        self.id = flow_id
        self.request = request
        self.response = response
        self.metadata: Dict[str, Any] = {}
        self.client_conn = MockClientConn()
        self.error: Any = None


@pytest.fixture
def temp_env():
    """Create short temp directory for AF_UNIX socket and exported session files."""
    d = tempfile.mkdtemp(prefix="ctx_e2e_", dir="/tmp")
    sock_path = os.path.join(d, f"{uuid.uuid4().hex[:8]}.sock")
    yield {"dir": d, "socket_path": sock_path}
    shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# E2E Test Cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_claude_code_multi_turn_workflow(temp_env: Dict[str, str]):
    """Simulate a 4-turn Claude Code coding session through the complete ctxins stack.

    Verifies:
    1. CtxinsAddon intercepts requests and streams over UDSClient.
    2. UDSFrameServer ingests frames and invokes normalizer, ContextGraph, and PollutionAnalyzer.
    3. SessionStore records all 4 turns in order with accurate token and financial accounting.
    4. CTX-001 (Stale Tool Output Bloat) flags turn 3 due to unreferenced 4,000-token tool result.
    5. Session exports to .jsonc and passes schema validation.
    """
    socket_path = temp_env["socket_path"]
    session_id = "sess_claude_code_e2e"

    session_store = SessionStore()
    analyzer = PollutionAnalyzer()
    processed_turns: List[CanonicalTurn] = []
    completion_events: Dict[int, asyncio.Event] = {i: asyncio.Event() for i in range(4)}

    async def on_wire_envelope(envelope: Any) -> None:
        if not isinstance(envelope, WireEnvelope):
            return
        if envelope.event_type == WireEventType.TURN_COMPLETED:
            provider = envelope.payload.get("provider", "anthropic")
            normalizer = get_normalizer(provider)
            turn = normalizer.normalize(
                envelope.to_dict(),
                turn_index=len(processed_turns),
            )
            session_store.append_turn(turn)
            graph = session_store.get_graph(turn.session_id)
            violations = analyzer.analyze_turn(turn, graph=graph)
            turn.violations = violations

            processed_turns.append(turn)
            idx = turn.turn_index
            if idx in completion_events:
                completion_events[idx].set()

    # 1. Start Core UDS Server
    server = UDSFrameServer(socket_path=socket_path, on_turn_callback=on_wire_envelope)
    await server.start()

    # 2. Start Interceptor Addon with UDSClient
    ring_buffer = BoundedRingBuffer(capacity=100)
    uds_client = UDSClient(
        socket_path=socket_path,
        buffer=ring_buffer,
        connect_retry_interval=0.05,
        reconnect_backoff=0.05,
        poll_interval=0.005,
    )
    uds_client.start()

    addon = CtxinsAddon(
        uds_client=uds_client,
        ring_buffer=ring_buffer,
        default_session_id=session_id,
    )

    try:
        # Generate a large 16KB (~4,000 tokens) SQL schema result for tool output
        large_schema_sql = (
            "CREATE TABLE users (id SERIAL PRIMARY KEY, email VARCHAR(255) NOT NULL, created_at TIMESTAMP);\n"
            "CREATE TABLE orders (id SERIAL PRIMARY KEY, user_id INT REFERENCES users(id), amount NUMERIC(10,2));\n"
            "CREATE TABLE products (id SERIAL PRIMARY KEY, name VARCHAR(255), price NUMERIC(10,2), stock INT);\n"
        ) * 45  # ~16,000 characters = ~4,000 estimated tokens (len // 4)

        base_tool_result = {
            "type": "tool_result",
            "tool_use_id": "toolu_read_schema_01",
            "content": large_schema_sql,
        }

        # -------------------------------------------------------------------
        # Turn 0: User provides initial tool result from reading schema.sql
        # -------------------------------------------------------------------
        msgs_0: List[Dict[str, Any]] = [
            {"role": "user", "content": "Please inspect schema.sql."},
            {"role": "user", "content": [base_tool_result]},
        ]
        req_payload_0: Dict[str, Any] = {
            "model": "claude-3-5-sonnet-20241022",
            "messages": msgs_0,
            "stream": True,
        }
        flow_0 = MockFlow(
            request=MockRequest(
                host="api.anthropic.com",
                path="/v1/messages",
                headers={
                    "x-api-key": "sk-ant-secret",
                    "x-correlation-id": "corr_turn_0",
                },
                content=json.dumps(req_payload_0).encode("utf-8"),
            ),
            flow_id="flow_0",
        )
        addon.requestheaders(flow_0)
        addon.request(flow_0)

        resp_0 = MockResponse(
            status_code=200,
            headers={"content-type": "text/event-stream; charset=utf-8"},
        )
        flow_0.response = resp_0
        addon.responseheaders(flow_0)

        chunks_0 = [
            b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_0","type":"message","role":"assistant","usage":{"input_tokens":4050,"cache_creation_input_tokens":0,"cache_read_input_tokens":0}}}\n\n',
            b'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n',
            b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"I have reviewed schema.sql. Tables include users, orders, and products."}}\n\n',
            b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
            b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":25}}\n\n',
            b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]
        list(flow_0.response.stream(chunks_0))
        addon.response(flow_0)

        await asyncio.wait_for(completion_events[0].wait(), timeout=3.0)

        # -------------------------------------------------------------------
        # Turn 1: User asks to add index (schema still in history, not referenced)
        # -------------------------------------------------------------------
        msgs_1: List[Dict[str, Any]] = [
            *msgs_0,
            {"role": "assistant", "content": "I have reviewed schema.sql. Tables include users, orders, and products."},
            {"role": "user", "content": "Add an index on users(email) to speed up queries."},
        ]
        req_payload_1: Dict[str, Any] = {
            "model": "claude-3-5-sonnet-20241022",
            "messages": msgs_1,
            "stream": True,
        }
        flow_1 = MockFlow(
            request=MockRequest(
                host="api.anthropic.com",
                path="/v1/messages",
                headers={
                    "x-api-key": "sk-ant-secret",
                    "x-correlation-id": "corr_turn_1",
                },
                content=json.dumps(req_payload_1).encode("utf-8"),
            ),
            flow_id="flow_1",
        )
        addon.requestheaders(flow_1)
        addon.request(flow_1)

        resp_1 = MockResponse(
            status_code=200,
            headers={"content-type": "text/event-stream; charset=utf-8"},
        )
        flow_1.response = resp_1
        addon.responseheaders(flow_1)

        chunks_1 = [
            b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1","type":"message","role":"assistant","usage":{"input_tokens":4100,"cache_creation_input_tokens":0,"cache_read_input_tokens":0}}}\n\n',
            b'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n',
            b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"I will create an index on users(email)."}}\n\n',
            b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
            b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":30}}\n\n',
            b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]
        list(flow_1.response.stream(chunks_1))
        addon.response(flow_1)

        await asyncio.wait_for(completion_events[1].wait(), timeout=3.0)

        # -------------------------------------------------------------------
        # Turn 2: User asks to run test suite (schema still lingering)
        # -------------------------------------------------------------------
        msgs_2: List[Dict[str, Any]] = [
            *msgs_1,
            {"role": "assistant", "content": "I will create an index on users(email)."},
            {"role": "user", "content": "Now run pytest to check if migrations pass."},
        ]
        req_payload_2: Dict[str, Any] = {
            "model": "claude-3-5-sonnet-20241022",
            "messages": msgs_2,
            "stream": True,
        }
        flow_2 = MockFlow(
            request=MockRequest(
                host="api.anthropic.com",
                path="/v1/messages",
                headers={
                    "x-api-key": "sk-ant-secret",
                    "x-correlation-id": "corr_turn_2",
                },
                content=json.dumps(req_payload_2).encode("utf-8"),
            ),
            flow_id="flow_2",
        )
        addon.requestheaders(flow_2)
        addon.request(flow_2)

        resp_2 = MockResponse(
            status_code=200,
            headers={"content-type": "text/event-stream; charset=utf-8"},
        )
        flow_2.response = resp_2
        addon.responseheaders(flow_2)

        chunks_2 = [
            b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_2","type":"message","role":"assistant","usage":{"input_tokens":4150,"cache_creation_input_tokens":0,"cache_read_input_tokens":0}}}\n\n',
            b'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n',
            b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Running pytest... all 12 tests passed successfully."}}\n\n',
            b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
            b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":25}}\n\n',
            b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]
        list(flow_2.response.stream(chunks_2))
        addon.response(flow_2)

        await asyncio.wait_for(completion_events[2].wait(), timeout=3.0)

        # -------------------------------------------------------------------
        # Turn 3: User asks for release notes (schema unreferenced for 3 turns!)
        # -------------------------------------------------------------------
        msgs_3: List[Dict[str, Any]] = [
            *msgs_2,
            {"role": "assistant", "content": "Running pytest... all 12 tests passed successfully."},
            {"role": "user", "content": "Please generate markdown release notes."},
        ]
        req_payload_3: Dict[str, Any] = {
            "model": "claude-3-5-sonnet-20241022",
            "messages": msgs_3,
            "stream": True,
        }
        flow_3 = MockFlow(
            request=MockRequest(
                host="api.anthropic.com",
                path="/v1/messages",
                headers={
                    "x-api-key": "sk-ant-secret",
                    "x-correlation-id": "corr_turn_3",
                },
                content=json.dumps(req_payload_3).encode("utf-8"),
            ),
            flow_id="flow_3",
        )
        addon.requestheaders(flow_3)
        addon.request(flow_3)

        resp_3 = MockResponse(
            status_code=200,
            headers={"content-type": "text/event-stream; charset=utf-8"},
        )
        flow_3.response = resp_3
        addon.responseheaders(flow_3)

        chunks_3 = [
            b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_3","type":"message","role":"assistant","usage":{"input_tokens":4200,"cache_creation_input_tokens":0,"cache_read_input_tokens":0}}}\n\n',
            b'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n',
            b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"# Release Notes\\n- Added users(email) index."}}\n\n',
            b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
            b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":40}}\n\n',
            b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]
        list(flow_3.response.stream(chunks_3))
        addon.response(flow_3)

        await asyncio.wait_for(completion_events[3].wait(), timeout=3.0)

        # -------------------------------------------------------------------
        # Verifications
        # -------------------------------------------------------------------
        stored_turns = session_store.get_session(session_id)
        assert stored_turns is not None
        assert len(stored_turns) == 4

        # Verify sequential ordering and correlation IDs
        for i in range(4):
            assert stored_turns[i].turn_index == i
            assert stored_turns[i].correlation_id == f"corr_turn_{i}"
            assert stored_turns[i].provider == "anthropic"
            assert stored_turns[i].model == "claude-3-5-sonnet-20241022"

        # Token accounting
        assert stored_turns[0].output_tokens == 25
        assert stored_turns[1].input_tokens >= 4000
        assert stored_turns[1].output_tokens == 30
        assert stored_turns[3].input_tokens >= 4000

        # Rule violation detection: CTX-001 flagged on Turn 3
        turn_3 = stored_turns[3]
        rule_ids = [v.rule_id for v in turn_3.violations]
        assert "CTX-001" in rule_ids

        ctx001 = next(v for v in turn_3.violations if v.rule_id == "CTX-001")
        assert ctx001.severity == ViolationSeverity.WARN
        assert ctx001.estimated_waste_usd > 0.0
        assert turn_3.wasted_cost_usd > 0.0
        assert turn_3.turn_cost_usd > 0.0

        # Session export to .jsonc and schema validation
        export_file = os.path.join(temp_env["dir"], "session.jsonc")
        jsonc_str = JsoncExporter.export_from_store(
            store=session_store,
            session_id=session_id,
            output_path=export_file,
            include_comments=True,
        )

        assert os.path.exists(export_file)
        assert "// 0 = pristine, 100 = critical bloat" in jsonc_str

        # Parse JSONC with comments stripped
        parsed = JsoncExporter.parse_jsonc(jsonc_str)

        # Schema and summary assertions
        assert parsed["$schema"] == JSONC_SCHEMA_URI
        assert parsed["sessionId"] == session_id
        assert parsed["model"]["provider"] == "anthropic"
        assert parsed["model"]["name"] == "claude-3-5-sonnet-20241022"

        summary = parsed["summary"]
        assert summary["totalTurns"] == 4
        assert summary["activeViolationsCount"] >= 1
        assert summary["pollutionScore"] > 0
        assert summary["estimatedCostUSD"] > 0.0
        assert summary["potentialSavingsUSD"] > 0.0

        # Turns schema assertions
        turns = parsed["turns"]
        assert len(turns) == 4
        assert turns[3]["turnIndex"] == 3
        assert turns[3]["tokens"]["toolResults"] >= 3000
        assert len(turns[3]["violations"]) >= 1
        assert turns[3]["violations"][0]["ruleId"] == "CTX-001"
        assert turns[3]["cost"]["wastedCostUSD"] > 0.0

    finally:
        uds_client.stop()
        await server.stop()


@pytest.mark.asyncio
async def test_e2e_openai_streaming_workflow(temp_env: Dict[str, str]):
    """Simulate multi-turn OpenAI streaming tool call workflow through ctxins stack."""
    socket_path = temp_env["socket_path"]
    session_id = "sess_openai_e2e"

    session_store = SessionStore()
    analyzer = PollutionAnalyzer()
    completion_event = asyncio.Event()

    async def on_turn(envelope: Any) -> None:
        if isinstance(envelope, WireEnvelope) and envelope.event_type == WireEventType.TURN_COMPLETED:
            normalizer = get_normalizer(envelope.payload.get("provider", "openai"))
            turn = normalizer.normalize(envelope.to_dict(), turn_index=0)
            session_store.append_turn(turn)
            analyzer.analyze_turn(turn, graph=session_store.get_graph(turn.session_id))
            completion_event.set()

    server = UDSFrameServer(socket_path=socket_path, on_turn_callback=on_turn)
    await server.start()

    ring_buffer = BoundedRingBuffer(capacity=50)
    uds_client = UDSClient(
        socket_path=socket_path,
        buffer=ring_buffer,
        connect_retry_interval=0.05,
        reconnect_backoff=0.05,
        poll_interval=0.005,
    )
    uds_client.start()

    addon = CtxinsAddon(
        uds_client=uds_client,
        ring_buffer=ring_buffer,
        default_session_id=session_id,
    )

    try:
        req_payload = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Fetch top news"}],
            "stream": True,
        }
        flow = MockFlow(
            request=MockRequest(
                host="api.openai.com",
                path="/v1/chat/completions",
                headers={
                    "authorization": "Bearer sk-proj-12345",
                    "x-request-id": "req-oai-e2e-1",
                },
                content=json.dumps(req_payload).encode("utf-8"),
            ),
            flow_id="flow_oai_1",
        )
        addon.requestheaders(flow)
        addon.request(flow)

        resp = MockResponse(
            status_code=200,
            headers={"content-type": "text/event-stream; charset=utf-8"},
        )
        flow.response = resp
        addon.responseheaders(flow)

        chunks = [
            b'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","model":"gpt-4o","choices":[{"index":0,"delta":{"role":"assistant","content":null,"tool_calls":[{"index":0,"id":"call_fetch_1","type":"function","function":{"name":"fetch_rss","arguments":""}}]},"finish_reason":null}]}\n\n',
            b'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","model":"gpt-4o","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"category\\": \\"tech\\"}"}}]},"finish_reason":null}]}\n\n',
            b'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","model":"gpt-4o","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":35,"completion_tokens":20,"total_tokens":55}}\n\n',
            b'data: [DONE]\n\n',
        ]
        list(flow.response.stream(chunks))
        addon.response(flow)

        await asyncio.wait_for(completion_event.wait(), timeout=3.0)

        turns = session_store.get_session(session_id)
        assert turns is not None
        assert len(turns) == 1
        t0 = turns[0]
        assert t0.model == "gpt-4o"
        assert t0.provider == "openai"
        assert t0.input_tokens == 35
        assert t0.output_tokens == 20
        assert t0.turn_cost_usd > 0.0

        # Export and check jsonc
        jsonc_data = JsoncExporter.export_from_store(session_store, session_id)
        parsed = JsoncExporter.parse_jsonc(jsonc_data)
        assert parsed["summary"]["totalInputTokens"] == 35
        assert parsed["summary"]["totalOutputTokens"] == 20
        assert parsed["model"]["name"] == "gpt-4o"

    finally:
        uds_client.stop()
        await server.stop()


@pytest.mark.asyncio
async def test_e2e_regression_diff_analysis(temp_env: Dict[str, str]):
    """Verify context difference calculation and regression detection between baseline and polluted sessions."""
    baseline_store = SessionStore()
    polluted_store = SessionStore()
    analyzer = PollutionAnalyzer()

    normalizer = get_normalizer("anthropic")

    # Construct clean baseline turns (short tool outputs, no stale tool pollution)
    for i in range(4):
        wire_data = {
            "session_id": "sess_baseline",
            "correlation_id": f"corr_b_{i}",
            "turn_index": i,
            "provider": "anthropic",
            "model": "claude-3-5-sonnet-20241022",
            "request_payload": {
                "model": "claude-3-5-sonnet-20241022",
                "messages": [{"role": "user", "content": f"Clean question {i}"}],
            },
            "response_payload": {
                "content": [{"type": "text", "text": f"Clean response {i}"}],
                "stop_reason": "end_turn",
            },
            "usage": {"input_tokens": 100 + i * 20, "output_tokens": 50},
        }
        turn = normalizer.normalize(wire_data, turn_index=i)
        baseline_store.append_turn(turn)
        analyzer.analyze_turn(turn, graph=baseline_store.get_graph("sess_baseline"))

    # Construct polluted turns (massive unreferenced tool result lingering across all 4 turns)
    huge_text = "ERROR 404: TABLE NOT FOUND\n" * 600  # ~16,200 chars = ~4,050 tokens
    for i in range(4):
        msgs = [
            {"role": "user", "content": "Question"},
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tool_err_1", "content": huge_text}
                ],
            },
        ]
        wire_data = {
            "session_id": "sess_polluted",
            "correlation_id": f"corr_p_{i}",
            "turn_index": i,
            "provider": "anthropic",
            "model": "claude-3-5-sonnet-20241022",
            "request_payload": {
                "model": "claude-3-5-sonnet-20241022",
                "messages": msgs,
            },
            "response_payload": {
                "content": [{"type": "text", "text": f"Polluted response {i}"}],
                "stop_reason": "end_turn",
            },
            "usage": {"input_tokens": 4200, "output_tokens": 50},
        }
        turn = normalizer.normalize(wire_data, turn_index=i)
        polluted_store.append_turn(turn)
        analyzer.analyze_turn(turn, graph=polluted_store.get_graph("sess_polluted"))

    # Turn diff analysis on successive turns
    base_turns = baseline_store.get_session("sess_baseline")
    poll_turns = polluted_store.get_session("sess_polluted")
    assert base_turns is not None and poll_turns is not None

    base_delta_3 = TurnDiffEngine.compute_delta(base_turns[2], base_turns[3])
    poll_delta_3 = TurnDiffEngine.compute_delta(poll_turns[2], poll_turns[3])

    # Polluted turn delta shows persistent large tool result blocks
    assert len(poll_delta_3.persisted_block_ids) > len(base_delta_3.persisted_block_ids)

    # Export both sessions to JSONC
    base_jsonc = JsoncExporter.export_from_store(baseline_store, "sess_baseline")
    poll_jsonc = JsoncExporter.export_from_store(polluted_store, "sess_polluted")

    base_data = JsoncExporter.parse_jsonc(base_jsonc)
    poll_data = JsoncExporter.parse_jsonc(poll_jsonc)

    # Regression assertions:
    # 1. Baseline has 0 violations and 0 pollution score
    assert base_data["summary"]["activeViolationsCount"] == 0
    assert base_data["summary"]["pollutionScore"] == 0

    # 2. Polluted session has active violations and non-zero pollution score
    assert poll_data["summary"]["activeViolationsCount"] >= 1
    assert poll_data["summary"]["pollutionScore"] > base_data["summary"]["pollutionScore"]
    assert poll_data["summary"]["potentialSavingsUSD"] > base_data["summary"]["potentialSavingsUSD"]

    # Exit code logic: regression detected => 1
    is_regression = (
        poll_data["summary"]["pollutionScore"] > base_data["summary"]["pollutionScore"]
        or poll_data["summary"]["activeViolationsCount"] > base_data["summary"]["activeViolationsCount"]
    )
    exit_code = 1 if is_regression else 0
    assert exit_code == 1, "Expected regression detection exit code 1"


@pytest.mark.asyncio
async def test_e2e_with_mock_llm_server(temp_env: Dict[str, str]):
    """Verify end-to-end integration when streaming against in-process MockLLMServer."""
    socket_path = temp_env["socket_path"]
    session_id = "sess_mock_server_e2e"

    session_store = SessionStore()
    received_event = asyncio.Event()

    async def on_turn(envelope: Any) -> None:
        if isinstance(envelope, WireEnvelope) and envelope.event_type == WireEventType.TURN_COMPLETED:
            normalizer = get_normalizer(envelope.payload.get("provider", "anthropic"))
            turn = normalizer.normalize(envelope.to_dict(), turn_index=0)
            session_store.append_turn(turn)
            received_event.set()

    server = UDSFrameServer(socket_path=socket_path, on_turn_callback=on_turn)
    await server.start()

    ring_buffer = BoundedRingBuffer(capacity=50)
    uds_client = UDSClient(
        socket_path=socket_path,
        buffer=ring_buffer,
        connect_retry_interval=0.05,
        reconnect_backoff=0.05,
        poll_interval=0.005,
    )
    uds_client.start()

    addon = CtxinsAddon(
        uds_client=uds_client,
        ring_buffer=ring_buffer,
        default_session_id=session_id,
    )

    try:
        with MockLLMServer() as mock_server:
            # Build mock streaming response
            mock_resp_config = mock_server.build_anthropic_stream(
                text="Integration test passed from MockLLMServer",
                input_tokens=45,
                output_tokens=12,
            )
            mock_server.enqueue_response(mock_resp_config)

            # Client initiates flow
            req = MockRequest(
                host="api.anthropic.com",
                path="/v1/messages",
                headers={"x-api-key": "secret", "x-correlation-id": "corr_mock_1"},
                content=json.dumps({"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "Hi"}], "stream": True}).encode("utf-8"),
            )
            flow = MockFlow(request=req)
            addon.requestheaders(flow)
            addon.request(flow)

            # Response headers from mock server
            flow.response = MockResponse(
                status_code=mock_resp_config.status_code,
                headers=mock_resp_config.headers,
            )
            addon.responseheaders(flow)

            # Stream chunks through passthrough
            assert callable(flow.response.stream)
            list(flow.response.stream(mock_resp_config.chunks))
            addon.response(flow)

            await asyncio.wait_for(received_event.wait(), timeout=3.0)

            turns = session_store.get_session(session_id)
            assert turns is not None
            assert len(turns) == 1
            assert turns[0].correlation_id == "corr_mock_1"
            assert turns[0].output_tokens == 12

    finally:
        uds_client.stop()
        await server.stop()
