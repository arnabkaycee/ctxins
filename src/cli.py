"""ctxins unified command-line entry points and runner."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.analyzer.engine import PollutionAnalyzer
from src.core.analyzer.scorer import PollutionScorer
from src.core.ast.normalizers import get_normalizer
from src.core.server.uds_server import UDSFrameServer
from src.core.store.session_store import SessionStore
from src.presentation.broadcaster import PresentationBroadcaster
from src.presentation.events import UIEvent, UIEventType
from src.presentation.tui.app import CtxinsTUIApp
from src.presentation.tui.state import TUIState
from src.presentation.web.server import create_app
from src.schema.wire import WireEnvelope, WireEventType

logger = logging.getLogger("ctxins.cli")

DEFAULT_SOCKET_PATH = "/tmp/ctxins.sock"
DEFAULT_WEB_PORT = 8484
DEFAULT_WEB_HOST = "127.0.0.1"
DEFAULT_PROXY_PORT = 8080


class CorePipelineBridge:
    """Bridges Core UDS telemetry ingestion to PresentationBroadcaster and SessionStore."""

    def __init__(
        self,
        store: Optional[SessionStore] = None,
        broadcaster: Optional[PresentationBroadcaster] = None,
        analyzer: Optional[PollutionAnalyzer] = None,
    ) -> None:
        self.store = store or SessionStore()
        self.broadcaster = broadcaster or PresentationBroadcaster()
        self.analyzer = analyzer or PollutionAnalyzer()

    async def handle_wire_envelope(self, data: WireEnvelope | Dict[str, Any]) -> None:
        """Process incoming wire envelope, update SessionStore, and broadcast UIEvents."""
        if isinstance(data, dict):
            try:
                envelope = WireEnvelope.from_dict(data)
            except Exception:
                return
        else:
            envelope = data

        session_id = envelope.session_id

        if envelope.event_type == WireEventType.REQUEST_INITIATED:
            existing_turns = self.store.get_session(session_id) or []
            if len(existing_turns) == 0:
                self.broadcaster.publish_nowait(
                    UIEvent(
                        event_type=UIEventType.SESSION_CREATED,
                        session_id=session_id,
                        timestamp=envelope.timestamp,
                        payload={
                            "sessionId": session_id,
                            "model": envelope.payload.get("model", "unknown"),
                            "provider": envelope.payload.get("provider", "unknown"),
                        },
                    )
                )

            self.broadcaster.publish_nowait(
                UIEvent(
                    event_type=UIEventType.TURN_STARTED,
                    session_id=session_id,
                    timestamp=envelope.timestamp,
                    payload=envelope.payload,
                )
            )

        elif envelope.event_type == WireEventType.SYSTEM_TELEMETRY:
            self.broadcaster.publish_nowait(
                UIEvent(
                    event_type=UIEventType.TURN_STREAMING,
                    session_id=session_id,
                    timestamp=envelope.timestamp,
                    payload=envelope.payload,
                )
            )

        elif envelope.event_type == WireEventType.TURN_COMPLETED:
            provider = envelope.payload.get("provider", "anthropic")
            try:
                normalizer = get_normalizer(provider)
            except Exception:
                normalizer = get_normalizer("anthropic")

            existing_turns = self.store.get_session(session_id) or []
            turn_index = len(existing_turns)

            try:
                turn = normalizer.normalize(envelope.to_dict(), turn_index=turn_index)
                self.store.append_turn(turn)
                violations = self.analyzer.analyze_turn(turn, graph=self.store.get_graph(session_id))
            except Exception as e:
                logger.error("Error normalizing or analyzing turn: %s", e)
                return

            all_turns = self.store.get_session(session_id) or []
            summary = PollutionScorer.calculate_summary(all_turns)
            turn_dict = turn.to_dict()
            turn_dict["all_blocks"] = [b.to_dict() for b in turn.all_blocks]

            token_breakdown = {
                "system": sum(b.token_count for b in turn.system_blocks),
                "tools": sum(b.token_count for b in turn.tool_defs),
                "history": sum(b.token_count for b in turn.conversation_history),
                "toolResults": sum(b.token_count for b in turn.tool_results),
                "assistant": sum(b.token_count for b in turn.assistant_blocks),
                "cache": turn.cached_read_tokens,
            }

            turn_payload = {
                # Canonical attributes
                "turn_index": turn.turn_index,
                "turn_id": turn.turn_id,
                "correlation_id": turn.correlation_id,
                "model": turn.model,
                "provider": turn.provider,
                "timestamp": turn.timestamp,
                "duration_ms": turn.duration_ms,
                "ttft_ms": turn.ttft_ms,
                "input_tokens": turn.input_tokens,
                "output_tokens": turn.output_tokens,
                "cached_read_tokens": turn.cached_read_tokens,
                "cached_created_tokens": turn.cached_created_tokens,
                "turn_cost_usd": turn.turn_cost_usd,
                "wasted_cost_usd": turn.wasted_cost_usd,
                "system_blocks": [b.to_dict() for b in turn.system_blocks],
                "tool_defs": [b.to_dict() for b in turn.tool_defs],
                "conversation_history": [b.to_dict() for b in turn.conversation_history],
                "tool_results": [b.to_dict() for b in turn.tool_results],
                "assistant_blocks": [b.to_dict() for b in turn.assistant_blocks],
                "all_blocks": [b.to_dict() for b in turn.all_blocks],
                "turn": turn_dict,
                "summary": summary,

                # CamelCase aliases for web dashboard
                "turnIndex": turn.turn_index,
                "turnId": turn.turn_id,
                "correlationId": turn.correlation_id,
                "durationMs": turn.duration_ms,
                "ttftMs": turn.ttft_ms,
                "inputTokens": turn.input_tokens,
                "outputTokens": turn.output_tokens,
                "cachedReadTokens": turn.cached_read_tokens,
                "cachedCreatedTokens": turn.cached_created_tokens,
                "cost": turn.turn_cost_usd,
                "wastedCost": turn.wasted_cost_usd,
                "tokenBreakdown": token_breakdown,
                "tokens": token_breakdown,
                "violations": [v.to_dict() for v in violations],
                "blocks": [b.to_dict() for b in turn.all_blocks],
            }

            self.broadcaster.publish_nowait(
                UIEvent(
                    event_type=UIEventType.TURN_COMPLETED,
                    session_id=session_id,
                    timestamp=turn.timestamp,
                    payload=turn_payload,
                )
            )

            for v in violations:
                self.broadcaster.publish_nowait(
                    UIEvent(
                        event_type=UIEventType.VIOLATION_DETECTED,
                        session_id=session_id,
                        timestamp=turn.timestamp,
                        payload=v.to_dict(),
                    )
                )

            self.broadcaster.publish_nowait(
                UIEvent(
                    event_type=UIEventType.SESSION_SUMMARY_UPDATED,
                    session_id=session_id,
                    timestamp=turn.timestamp,
                    payload={"sessionId": session_id, "summary": summary},
                )
            )


def spawn_mitmproxy(
    proxy_port: int = DEFAULT_PROXY_PORT,
    socket_path: str = DEFAULT_SOCKET_PATH,
) -> Optional[subprocess.Popen[Any]]:
    """Spawn mitmdump interceptor process if proxy_port is not already listening."""
    try:
        with socket.create_connection(("127.0.0.1", proxy_port), timeout=0.2):
            return None
    except OSError:
        pass

    addon_path = str(Path(__file__).parent / "interceptor" / "addon.py")
    repo_root = str(Path(__file__).parent.parent)

    mitm_env = os.environ.copy()
    mitm_env["CTXINS_SOCKET_PATH"] = socket_path
    existing_py_path = mitm_env.get("PYTHONPATH", "")
    mitm_env["PYTHONPATH"] = f"{repo_root}:{existing_py_path}" if existing_py_path else repo_root

    mitmdump_path = shutil.which("mitmdump")
    if mitmdump_path:
        mitm_cmd = [mitmdump_path, "-p", str(proxy_port), "-s", addon_path, "-q"]
    else:
        mitm_cmd = [
            sys.executable,
            "-c",
            "import sys; from mitmproxy.tools.main import mitmdump; sys.exit(mitmdump())",
            "-p",
            str(proxy_port),
            "-s",
            addon_path,
            "-q",
        ]

    proc = subprocess.Popen(
        mitm_cmd,
        env=mitm_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    start_wait = time.time()
    while time.time() - start_wait < 5.0:
        if proc.poll() is not None:
            break
        try:
            with socket.create_connection(("127.0.0.1", proxy_port), timeout=0.1):
                break
        except OSError:
            time.sleep(0.05)

    return proc


def run_tui(
    socket_path: str = DEFAULT_SOCKET_PATH,
    proxy_port: int = DEFAULT_PROXY_PORT,
) -> None:
    """Launch standalone Textual TUI attached to running Core Engine."""
    bridge = CorePipelineBridge()
    server = UDSFrameServer(socket_path=socket_path, on_turn_callback=bridge.handle_wire_envelope)
    mitm_proc = spawn_mitmproxy(proxy_port=proxy_port, socket_path=socket_path)

    async def _start_and_run() -> None:
        await server.start()
        try:
            state = TUIState()
            app = CtxinsTUIApp(state=state, broadcaster=bridge.broadcaster)
            await app.run_async()
        finally:
            if mitm_proc is not None and mitm_proc.poll() is None:
                mitm_proc.terminate()
                try:
                    mitm_proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    mitm_proc.kill()
            await server.stop()

    asyncio.run(_start_and_run())


def run_web(
    port: int = DEFAULT_WEB_PORT,
    host: str = DEFAULT_WEB_HOST,
    socket_path: str = DEFAULT_SOCKET_PATH,
    proxy_port: int = DEFAULT_PROXY_PORT,
) -> None:
    """Launch standalone Web Dashboard attached to running Core Engine."""
    import uvicorn

    bridge = CorePipelineBridge()
    server = UDSFrameServer(socket_path=socket_path, on_turn_callback=bridge.handle_wire_envelope)
    mitm_proc = spawn_mitmproxy(proxy_port=proxy_port, socket_path=socket_path)

    async def _run_web_pipeline() -> None:
        await server.start()
        try:
            web_app = create_app(store=bridge.store, broadcaster=bridge.broadcaster)
            config = uvicorn.Config(app=web_app, host=host, port=port, log_level="warning")
            uvi_server = uvicorn.Server(config)
            await uvi_server.serve()
        finally:
            if mitm_proc is not None and mitm_proc.poll() is None:
                mitm_proc.terminate()
                try:
                    mitm_proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    mitm_proc.kill()
            await server.stop()

    asyncio.run(_run_web_pipeline())


def run_live(
    ui_mode: str = "tui",
    port: int = DEFAULT_WEB_PORT,
    host: str = DEFAULT_WEB_HOST,
    socket_path: str = DEFAULT_SOCKET_PATH,
    proxy_port: int = DEFAULT_PROXY_PORT,
) -> None:
    """Start Core Engine + selected UI."""
    if ui_mode == "web":
        run_web(port=port, host=host, socket_path=socket_path, proxy_port=proxy_port)
    else:
        run_tui(socket_path=socket_path, proxy_port=proxy_port)


def run_with_harness(
    command: List[str],
    ui_mode: str = "tui",
    port: int = DEFAULT_WEB_PORT,
    host: str = DEFAULT_WEB_HOST,
    socket_path: str = DEFAULT_SOCKET_PATH,
    proxy_port: int = DEFAULT_PROXY_PORT,
) -> None:
    """Start proxy, launch agent harness command, and run interactive UI."""
    bridge = CorePipelineBridge()
    server = UDSFrameServer(socket_path=socket_path, on_turn_callback=bridge.handle_wire_envelope)

    cert_path = os.path.expanduser("~/.mitmproxy/mitmproxy-ca-cert.pem")

    # Configure proxy environment variables (both upper and lowercase for Go/Python/Node)
    env = os.environ.copy()
    proxy_url = f"http://127.0.0.1:{proxy_port}"
    env["HTTP_PROXY"] = proxy_url
    env["HTTPS_PROXY"] = proxy_url
    env["ALL_PROXY"] = proxy_url
    env["http_proxy"] = proxy_url
    env["https_proxy"] = proxy_url
    env["all_proxy"] = proxy_url
    if os.path.exists(cert_path):
        env["SSL_CERT_FILE"] = cert_path
        env["REQUESTS_CA_BUNDLE"] = cert_path
        env["NODE_EXTRA_CA_CERTS"] = cert_path

    async def _run_pipeline() -> None:
        await server.start()
        mitm_proc = spawn_mitmproxy(proxy_port=proxy_port, socket_path=socket_path)
        harness_proc: Optional[subprocess.Popen[Any]] = None
        try:
            # Launch harness subprocess if command is specified
            if command:
                harness_proc = subprocess.Popen(command, env=env)

            if ui_mode == "web":
                import uvicorn
                web_app = create_app(store=bridge.store, broadcaster=bridge.broadcaster)
                config = uvicorn.Config(app=web_app, host=host, port=port, log_level="warning")
                uvi_server = uvicorn.Server(config)
                await uvi_server.serve()
            else:
                state = TUIState()
                tui_app = CtxinsTUIApp(state=state, broadcaster=bridge.broadcaster)
                await tui_app.run_async()

        finally:
            if harness_proc is not None and harness_proc.poll() is None:
                harness_proc.terminate()
                try:
                    harness_proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    harness_proc.kill()
            if mitm_proc is not None and mitm_proc.poll() is None:
                mitm_proc.terminate()
                try:
                    mitm_proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    mitm_proc.kill()
            await server.stop()

    asyncio.run(_run_pipeline())


def build_parser() -> argparse.ArgumentParser:
    """Build argparse parser for ctxins CLI."""
    parser = argparse.ArgumentParser(
        prog="ctxins",
        description="Context Inspector & Optimizer for Agentic Harnesses",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Subcommands")

    # 1. tui
    tui_p = subparsers.add_parser("tui", help="Launch interactive Terminal UI")
    tui_p.add_argument("--socket", default=DEFAULT_SOCKET_PATH, help="UDS socket path")
    tui_p.add_argument("--proxy-port", type=int, default=DEFAULT_PROXY_PORT, help="Proxy port")

    # 2. web
    web_p = subparsers.add_parser("web", help="Launch Web Dashboard")
    web_p.add_argument("--port", type=int, default=DEFAULT_WEB_PORT, help="Web server port")
    web_p.add_argument("--host", default=DEFAULT_WEB_HOST, help="Web server host")
    web_p.add_argument("--socket", default=DEFAULT_SOCKET_PATH, help="UDS socket path")
    web_p.add_argument("--proxy-port", type=int, default=DEFAULT_PROXY_PORT, help="Proxy port")

    # 3. live
    live_p = subparsers.add_parser("live", help="Start Core Engine and presentation UI")
    ui_group = live_p.add_mutually_exclusive_group()
    ui_group.add_argument("--tui", dest="ui_mode", action="store_const", const="tui", default="tui", help="Use Terminal UI (default)")
    ui_group.add_argument("--web", dest="ui_mode", action="store_const", const="web", help="Use Web Dashboard")
    live_p.add_argument("--port", type=int, default=DEFAULT_WEB_PORT, help="Web port")
    live_p.add_argument("--host", default=DEFAULT_WEB_HOST, help="Web host")
    live_p.add_argument("--socket", default=DEFAULT_SOCKET_PATH, help="UDS socket path")
    live_p.add_argument("--proxy-port", type=int, default=DEFAULT_PROXY_PORT, help="Proxy port")

    # 4. run
    run_p = subparsers.add_parser("run", help="Start proxy and execute agent harness wrapped in ctxins")
    run_ui_group = run_p.add_mutually_exclusive_group()
    run_ui_group.add_argument("--tui", dest="ui_mode", action="store_const", const="tui", default="tui", help="Use Terminal UI (default)")
    run_ui_group.add_argument("--web", dest="ui_mode", action="store_const", const="web", help="Use Web Dashboard")
    run_p.add_argument("--port", type=int, default=DEFAULT_WEB_PORT, help="Web port")
    run_p.add_argument("--host", default=DEFAULT_WEB_HOST, help="Web host")
    run_p.add_argument("--proxy-port", type=int, default=DEFAULT_PROXY_PORT, help="Proxy port")
    run_p.add_argument("--socket", default=DEFAULT_SOCKET_PATH, help="UDS socket path")
    run_p.add_argument("command", nargs=argparse.REMAINDER, help="Command to execute after --")

    return parser


def main(args: Optional[List[str]] = None) -> int:
    """CLI entry point for ctxins."""
    parser = build_parser()
    parsed = parser.parse_args(args)

    if not parsed.subcommand:
        parser.print_help()
        return 0

    if parsed.subcommand == "tui":
        run_tui(socket_path=parsed.socket, proxy_port=parsed.proxy_port)
    elif parsed.subcommand == "web":
        run_web(
            port=parsed.port,
            host=parsed.host,
            socket_path=parsed.socket,
            proxy_port=parsed.proxy_port,
        )
    elif parsed.subcommand == "live":
        run_live(
            ui_mode=parsed.ui_mode,
            port=parsed.port,
            host=parsed.host,
            socket_path=parsed.socket,
            proxy_port=parsed.proxy_port,
        )
    elif parsed.subcommand == "run":
        cmd = parsed.command
        if cmd and cmd[0] == "--":
            cmd = cmd[1:]
        run_with_harness(
            command=cmd,
            ui_mode=parsed.ui_mode,
            port=parsed.port,
            host=parsed.host,
            socket_path=parsed.socket,
            proxy_port=parsed.proxy_port,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
