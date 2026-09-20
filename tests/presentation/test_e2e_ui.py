"""End-to-end integration and regression test suite for ctxins presentation layer."""

from __future__ import annotations

import asyncio
import os
import tempfile
import time

import pytest
from starlette.testclient import TestClient

from src.cli import CorePipelineBridge
from src.core.analyzer.engine import PollutionAnalyzer
from src.core.server.framer import encode_frame
from src.core.server.uds_server import UDSFrameServer
from src.core.store.session_store import SessionStore
from src.presentation.broadcaster import PresentationBroadcaster
from src.presentation.events import UIEventType
from src.presentation.web.server import create_app
from src.schema.wire import WireEnvelope, WireEventType


@pytest.mark.asyncio
async def test_e2e_uds_to_presentation_broadcaster() -> None:
    """Verify full flow: Wire frames sent over UDS are normalized, analyzed, and broadcast as UIEvents."""
    with tempfile.TemporaryDirectory() as tmpdir:
        sock_path = os.path.join(tmpdir, "test_ctxins.sock")

        store = SessionStore()
        broadcaster = PresentationBroadcaster()
        analyzer = PollutionAnalyzer()
        bridge = CorePipelineBridge(store=store, broadcaster=broadcaster, analyzer=analyzer)

        server = UDSFrameServer(socket_path=sock_path, on_turn_callback=bridge.handle_wire_envelope)
        await server.start()

        # Subscribe to broadcaster
        queue = await broadcaster.subscribe()

        try:
            # Connect a raw client to the UDS socket
            reader, writer = await asyncio.open_unix_connection(path=sock_path)

            session_id = "sess_e2e_ui_001"

            # 1. Send REQUEST_INITIATED frame
            env1 = WireEnvelope(
                event_type=WireEventType.REQUEST_INITIATED,
                correlation_id="corr_e2e_1",
                session_id=session_id,
                timestamp=time.time(),
                payload={"model": "claude-3-5-sonnet", "provider": "anthropic"},
            )
            raw_frame1 = encode_frame(env1.to_bytes())
            writer.write(raw_frame1)
            await writer.drain()

            event0 = await asyncio.wait_for(queue.get(), timeout=2.0)
            assert event0.event_type == UIEventType.SESSION_CREATED
            assert event0.session_id == session_id

            event1 = await asyncio.wait_for(queue.get(), timeout=2.0)
            assert event1.event_type == UIEventType.TURN_STARTED
            assert event1.session_id == session_id

            # 2. Send SYSTEM_TELEMETRY frame
            env2 = WireEnvelope(
                event_type=WireEventType.SYSTEM_TELEMETRY,
                correlation_id="corr_e2e_1",
                session_id=session_id,
                timestamp=time.time(),
                payload={"deltaTokens": 25, "ttftMs": 180.0},
            )
            raw_frame2 = encode_frame(env2.to_bytes())
            writer.write(raw_frame2)
            await writer.drain()

            event2 = await asyncio.wait_for(queue.get(), timeout=2.0)
            assert event2.event_type == UIEventType.TURN_STREAMING
            assert event2.payload["deltaTokens"] == 25

            # 3. Send TURN_COMPLETED frame with simulated tool result
            env3 = WireEnvelope(
                event_type=WireEventType.TURN_COMPLETED,
                correlation_id="corr_e2e_1",
                session_id=session_id,
                timestamp=time.time(),
                payload={
                    "provider": "anthropic",
                    "model": "claude-3-5-sonnet",
                    "timing": {
                        "request_dispatched_at": time.time() - 2.0,
                        "first_byte_received_at": time.time() - 1.8,
                        "stream_closed_at": time.time(),
                    },
                    "request": {
                        "system": "You are a coding assistant.",
                        "messages": [
                            {"role": "user", "content": "Run tests"},
                            {
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "tool_use",
                                        "id": "call_1",
                                        "name": "run_cmd",
                                        "input": {"cmd": "pytest"},
                                    }
                                ],
                            },
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "tool_result",
                                        "tool_use_id": "call_1",
                                        "content": "all 100 tests passed",
                                    }
                                ],
                            },
                        ],
                    },
                    "response": {
                        "content": [{"type": "text", "text": "All tests passed successfully!"}],
                        "usage": {
                            "input_tokens": 350,
                            "output_tokens": 30,
                            "cache_read_input_tokens": 200,
                        },
                    },
                },
            )
            raw_frame3 = encode_frame(env3.to_bytes())
            writer.write(raw_frame3)
            await writer.drain()

            event3 = await asyncio.wait_for(queue.get(), timeout=2.0)
            assert event3.event_type == UIEventType.TURN_COMPLETED
            assert event3.session_id == session_id
            assert event3.payload["turnIndex"] == 0
            assert event3.payload["inputTokens"] == 350
            assert event3.payload["cachedReadTokens"] == 200

            event4 = await asyncio.wait_for(queue.get(), timeout=2.0)
            assert event4.event_type == UIEventType.SESSION_SUMMARY_UPDATED
            assert event4.session_id == session_id

            # Verify store was updated
            turns = store.get_session(session_id)
            assert turns is not None
            assert len(turns) == 1
            assert turns[0].turn_index == 0

            writer.close()
            await writer.wait_closed()
        finally:
            await broadcaster.unsubscribe(queue)
            await server.stop()


def test_e2e_web_rest_and_websocket_snapshot() -> None:
    """Verify Web API REST and WebSocket snapshot with loaded session data."""
    store = SessionStore()
    broadcaster = PresentationBroadcaster()

    # Pre-populate store with a turn via normalizer
    from src.core.ast.normalizers import get_normalizer

    normalizer = get_normalizer("anthropic")
    turn = normalizer.normalize(
        {
            "session_id": "sess_web_e2e",
            "correlation_id": "corr_web_e2e",
            "timestamp": time.time(),
            "payload": {
                "model": "claude-3-5-sonnet",
                "provider": "anthropic",
                "request": {"messages": [{"role": "user", "content": "ping"}]},
                "response": {
                    "content": [{"type": "text", "text": "pong"}],
                    "usage": {"input_tokens": 20, "output_tokens": 5},
                },
            },
        },
        turn_index=0,
    )
    store.append_turn(turn)

    app = create_app(store=store, broadcaster=broadcaster)
    client = TestClient(app)

    # 1. Query sessions REST endpoint
    resp = client.get("/api/v1/sessions")
    assert resp.status_code == 200
    sessions_data = resp.json()
    assert len(sessions_data) == 1
    assert sessions_data[0]["sessionId"] == "sess_web_e2e"

    # 2. Query turn details
    turn_resp = client.get("/api/v1/sessions/sess_web_e2e/turns/0")
    assert turn_resp.status_code == 200
    assert turn_resp.json()["turn_index"] == 0

    # 3. Connect via WebSocket and assert SNAPSHOT message
    with client.websocket_connect("/ws/live?session_id=sess_web_e2e") as ws:
        snapshot = ws.receive_json()
        assert snapshot["type"] == "SNAPSHOT"
        assert snapshot["sessionId"] == "sess_web_e2e"
        assert len(snapshot["turns"]) == 1


def test_actionable_context_health_alerts_assets() -> None:
    """Verify app.js and styles.css include actionable Context Health Alerts logic."""
    static_dir = os.path.join(os.path.dirname(__file__), "../../src/presentation/web/static")
    js_file = os.path.join(static_dir, "js/app.js")
    css_file = os.path.join(static_dir, "css/styles.css")

    with open(js_file, "r", encoding="utf-8") as f:
        js_content = f.read()

    with open(css_file, "r", encoding="utf-8") as f:
        css_content = f.read()

    # Verify buttons and handlers in JS
    assert "inspect-culprit-btn" in js_content
    assert "copy-directive-btn" in js_content
    assert "highlight-culprit-row" in js_content
    assert "generateDirective" in js_content
    assert "showToast" in js_content
    assert "fallbackCopyText" in js_content
    assert "CTX001" in js_content
    assert "CTX002" in js_content
    assert "CACHE001" in js_content
    assert "Compact Stale Tool Results" in js_content
    assert "Prune Unused Tool Schemas" in js_content
    assert "Cache Stability" in js_content
    assert "✓ Copied!" in js_content

    # Verify CSS styling
    assert ".violation-actions" in css_content
    assert ".violation-action-btn" in css_content
    assert ".highlight-culprit-row" in css_content
    assert "@keyframes culprit-pulse" in css_content
    assert ".ctxins-toast-container" in css_content
    assert ".ctxins-toast" in css_content
