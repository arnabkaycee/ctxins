"""Unit tests for ctxins unified CLI entry point and CorePipelineBridge."""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any
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


@pytest.fixture(autouse=True)
def mock_agent_scanner():
    """Isolate CLI subprocess assertions from proactive background agent process scanner."""
    with patch("src.cli.CorePipelineBridge.scan_and_register_agents", return_value=[]), \
         patch("src.cli._agent_scanner_loop", return_value=None):
        yield


def test_build_parser_defaults() -> None:
    """Verify CLI argument parsing defaults."""
    parser = build_parser()

    # tui subcommand
    args = parser.parse_args(["tui", "--proxy-port", "8085"])
    assert args.subcommand == "tui"
    assert args.socket == DEFAULT_SOCKET_PATH
    assert args.proxy_port == 8085

    # web subcommand
    args = parser.parse_args(["web", "--port", "9090", "--proxy-port", "8086"])
    assert args.subcommand == "web"
    assert args.port == 9090
    assert args.host == DEFAULT_WEB_HOST
    assert args.socket == DEFAULT_SOCKET_PATH
    assert args.proxy_port == 8086

    # live subcommand
    args = parser.parse_args(["live", "--web", "--proxy-port", "8087"])
    assert args.subcommand == "live"
    assert args.ui_mode == "web"
    assert args.proxy_port == 8087

    # run subcommand with trailing args
    args = parser.parse_args(["run", "--web", "--", "claude", "code"])
    assert args.subcommand == "run"
    assert args.ui_mode == "web"
    assert args.command == ["--", "claude", "code"]
    assert args.proxy_port == DEFAULT_PROXY_PORT


def test_find_available_port() -> None:
    from src.cli import find_available_port

    port = find_available_port(8080)
    assert isinstance(port, int)
    assert port >= 8080


def test_env_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["env"]) == 0
    captured = capsys.readouterr()
    assert 'export HTTP_PROXY="http://127.0.0.1:8080"' in captured.out
    assert 'export HTTPS_PROXY="http://127.0.0.1:8080"' in captured.out


def test_env_json_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    import json

    assert main(["env", "--json", "--proxy-port", "9999"]) == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["HTTP_PROXY"] == "http://127.0.0.1:9999"


def test_main_default_to_tui(monkeypatch: pytest.MonkeyPatch) -> None:
    called = []

    def mock_run_tui(**kwargs: Any) -> None:
        called.append(kwargs)

    monkeypatch.setattr("src.cli.run_tui", mock_run_tui)
    assert main([]) == 0
    assert len(called) == 1


def test_main_help_invocation(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify main --help prints usage."""
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
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
    ev0 = await q.get()
    assert ev0.event_type.value == "session_created"
    assert ev0.session_id == "sess_cli_test"

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
    assert ev3.payload["turn_index"] == 0

    ev4 = await q.get()
    assert ev4.event_type.value == "session_summary_updated"
    assert ev4.session_id == "sess_cli_test"

    # Ensure turn was recorded in SessionStore
    turns = store.get_session("sess_cli_test")
    assert turns is not None
    assert len(turns) == 1

    await broadcaster.unsubscribe(q)


def test_run_with_harness_lifecycle():
    with patch("subprocess.Popen") as mock_popen, \
         patch("socket.create_connection") as mock_conn, \
         patch("src.cli.find_available_port", side_effect=lambda p, **kw: p), \
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


def test_run_web_lifecycle():
    with patch("subprocess.Popen") as mock_popen, \
         patch("socket.create_connection") as mock_conn, \
         patch("uvicorn.Server.serve"), \
         patch("src.core.server.uds_server.UDSFrameServer.start"), \
         patch("src.core.server.uds_server.UDSFrameServer.stop"):
        mock_conn.side_effect = [OSError("not listening"), MagicMock()]
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        from src.cli import run_web

        run_web(port=8484, proxy_port=8080)

        assert mock_popen.call_count == 1
        mitm_call = mock_popen.call_args_list[0]
        assert any("mitmdump" in str(arg) for arg in mitm_call[0][0])
        assert mock_proc.terminate.called


def test_run_with_harness_with_target_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TMUX", raising=False)
    with patch("subprocess.Popen") as mock_popen, \
         patch("socket.create_connection") as mock_conn, \
         patch("src.core.server.uds_server.UDSFrameServer.start"), \
         patch("src.core.server.uds_server.UDSFrameServer.stop"):
        mock_conn.side_effect = [OSError("not listening"), MagicMock()]
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        run_with_harness(
            command=["test-agent"],
            proxy_port=8080,
            target_port=8000,
            no_web=True,
        )

        assert mock_popen.call_count == 2
        harness_call = mock_popen.call_args_list[1]
        env = harness_call[1]["env"]
        assert env["CTXINS_TARGET"] == "http://127.0.0.1:8000"


def test_run_with_harness_tmux_split(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TMUX", "/tmp/tmux-501/default,123,0")
    with patch("subprocess.Popen") as mock_popen, \
         patch("subprocess.run") as mock_run, \
         patch("socket.create_connection") as mock_conn, \
         patch("src.core.server.uds_server.UDSFrameServer.start"), \
         patch("src.core.server.uds_server.UDSFrameServer.stop"):
        mock_conn.side_effect = [OSError("not listening"), MagicMock()]
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        run_with_harness(
            command=["test-agent"],
            proxy_port=8080,
            no_web=True,
        )

        assert mock_run.called
        tmux_call = mock_run.call_args[0][0]
        assert tmux_call[0] == "tmux"
        assert tmux_call[1] == "split-window"


@pytest.mark.asyncio
async def test_shutdown_uvicorn_graceful() -> None:
    """Verify _shutdown_uvicorn sets should_exit and completes task without errors."""
    from src.cli import _shutdown_uvicorn

    mock_server = MagicMock()
    mock_server.should_exit = False

    async def _dummy_serve() -> None:
        while not mock_server.should_exit:
            await asyncio.sleep(0.01)

    task = asyncio.create_task(_dummy_serve())
    await _shutdown_uvicorn(mock_server, task)
    assert mock_server.should_exit is True
    assert task.done()


@pytest.mark.asyncio
async def test_shutdown_uvicorn_handles_none() -> None:
    """Verify _shutdown_uvicorn handles None parameters safely."""
    from src.cli import _shutdown_uvicorn

    await _shutdown_uvicorn(None, None)


def test_cli_logging_flags_parsing() -> None:
    """Verify CLI parser properly parses --debug, --log-level, and --log-file."""
    parser = build_parser()

    # Subcommand level
    args = parser.parse_args(["tui", "--debug", "--log-level", "DEBUG", "--log-file", "/tmp/test.log"])
    assert args.debug is True
    assert args.log_level == "DEBUG"
    assert args.log_file == "/tmp/test.log"

    # Root level
    args2 = parser.parse_args(["--debug", "web", "--log-file", "/tmp/web.log"])
    assert args2.log_file == "/tmp/web.log"


def test_main_configures_logging(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Verify main() sets os.environ and configures logging from CLI arguments."""
    called_tui = []
    log_file = tmp_path / "test_main.log"

    monkeypatch.setattr("src.cli.run_tui", lambda **kwargs: called_tui.append(kwargs))
    monkeypatch.delenv("CTXINS_DEBUG", raising=False)
    monkeypatch.delenv("CTXINS_LOG_LEVEL", raising=False)
    monkeypatch.delenv("CTXINS_LOG_FILE", raising=False)

    res = main(["tui", "--debug", "--log-file", str(log_file)])
    assert res == 0
    assert len(called_tui) == 1
    assert os.environ.get("CTXINS_DEBUG") == "1"
    assert os.environ.get("CTXINS_LOG_FILE") == str(log_file)


def test_spawn_mitmproxy_forwards_log_env(tmp_path: Any) -> None:
    """Verify spawn_mitmproxy forwards log level and file into mitm_env."""
    from src.cli import spawn_mitmproxy

    log_file = str(tmp_path / "mitm.log")
    with patch("socket.create_connection", side_effect=OSError("not listening")), \
         patch("subprocess.Popen") as mock_popen, \
         patch("shutil.which", return_value="/usr/local/bin/mitmdump"):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_popen.return_value = mock_proc

        spawn_mitmproxy(
            proxy_port=8080,
            log_level="DEBUG",
            log_file=log_file,
        )

        assert mock_popen.called
        call_kwargs = mock_popen.call_args[1]
        env = call_kwargs["env"]
        assert env.get("CTXINS_LOG_LEVEL") == "DEBUG"
        assert env.get("CTXINS_LOG_FILE") == log_file





