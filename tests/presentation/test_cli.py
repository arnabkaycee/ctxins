"""Unit tests for ctxins unified CLI entry point and CorePipelineBridge."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from src.cli import (
    DEFAULT_PROXY_PORT,
    DEFAULT_SOCKET_PATH,
    DEFAULT_WEB_HOST,
    CorePipelineBridge,
    build_parser,
    main,
    run_with_harness,
)
from src.core.analyzer.engine import PollutionAnalyzer
from src.core.store.session_store import SessionStore
from src.presentation.broadcaster import PresentationBroadcaster
from src.schema.wire import WireEnvelope, WireEventType


def test_build_parser_defaults() -> None:
    """Verify CLI argument parsing defaults."""
    parser = build_parser()

    # tui subcommand
    args = parser.parse_args(["tui"])
    assert args.subcommand == "tui"
    assert args.socket == DEFAULT_SOCKET_PATH

    # web subcommand
    args = parser.parse_args(["web", "--port", "9090"])
    assert args.subcommand == "web"
    assert args.port == 9090
    assert args.host == DEFAULT_WEB_HOST
    assert args.socket == DEFAULT_SOCKET_PATH

    # live subcommand
    args = parser.parse_args(["live", "--web"])
    assert args.subcommand == "live"
    assert args.ui_mode == "web"

    # run subcommand with trailing args
    args = parser.parse_args(["run", "--web", "--", "claude", "code"])
    assert args.subcommand == "run"
    assert args.ui_mode == "web"
    assert args.command == ["--", "claude", "code"]
    assert args.proxy_port == DEFAULT_PROXY_PORT


def test_main_help_invocation(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify main returns 0 on empty or help args."""
    assert main([]) == 0
    captured = capsys.readouterr()
    assert "usage: ctxins" in captured.out


@pytest.mark.asyncio
async def test_core_pipeline_bridge_turn_lifecycle() -> None:
    """Verify CorePipelineBridge ingests WireEnvelopes and emits UIEvents."""
    store = SessionStore()
    broadcaster = PresentationBroadcaster()
    analyzer = PollutionAnalyzer()
    bridge = CorePipelineBridge(store=store, broadcaster=broadcaster, analyzer=analyzer)

    q = await broadcaster.subscribe()

    # 1. Dispatch REQUEST_INITIATED
    await bridge.handle_wire_envelope(
        WireEnvelope(
            event_type=WireEventType.REQUEST_INITIATED,
            correlation_id="corr_1",
            session_id="sess_cli_test",
            timestamp=time.time(),
            payload={"model": "claude-3-5-sonnet", "provider": "anthropic"},
        )
    )
    ev1 = await q.get()
    assert ev1.event_type.value == "turn_started"
    assert ev1.session_id == "sess_cli_test"

    # 2. Dispatch SYSTEM_TELEMETRY
    await bridge.handle_wire_envelope(
        WireEnvelope(
            event_type=WireEventType.SYSTEM_TELEMETRY,
            correlation_id="corr_1",
            session_id="sess_cli_test",
            timestamp=time.time(),
            payload={"deltaTokens": 50},
        )
    )
    ev2 = await q.get()
    assert ev2.event_type.value == "turn_streaming"

    # 3. Dispatch TURN_COMPLETED
    await bridge.handle_wire_envelope(
        WireEnvelope(
            event_type=WireEventType.TURN_COMPLETED,
            correlation_id="corr_1",
            session_id="sess_cli_test",
            timestamp=time.time(),
            payload={
                "provider": "anthropic",
                "model": "claude-3-5-sonnet",
                "request": {
                    "messages": [{"role": "user", "content": "hello"}],
                },
                "response": {
                    "content": [{"type": "text", "text": "world"}],
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            },
        )
    )

    ev3 = await q.get()
    assert ev3.event_type.value == "turn_completed"
    assert ev3.session_id == "sess_cli_test"
    assert ev3.payload["turnIndex"] == 0

    # Ensure turn was recorded in SessionStore
    turns = store.get_session("sess_cli_test")
    assert turns is not None
    assert len(turns) == 1

    await broadcaster.unsubscribe(q)


def test_run_with_harness_lifecycle():
    with patch("subprocess.Popen") as mock_popen, \
         patch("socket.create_connection") as mock_conn, \
         patch("uvicorn.Server.serve"), \
         patch("src.core.server.uds_server.UDSFrameServer.start"), \
         patch("src.core.server.uds_server.UDSFrameServer.stop"):
        mock_conn.side_effect = [OSError("not listening"), MagicMock()]
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        run_with_harness(command=["agy", "-p", "hi"], ui_mode="web", proxy_port=8080)

        assert mock_popen.call_count == 2
        mitm_call = mock_popen.call_args_list[0]
        assert any("mitmdump" in str(arg) for arg in mitm_call[0][0])
        assert mitm_call[1]["env"]["CTXINS_SOCKET_PATH"] == DEFAULT_SOCKET_PATH

        harness_call = mock_popen.call_args_list[1]
        assert harness_call[0][0] == ["agy", "-p", "hi"]
        env = harness_call[1]["env"]
        assert env["HTTP_PROXY"] == "http://127.0.0.1:8080"
        assert env["http_proxy"] == "http://127.0.0.1:8080"
        assert env["HTTPS_PROXY"] == "http://127.0.0.1:8080"
        assert env["https_proxy"] == "http://127.0.0.1:8080"

