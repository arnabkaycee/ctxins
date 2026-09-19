"""ctxins unified command-line entry points and runner."""

from __future__ import annotations

import argparse
import asyncio
import errno
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
from src.core.logging_config import DEFAULT_LOG_FILE, configure_logging
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


def find_available_port(start_port: int, max_attempts: int = 50) -> int:
    """Find the first open TCP port starting from start_port."""
    for port in range(start_port, start_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start_port


def get_env_exports(proxy_port: int = DEFAULT_PROXY_PORT) -> Dict[str, str]:
    """Return dictionary of environment variables for proxy and certificate configuration."""
    cert_path = os.path.expanduser("~/.mitmproxy/mitmproxy-ca-cert.pem")
    proxy_url = f"http://127.0.0.1:{proxy_port}"
    exports: Dict[str, str] = {
        "HTTP_PROXY": proxy_url,
        "HTTPS_PROXY": proxy_url,
        "ALL_PROXY": proxy_url,
        "http_proxy": proxy_url,
        "https_proxy": proxy_url,
        "all_proxy": proxy_url,
    }
    if os.path.exists(cert_path):
        exports["SSL_CERT_FILE"] = cert_path
        exports["REQUESTS_CA_BUNDLE"] = cert_path
        exports["NODE_EXTRA_CA_CERTS"] = cert_path
    return exports


def run_env(proxy_port: int = DEFAULT_PROXY_PORT, as_json: bool = False) -> None:
    """Print proxy and cert environment configuration to stdout."""
    exports = get_env_exports(proxy_port=proxy_port)
    if as_json:
        import json

        print(json.dumps(exports, indent=2))
    else:
        for k, v in exports.items():
            print(f'export {k}="{v}"')


async def _shutdown_uvicorn(
    server: Optional[Any], task: Optional[asyncio.Task[Any]]
) -> None:
    """Gracefully shutdown background uvicorn server and task without CancelledError noise."""
    if server is not None:
        server.should_exit = True
    if task is not None and not task.done():
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

class CorePipelineBridge:
    """Bridges Core UDS telemetry ingestion to PresentationBroadcaster and SessionStore."""

    def __init__(
        self,
        store: Optional[SessionStore] = None,
        broadcaster: Optional[PresentationBroadcaster] = None,
        analyzer: Optional[PollutionAnalyzer] = None,
        auto_scan: bool = True,
    ) -> None:
        self.store = store or SessionStore()
        self.broadcaster = broadcaster or PresentationBroadcaster()
        self.analyzer = analyzer or PollutionAnalyzer()
        if auto_scan:
            self.scan_and_register_agents()

    def scan_and_register_agents(self) -> List[str]:
        """Scan running agents immediately and register newly detected sessions."""
        from src.interceptor.detection.process_detector import ProcessDetector

        detector = ProcessDetector()
        agents = detector.scan_running_agents()
        new_sessions: List[str] = []
        for ag in agents:
            if ag.pid is None:
                continue
            sess_id = f"sess_{ag.name}_{ag.pid}"
            if sess_id not in self.store.list_sessions():
                self.store.register_session(
                    sess_id,
                    metadata={
                        "sessionId": sess_id,
                        "harness": ag.name,
                        "agentHarness": ag.name,
                        "agent": ag.to_dict(),
                        "status": "detected",
                        "model": "auto-detect",
                        "provider": "auto-detect",
                    },
                )
                self.broadcaster.publish_nowait(
                    UIEvent(
                        event_type=UIEventType.SESSION_CREATED,
                        session_id=sess_id,
                        payload={
                            "sessionId": sess_id,
                            "agentHarness": ag.name,
                            "harness": ag.name,
                            "agent": ag.to_dict(),
                            "status": "detected",
                            "model": "auto-detect",
                            "provider": "auto-detect",
                        },
                    )
                )
                logger.info("Detected running agent: %s (PID: %s) -> registered session '%s'", ag.name, ag.pid, sess_id)
                new_sessions.append(sess_id)
        return new_sessions

    def cleanup_dead_agent_sessions(self) -> List[str]:
        """Check registered sessions with a known PID and handle disconnect if process exited."""
        dead_sessions: List[str] = []
        for sess_id in list(self.store.session_metadata.keys()):
            meta = self.store.get_session_metadata(sess_id) or {}
            agent_info = meta.get("agent")
            if not isinstance(agent_info, dict):
                continue
            pid = agent_info.get("pid")
            if not pid or not isinstance(pid, int):
                continue

            is_alive = True
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                is_alive = False
            except PermissionError:
                is_alive = True
            except OSError as err:
                if err.errno == errno.ESRCH:
                    is_alive = False

            if not is_alive:
                dead_sessions.append(sess_id)
                meta = self.store.get_session_metadata(sess_id) or {}
                meta["status"] = "disconnected"
                self.store.register_session(sess_id, metadata=meta)
                logger.info(
                    "Process %s for session '%s' exited. Preserving session data in memory as long as ctxins is open.",
                    pid,
                    sess_id,
                )
                self.broadcaster.publish_nowait(
                    UIEvent(
                        event_type=UIEventType.SESSION_DISCONNECTED,
                        session_id=sess_id,
                        payload={
                            "sessionId": sess_id,
                            "preserved": True,
                            "message": f"Process {pid} exited. Preserved in memory as long as ctxins is open.",
                        },
                    )
                )
        return dead_sessions

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
            agent_info = (
                envelope.payload.get("client_metadata", {}).get("agent")
                or envelope.payload.get("agent")
            )
            harness = (
                envelope.payload.get("client_metadata", {}).get("harness")
                or envelope.payload.get("harness")
                or (agent_info.get("name") if isinstance(agent_info, dict) else None)
                or getattr(agent_info, "name", None)
                or "unknown"
            )
            if session_id not in self.store.list_sessions():
                self.store.register_session(
                    session_id,
                    metadata={
                        "sessionId": session_id,
                        "model": envelope.payload.get("model", "unknown"),
                        "provider": envelope.payload.get("provider", "unknown"),
                        "agentHarness": harness,
                        "harness": harness,
                        "agent": agent_info,
                        "status": "active",
                    },
                )

            # Link scanner placeholder session if one was registered for this PID
            pid = (
                envelope.payload.get("client_metadata", {}).get("pid")
                or (agent_info.get("pid") if isinstance(agent_info, dict) else None)
                or getattr(agent_info, "pid", None)
            )
            if pid and harness != "unknown":
                scan_sid = f"sess_{harness}_{pid}"
                if scan_sid in self.store.list_sessions() and scan_sid != session_id:
                    self.store.alias_session(scan_sid, session_id)

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
                            "agentHarness": harness,
                            "harness": harness,
                            "agent": agent_info,
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
                "tokens": turn.total_tokens,
                "total_tokens": turn.total_tokens,
                "totalTokens": turn.total_tokens,
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
            logger.info(
                "Processed turn #%d for session '%s' (tokens=%d, cost=$%.4f, model=%s)",
                turn.turn_index,
                session_id,
                turn.total_tokens,
                turn.turn_cost_usd,
                turn.model,
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

        elif envelope.event_type == WireEventType.SESSION_DISCONNECTED:
            meta = self.store.get_session_metadata(session_id) or {}
            meta["status"] = "disconnected"
            self.store.register_session(session_id, metadata=meta)
            logger.info(
                "Session '%s' disconnected. Preserving all session data in memory as long as ctxins is open.",
                session_id,
            )
            self.broadcaster.publish_nowait(
                UIEvent(
                    event_type=UIEventType.SESSION_DISCONNECTED,
                    session_id=session_id,
                    timestamp=envelope.timestamp,
                    payload={
                        "sessionId": session_id,
                        "preserved": True,
                        "message": f"Session '{session_id}' disconnected. Preserved in memory as long as ctxins is open.",
                    },
                )
            )

        elif envelope.event_type == WireEventType.SESSION_EXPORTED:
            self.store.mark_exported(session_id)
            logger.info("Session '%s' marked as exported via JSONC.", session_id)


def spawn_mitmproxy(
    proxy_port: int = DEFAULT_PROXY_PORT,
    socket_path: str = DEFAULT_SOCKET_PATH,
    target: Optional[str] = None,
    target_port: Optional[int] = None,
    log_level: Optional[str] = None,
    log_file: Optional[str] = None,
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
    mitm_env["CTXINS_PROXY_PORT"] = str(proxy_port)
    if target:
        mitm_env["CTXINS_TARGET"] = target
    elif target_port is not None:
        mitm_env["CTXINS_TARGET"] = f"http://127.0.0.1:{target_port}"

    eff_log_file = log_file or os.environ.get("CTXINS_LOG_FILE") or str(DEFAULT_LOG_FILE.resolve())
    mitm_env["CTXINS_LOG_FILE"] = str(eff_log_file)

    eff_log_level = (
        log_level
        or os.environ.get("CTXINS_LOG_LEVEL")
        or ("DEBUG" if os.environ.get("CTXINS_DEBUG") in ("1", "true", "yes") else "INFO")
    )
    mitm_env["CTXINS_LOG_LEVEL"] = str(eff_log_level)

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


async def _agent_scanner_loop(bridge: CorePipelineBridge, interval: float = 2.0) -> None:
    """Periodically scan for running agent processes and clean up exited ones."""
    while True:
        try:
            bridge.scan_and_register_agents()
            bridge.cleanup_dead_agent_sessions()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug("Error in agent scanner loop: %s", e)
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            break


def run_tui(
    socket_path: str = DEFAULT_SOCKET_PATH,
    proxy_port: int = DEFAULT_PROXY_PORT,
    target: Optional[str] = None,
    target_port: Optional[int] = None,
    no_web: bool = False,
    web_port: int = DEFAULT_WEB_PORT,
) -> None:
    """Launch standalone Textual TUI attached to running Core Engine."""
    bridge = CorePipelineBridge()
    server = UDSFrameServer(socket_path=socket_path, on_turn_callback=bridge.handle_wire_envelope)
    actual_proxy_port = find_available_port(proxy_port)
    mitm_proc = spawn_mitmproxy(
        proxy_port=actual_proxy_port,
        socket_path=socket_path,
        target=target,
        target_port=target_port,
    )

    logger.info("Launching Ctxins TUI (proxy port: %s, socket: %s)", actual_proxy_port, socket_path)

    async def _start_and_run() -> None:
        await server.start()
        # Immediately detect running agents and start background scanner
        bridge.scan_and_register_agents()
        scanner_task = asyncio.create_task(_agent_scanner_loop(bridge))

        uvi_task: Optional[asyncio.Task[Any]] = None
        uvi_server: Optional[Any] = None
        actual_web_port = find_available_port(web_port)
        if not no_web:
            try:
                import uvicorn

                web_app = create_app(store=bridge.store, broadcaster=bridge.broadcaster)
                config = uvicorn.Config(
                    app=web_app, host="127.0.0.1", port=actual_web_port, log_level="error"
                )
                uvi_server = uvicorn.Server(config)
                uvi_task = asyncio.create_task(uvi_server.serve())
            except Exception as e:
                logger.warning("Could not start background Web Dashboard: %s", e)

        try:
            state = TUIState()
            app = CtxinsTUIApp(
                state=state,
                broadcaster=bridge.broadcaster,
                store=bridge.store,
                proxy_port=actual_proxy_port,
                web_url=f"http://127.0.0.1:{actual_web_port}" if not no_web else None,
            )
            await app.run_async()
        finally:
            scanner_task.cancel()
            try:
                await scanner_task
            except asyncio.CancelledError:
                pass
            await _shutdown_uvicorn(uvi_server, uvi_task)
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
    target: Optional[str] = None,
    target_port: Optional[int] = None,
) -> None:
    """Launch standalone Web Dashboard attached to running Core Engine."""
    import uvicorn

    bridge = CorePipelineBridge()
    server = UDSFrameServer(socket_path=socket_path, on_turn_callback=bridge.handle_wire_envelope)
    actual_proxy_port = find_available_port(proxy_port)
    actual_web_port = find_available_port(port)
    mitm_proc = spawn_mitmproxy(
        proxy_port=actual_proxy_port,
        socket_path=socket_path,
        target=target,
        target_port=target_port,
    )

    async def _run_web_pipeline() -> None:
        await server.start()
        # Immediately detect running agents and start background scanner
        bridge.scan_and_register_agents()
        scanner_task = asyncio.create_task(_agent_scanner_loop(bridge))
        uvi_server: Optional[Any] = None
        try:
            web_app = create_app(store=bridge.store, broadcaster=bridge.broadcaster)
            config = uvicorn.Config(app=web_app, host=host, port=actual_web_port, log_level="warning")
            uvi_server = uvicorn.Server(config)
            await uvi_server.serve()
        finally:
            scanner_task.cancel()
            try:
                await scanner_task
            except asyncio.CancelledError:
                pass
            if uvi_server is not None:
                uvi_server.should_exit = True
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
    target: Optional[str] = None,
    target_port: Optional[int] = None,
    no_web: bool = False,
    web_port: int = DEFAULT_WEB_PORT,
) -> None:
    """Start Core Engine + selected UI."""
    if ui_mode == "web":
        run_web(
            port=port,
            host=host,
            socket_path=socket_path,
            proxy_port=proxy_port,
            target=target,
            target_port=target_port,
        )
    else:
        run_tui(
            socket_path=socket_path,
            proxy_port=proxy_port,
            target=target,
            target_port=target_port,
            no_web=no_web,
            web_port=web_port,
        )


def run_with_harness(
    command: List[str],
    ui_mode: str = "tui",
    port: int = DEFAULT_WEB_PORT,
    host: str = DEFAULT_WEB_HOST,
    socket_path: str = DEFAULT_SOCKET_PATH,
    proxy_port: int = DEFAULT_PROXY_PORT,
    target: Optional[str] = None,
    target_port: Optional[int] = None,
    no_web: bool = False,
    web_port: int = DEFAULT_WEB_PORT,
) -> None:
    """Start proxy, launch agent harness command, and run presentation UI without terminal conflict."""
    bridge = CorePipelineBridge()
    server = UDSFrameServer(socket_path=socket_path, on_turn_callback=bridge.handle_wire_envelope)
    actual_proxy_port = find_available_port(proxy_port)
    actual_web_port = find_available_port(web_port)

    cert_path = os.path.expanduser("~/.mitmproxy/mitmproxy-ca-cert.pem")

    # Configure proxy environment variables (both upper and lowercase for Go/Python/Node)
    env = os.environ.copy()
    proxy_url = f"http://127.0.0.1:{actual_proxy_port}"
    env["HTTP_PROXY"] = proxy_url
    env["HTTPS_PROXY"] = proxy_url
    env["ALL_PROXY"] = proxy_url
    env["http_proxy"] = proxy_url
    env["https_proxy"] = proxy_url
    env["all_proxy"] = proxy_url
    if target:
        env["CTXINS_TARGET"] = target
    elif target_port is not None:
        env["CTXINS_TARGET"] = f"http://127.0.0.1:{target_port}"

    if os.path.exists(cert_path):
        env["SSL_CERT_FILE"] = cert_path
        env["REQUESTS_CA_BUNDLE"] = cert_path
        env["NODE_EXTRA_CA_CERTS"] = cert_path

    async def _run_pipeline() -> None:
        await server.start()
        # Immediately detect running agents and start background scanner
        bridge.scan_and_register_agents()
        scanner_task = asyncio.create_task(_agent_scanner_loop(bridge))

        mitm_proc = spawn_mitmproxy(
            proxy_port=actual_proxy_port,
            socket_path=socket_path,
            target=target,
            target_port=target_port,
        )
        uvi_task: Optional[asyncio.Task[Any]] = None
        uvi_server: Optional[Any] = None
        if not no_web:
            try:
                import uvicorn

                web_app = create_app(store=bridge.store, broadcaster=bridge.broadcaster)
                config = uvicorn.Config(
                    app=web_app, host=host, port=actual_web_port, log_level="error"
                )
                uvi_server = uvicorn.Server(config)
                uvi_task = asyncio.create_task(uvi_server.serve())
            except Exception as e:
                logger.warning("Could not start background Web Dashboard: %s", e)

        harness_proc: Optional[subprocess.Popen[Any]] = None
        try:
            if command:
                # If TUI requested and tmux available, split pane
                if ui_mode == "tui" and os.environ.get("TMUX"):
                    try:
                        subprocess.run(
                            ["tmux", "split-window", "-h", f"ctxins tui --proxy-port {actual_proxy_port}"],
                            check=False,
                        )
                    except Exception:
                        pass

                # Execute agent harness in foreground with direct terminal I/O
                # This guarantees interactive agent works with zero curses/raw-mode corruption
                logger.info("Interceptor active on 127.0.0.1:%s", actual_proxy_port)
                if not no_web:
                    logger.info("Live Web Dashboard at http://127.0.0.1:%s", actual_web_port)

                harness_proc = subprocess.Popen(command, env=env)
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, harness_proc.wait)
            else:
                if ui_mode == "web":
                    if uvi_task:
                        await uvi_task
                else:
                    state = TUIState()
                    tui_app = CtxinsTUIApp(
                        state=state,
                        broadcaster=bridge.broadcaster,
                        store=bridge.store,
                        proxy_port=actual_proxy_port,
                        web_url=f"http://127.0.0.1:{actual_web_port}" if not no_web else None,
                    )
                    await tui_app.run_async()

        finally:
            scanner_task.cancel()
            try:
                await scanner_task
            except asyncio.CancelledError:
                pass
            if harness_proc is not None and harness_proc.poll() is None:
                harness_proc.terminate()
                try:
                    harness_proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    harness_proc.kill()
            await _shutdown_uvicorn(uvi_server, uvi_task)
            if mitm_proc is not None and mitm_proc.poll() is None:
                mitm_proc.terminate()
                try:
                    mitm_proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    mitm_proc.kill()
            await server.stop()

    asyncio.run(_run_pipeline())


def _build_log_parser() -> argparse.ArgumentParser:
    """Build shared parser defining logging and debug CLI arguments."""
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument(
        "--debug",
        "-d",
        action="store_true",
        help="Enable verbose debug logging (sets level to DEBUG and writes to log file)",
    )
    p.add_argument(
        "--log-level",
        type=str.upper,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default=None,
        help="Explicit logging level (default: WARNING)",
    )
    p.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="Path to write log file (default: ~/.ctxins/ctxins.log in debug/TUI mode)",
    )
    return p


def build_parser() -> argparse.ArgumentParser:
    """Build argparse parser for ctxins CLI."""
    log_p = _build_log_parser()
    parser = argparse.ArgumentParser(
        prog="ctxins",
        description="Context Inspector & Optimizer for Agentic Harnesses",
        parents=[log_p],
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Subcommands")

    # 1. tui
    tui_p = subparsers.add_parser("tui", parents=[log_p], help="Launch interactive Terminal UI")
    tui_p.add_argument("--socket", default=DEFAULT_SOCKET_PATH, help="UDS socket path")
    tui_p.add_argument("--proxy-port", type=int, default=DEFAULT_PROXY_PORT, help="Proxy port")
    tui_p.add_argument("--target-port", type=int, default=None, help="Target port for local LLM")
    tui_p.add_argument("--target", default=None, help="Target upstream URL (e.g. http://localhost:8000)")
    tui_p.add_argument("--no-web", action="store_true", help="Disable concurrent background Web Dashboard")
    tui_p.add_argument("--web-port", type=int, default=DEFAULT_WEB_PORT, help="Web Dashboard port")

    # 2. web
    web_p = subparsers.add_parser("web", parents=[log_p], help="Launch Web Dashboard")
    web_p.add_argument("--port", type=int, default=DEFAULT_WEB_PORT, help="Web server port")
    web_p.add_argument("--host", default=DEFAULT_WEB_HOST, help="Web server host")
    web_p.add_argument("--socket", default=DEFAULT_SOCKET_PATH, help="UDS socket path")
    web_p.add_argument("--proxy-port", type=int, default=DEFAULT_PROXY_PORT, help="Proxy port")
    web_p.add_argument("--target-port", type=int, default=None, help="Target port for local LLM")
    web_p.add_argument("--target", default=None, help="Target upstream URL (e.g. http://localhost:8000)")

    # 3. live
    live_p = subparsers.add_parser("live", parents=[log_p], help="Start Core Engine and presentation UI")
    ui_group = live_p.add_mutually_exclusive_group()
    ui_group.add_argument("--tui", dest="ui_mode", action="store_const", const="tui", default="tui", help="Use Terminal UI (default)")
    ui_group.add_argument("--web", dest="ui_mode", action="store_const", const="web", help="Use Web Dashboard")
    live_p.add_argument("--port", type=int, default=DEFAULT_WEB_PORT, help="Web port")
    live_p.add_argument("--host", default=DEFAULT_WEB_HOST, help="Web host")
    live_p.add_argument("--socket", default=DEFAULT_SOCKET_PATH, help="UDS socket path")
    live_p.add_argument("--proxy-port", type=int, default=DEFAULT_PROXY_PORT, help="Proxy port")
    live_p.add_argument("--target-port", type=int, default=None, help="Target port for local LLM")
    live_p.add_argument("--target", default=None, help="Target upstream URL (e.g. http://localhost:8000)")
    live_p.add_argument("--no-web", action="store_true", help="Disable concurrent background Web Dashboard")
    live_p.add_argument("--web-port", type=int, default=DEFAULT_WEB_PORT, help="Web Dashboard port")

    # 4. run
    run_p = subparsers.add_parser("run", parents=[log_p], help="Start proxy and execute agent harness wrapped in ctxins")
    run_ui_group = run_p.add_mutually_exclusive_group()
    run_ui_group.add_argument("--tui", dest="ui_mode", action="store_const", const="tui", default="tui", help="Use Terminal UI (default)")
    run_ui_group.add_argument("--web", dest="ui_mode", action="store_const", const="web", help="Use Web Dashboard")
    run_p.add_argument("--port", type=int, default=DEFAULT_WEB_PORT, help="Web port")
    run_p.add_argument("--host", default=DEFAULT_WEB_HOST, help="Web host")
    run_p.add_argument("--proxy-port", type=int, default=DEFAULT_PROXY_PORT, help="Proxy port")
    run_p.add_argument("--socket", default=DEFAULT_SOCKET_PATH, help="UDS socket path")
    run_p.add_argument("--target-port", type=int, default=None, help="Target port for local LLM")
    run_p.add_argument("--target", default=None, help="Target upstream URL (e.g. http://localhost:8000)")
    run_p.add_argument("--no-web", action="store_true", help="Disable concurrent background Web Dashboard")
    run_p.add_argument("--web-port", type=int, default=DEFAULT_WEB_PORT, help="Web Dashboard port")
    run_p.add_argument("command", nargs=argparse.REMAINDER, help="Command to execute after --")

    # 5. env
    env_p = subparsers.add_parser("env", parents=[log_p], help="Generate shell export commands for proxy & certs")
    env_p.add_argument("--proxy-port", type=int, default=DEFAULT_PROXY_PORT, help="Proxy port")
    env_p.add_argument("--json", action="store_true", help="Output JSON format instead of shell export")

    return parser


def main(args: Optional[List[str]] = None) -> int:
    """CLI entry point for ctxins."""
    parser = build_parser()

    if args is None:
        raw_args = sys.argv[1:]
    else:
        raw_args = list(args)

    # If no subcommand specified, default to "tui"
    if not raw_args:
        raw_args = ["tui"]

    # Extract any top-level logging options that might precede subcommands
    log_p = _build_log_parser()
    top_opts, _ = log_p.parse_known_args(raw_args)

    parsed = parser.parse_args(raw_args)

    effective_debug = bool(getattr(parsed, "debug", False) or top_opts.debug)
    effective_log_level = getattr(parsed, "log_level", None) or top_opts.log_level
    effective_log_file = getattr(parsed, "log_file", None) or top_opts.log_file

    # Propagate into environment so child subprocesses automatically inherit them
    if effective_debug:
        os.environ["CTXINS_DEBUG"] = "1"
        os.environ["CTXINS_LOG_LEVEL"] = "DEBUG"
    elif effective_log_level:
        os.environ["CTXINS_LOG_LEVEL"] = str(effective_log_level)
    else:
        os.environ["CTXINS_LOG_LEVEL"] = "INFO"

    if effective_log_file:
        os.environ["CTXINS_LOG_FILE"] = str(effective_log_file)
    else:
        os.environ["CTXINS_LOG_FILE"] = str(DEFAULT_LOG_FILE.resolve())

    subcmd = parsed.subcommand or "tui"
    mode = (
        "env"
        if subcmd == "env"
        else ("web" if subcmd == "web" or getattr(parsed, "ui_mode", "") == "web" else subcmd)
    )

    configure_logging(
        level=os.environ["CTXINS_LOG_LEVEL"],
        debug=effective_debug,
        log_file=os.environ["CTXINS_LOG_FILE"],
        mode=mode,
    )

    if not parsed.subcommand:
        parser.print_help()
        return 0

    if parsed.subcommand == "env":
        run_env(proxy_port=parsed.proxy_port, as_json=parsed.json)
        return 0
    elif parsed.subcommand == "tui":
        run_tui(
            socket_path=parsed.socket,
            proxy_port=parsed.proxy_port,
            target=getattr(parsed, "target", None),
            target_port=getattr(parsed, "target_port", None),
            no_web=getattr(parsed, "no_web", False),
            web_port=getattr(parsed, "web_port", DEFAULT_WEB_PORT),
        )
    elif parsed.subcommand == "web":
        run_web(
            port=parsed.port,
            host=parsed.host,
            socket_path=parsed.socket,
            proxy_port=parsed.proxy_port,
            target=getattr(parsed, "target", None),
            target_port=getattr(parsed, "target_port", None),
        )
    elif parsed.subcommand == "live":
        run_live(
            ui_mode=parsed.ui_mode,
            port=parsed.port,
            host=parsed.host,
            socket_path=parsed.socket,
            proxy_port=parsed.proxy_port,
            target=getattr(parsed, "target", None),
            target_port=getattr(parsed, "target_port", None),
            no_web=getattr(parsed, "no_web", False),
            web_port=getattr(parsed, "web_port", DEFAULT_WEB_PORT),
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
            target=getattr(parsed, "target", None),
            target_port=getattr(parsed, "target_port", None),
            no_web=getattr(parsed, "no_web", False),
            web_port=getattr(parsed, "web_port", DEFAULT_WEB_PORT),
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
