"""Mitmproxy addon for LLM network interception, stream tapping, and telemetry egress."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import re
import threading
import time
import urllib.parse
import uuid
from typing import Any, Dict, List, Optional

from src.core.logging_config import configure_logging
from src.interceptor.correlation.tracker import ActiveTurnTracker
from src.interceptor.detection.process_detector import (
    AgentIdentity,
    ProcessDetector,
)
from src.interceptor.egress.ring_buffer import BoundedRingBuffer
from src.interceptor.egress.uds_client import UDSClient
from src.interceptor.filter.provider_router import ProviderRouter
from src.interceptor.filter.sanitizer import HeaderSanitizer
from src.interceptor.stream.accumulators.anthropic import AnthropicAccumulator
from src.interceptor.stream.accumulators.base import BaseAccumulator
from src.interceptor.stream.accumulators.gemini import GeminiAccumulator
from src.interceptor.stream.accumulators.openai import OpenAIAccumulator
from src.interceptor.stream.passthrough import StreamPassthrough
from src.schema.wire import (
    ActiveTurnContext,
    ContentBlock,
    Provider,
    TimingMetrics,
    UsageMetrics,
    WireEnvelope,
    WireEventType,
)

configure_logging(mode="headless", enable_memory_buffer=False)
logger = logging.getLogger(__name__)


class CtxinsAddon:
    """Mitmproxy addon that intercepts LLM provider traffic and ships telemetry to UDS.

    Wires together:
    - ProviderRouter: recognizes LLM provider hosts and paths.
    - HeaderSanitizer: redacts credentials from headers and payloads.
    - StreamPassthrough: zero-delay token tap for streaming responses.
    - SSE Accumulators: reconstructs turn AST and token counts per provider.
    - ActiveTurnTracker: in-flight turn correlation and TTL reaper.
    - BoundedRingBuffer: thread-safe fail-open frame buffer.
    - UDSClient: background length-prefixed IPC writer.

    Honors fail-open semantics: errors in the addon never crash or block proxied traffic.
    """

    def __init__(
        self,
        uds_client: Optional[UDSClient] = None,
        ring_buffer: Optional[BoundedRingBuffer] = None,
        tracker: Optional[ActiveTurnTracker] = None,
        router: Optional[ProviderRouter] = None,
        sanitizer: Optional[HeaderSanitizer] = None,
        passthrough: Optional[StreamPassthrough] = None,
        socket_path: Optional[str] = None,
        buffer_capacity: int = 1000,
        default_session_id: Optional[str] = None,
        process_detector: Optional[ProcessDetector] = None,
        auto_start: bool = False,
    ) -> None:
        self.buffer = ring_buffer if ring_buffer is not None else BoundedRingBuffer(capacity=buffer_capacity)

        effective_socket_path = socket_path or os.environ.get("CTXINS_SOCKET_PATH")
        if uds_client is not None:
            self.uds_client: Optional[UDSClient] = uds_client
        elif effective_socket_path:
            self.uds_client = UDSClient(
                socket_path=effective_socket_path,
                buffer=self.buffer,
            )
        else:
            self.uds_client = None

        self.router = router if router is not None else ProviderRouter()
        self.sanitizer = sanitizer if sanitizer is not None else HeaderSanitizer()
        self.process_detector = (
            process_detector if process_detector is not None else ProcessDetector()
        )

        # Connection and session lifecycle tracking for auto-detection and multi-session persistence
        self._client_to_session: Dict[tuple[str, int], str] = {}
        self._session_to_clients: Dict[str, set[tuple[str, int]]] = {}
        self._session_agents: Dict[str, AgentIdentity] = {}

        # Per-process session tracking to detect session switches within a single agent process
        self._pid_active_session: Dict[int, str] = {}
        self._pid_session_counter: Dict[int, int] = {}
        self._pid_last_history_len: Dict[int, int] = {}
        self._pid_last_first_msg_hash: Dict[int, str] = {}

        # Thread-safe chunk tee queue consumed by background worker or drained on response
        self.chunk_queue: queue.Queue[tuple[str, bytes, float]] = queue.Queue(maxsize=10000)
        self.passthrough = passthrough if passthrough is not None else StreamPassthrough(self.chunk_queue)

        # Correlation tracker wired with error emitter callback
        self.tracker = tracker if tracker is not None else ActiveTurnTracker(
            on_turn_error=self.emit_envelope,
            auto_start_reaper=False,
        )
        if tracker is not None and tracker.on_turn_error is None:
            tracker.on_turn_error = self.emit_envelope

        self.default_session_id = default_session_id or os.environ.get(
            "CTXINS_SESSION_ID", "sess_default"
        )

        self._running = False
        self._worker_thread: Optional[threading.Thread] = None

        if auto_start:
            self.start()

    @property
    def is_running(self) -> bool:
        """Return True if background workers are active."""
        return self._running

    def start(self) -> None:
        """Start background egress client, reaper, and chunk processing worker."""
        if self._running:
            return
        self._running = True

        if self.uds_client is not None and not self.uds_client.is_running:
            self.uds_client.start()

        if not self.tracker.is_reaper_running:
            self.tracker.start_reaper()

        self._worker_thread = threading.Thread(
            target=self._chunk_worker_loop,
            daemon=True,
            name="CtxinsChunkWorker",
        )
        self._worker_thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Stop background worker threads and socket client."""
        self._running = False

        if self._worker_thread is not None and self._worker_thread is not threading.current_thread():
            self._worker_thread.join(timeout=timeout)
            self._worker_thread = None

        self.tracker.stop_reaper(timeout=timeout)

        if self.uds_client is not None and self.uds_client.is_running:
            self.uds_client.stop(timeout=timeout)

    def __enter__(self) -> "CtxinsAddon":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()

    def emit_envelope(self, envelope: WireEnvelope) -> bool:
        """Serialize and push a WireEnvelope to the egress ring buffer.

        Honors fail-open semantics: exceptions are logged and never raised.
        """
        try:
            raw_bytes = envelope.to_bytes()
            return self.buffer.push(raw_bytes)
        except Exception as e:
            logger.error("Failed to emit wire envelope %s: %s", envelope.event_type, e)
            return False

    def drain_chunk_queue(self) -> None:
        """Drain all pending chunks from the passthrough queue into the tracker."""
        while not self.chunk_queue.empty():
            try:
                item = self.chunk_queue.get_nowait()
                corr_id, chunk, ts = item
                self.tracker.record_chunk(corr_id, chunk, ts)
            except queue.Empty:
                break
            except Exception as e:
                logger.debug("Error in drain_chunk_queue: %s", e)

    def _chunk_worker_loop(self) -> None:
        """Worker thread processing chunks from StreamPassthrough."""
        while self._running:
            try:
                item = self.chunk_queue.get(timeout=0.05)
                corr_id, chunk, ts = item
                self.tracker.record_chunk(corr_id, chunk, ts)
            except queue.Empty:
                continue
            except Exception as e:
                logger.debug("Error in chunk worker loop: %s", e)

    def _extract_ids(self, flow: Any, body: Optional[Dict[str, Any]] = None) -> tuple[str, str]:
        """Extract or generate session_id and correlation_id for flow."""
        headers = flow.request.headers if hasattr(flow, "request") and flow.request else {}
        lower_headers = {str(k).lower(): str(v) for k, v in headers.items()}

        session_id = None
        for key in (
            "x-session-id",
            "x-ctxins-session-id",
            "ctxins-session-id",
            "session-id",
            "session_id",
            "x-conversation-id",
            "conversation-id",
            "conversation_id",
            "x-chat-id",
            "chat-id",
            "x-opencode-session",
            "x-opencode-session-id",
            "opencode-session-id",
            "x-claude-session-id",
            "claude-session-id",
            "x-agy-session",
            "x-agy-session-id",
        ):
            val = lower_headers.get(key)
            if val and val.strip():
                session_id = val.strip()
                break

        if not session_id and isinstance(body, dict):
            for key in ("sessionId", "session_id", "conversationId", "conversation_id", "chatId", "chat_id"):
                val = body.get(key)
                if val and isinstance(val, (str, int)) and str(val).strip():
                    session_id = str(val).strip()
                    break

            if not session_id:
                meta = body.get("metadata")
                if isinstance(meta, dict):
                    for key in ("sessionId", "session_id", "conversationId", "conversation_id", "chatId", "chat_id", "user_id"):
                        val = meta.get(key)
                        if val and isinstance(val, (str, int)) and str(val).strip():
                            session_id = str(val).strip()
                            break

            if not session_id:
                req_wrapper = body.get("request")
                if isinstance(req_wrapper, dict):
                    for key in ("sessionId", "session_id", "conversationId", "conversation_id"):
                        val = req_wrapper.get(key)
                        if val and isinstance(val, (str, int)) and str(val).strip():
                            session_id = str(val).strip()
                            break

        if not session_id:
            session_id = self.default_session_id

        metadata = getattr(flow, "metadata", {})
        correlation_id = (
            metadata.get("ctxins_correlation_id")
            or lower_headers.get("x-correlation-id")
            or lower_headers.get("x-ctxins-correlation-id")
            or lower_headers.get("x-request-id")
            or getattr(flow, "id", None)
            or f"corr-{uuid.uuid4().hex[:12]}"
        )
        return session_id, correlation_id

    def _extract_client_metadata(self, flow: Any) -> Dict[str, Any]:
        """Extract diagnostic client connection metadata."""
        meta: Dict[str, Any] = {}
        if hasattr(flow, "request") and flow.request:
            meta["userAgent"] = flow.request.headers.get("user-agent", "")
            meta["method"] = getattr(flow.request, "method", "POST")

        client_conn = getattr(flow, "client_conn", None)
        if client_conn is not None:
            peer = getattr(client_conn, "peername", None)
            if peer and isinstance(peer, (tuple, list)) and len(peer) >= 2:
                meta["clientIp"] = peer[0]
                meta["clientPort"] = peer[1]
            else:
                addr = getattr(client_conn, "address", None)
                if addr and isinstance(addr, (tuple, list)) and len(addr) >= 2:
                    meta["clientIp"] = addr[0]
                    meta["clientPort"] = addr[1]

        meta.setdefault("clientIp", "127.0.0.1")
        meta.setdefault("clientPort", 0)
        return meta

    def _extract_model(self, provider: Provider, path: str, body: Dict[str, Any]) -> str:
        """Extract model identifier from body or URL path."""
        if "model" in body and isinstance(body["model"], str) and body["model"]:
            return body["model"]

        if provider == Provider.GEMINI:
            if "request" in body and isinstance(body["request"], dict) and "model" in body["request"]:
                return str(body["request"]["model"])
            m = re.search(r"/models/([^:/]+)", path)
            if m:
                return m.group(1)
        elif provider == Provider.AZURE_OPENAI:
            m = re.search(r"/deployments/([^/]+)", path)
            if m:
                return m.group(1)

        return "gemini" if provider == Provider.GEMINI else "unknown"

    def _create_accumulator(self, provider: Provider) -> Optional[BaseAccumulator]:
        """Create provider-specific SSE stream accumulator."""
        if provider == Provider.ANTHROPIC:
            return AnthropicAccumulator()
        elif provider in (
            Provider.OPENAI,
            Provider.AZURE_OPENAI,
            Provider.OPENROUTER,
            Provider.OLLAMA,
        ):
            return OpenAIAccumulator()
        elif provider == Provider.GEMINI:
            return GeminiAccumulator()
        return None

    def _is_streaming_response(self, flow: Any) -> bool:
        """Determine if response is an SSE stream."""
        if hasattr(flow, "response") and flow.response:
            content_type = flow.response.headers.get("content-type", "").lower()
            if "text/event-stream" in content_type:
                return True

        if hasattr(flow, "request") and flow.request:
            path = getattr(flow.request, "path", "")
            if "streamGenerateContent" in path:
                return True

        metadata = getattr(flow, "metadata", {})
        corr_id = metadata.get("ctxins_correlation_id")
        if corr_id:
            turn = self.tracker.get(corr_id)
            if turn and turn.request_payload.get("stream") is True:
                return True

        return False

    def _build_synthetic_response(
        self,
        turn: ActiveTurnContext,
        blocks: List[ContentBlock],
        usage: UsageMetrics,
        stop_reason: Optional[str],
    ) -> Dict[str, Any]:
        """Synthesize a canonical response payload from accumulated content blocks."""
        if turn.provider == Provider.ANTHROPIC:
            return {
                "id": f"msg_{turn.correlation_id}",
                "type": "message",
                "role": "assistant",
                "model": turn.model,
                "content": [b.to_dict() for b in blocks],
                "stop_reason": stop_reason or "end_turn",
                "usage": usage.to_dict(),
            }

        if turn.provider in (
            Provider.OPENAI,
            Provider.AZURE_OPENAI,
            Provider.OPENROUTER,
            Provider.OLLAMA,
        ):
            content_text = "".join(b.text for b in blocks if b.block_type == "text" and b.text)
            tool_calls = [
                {
                    "id": b.tool_id or f"call_{i}",
                    "type": "function",
                    "function": {
                        "name": b.tool_name or "",
                        "arguments": b.partial_json or json.dumps(b.parsed_input or {}),
                    },
                }
                for i, b in enumerate(blocks)
                if b.block_type == "tool_use"
            ]
            msg_dict: Dict[str, Any] = {"role": "assistant"}
            if content_text:
                msg_dict["content"] = content_text
            if tool_calls:
                msg_dict["tool_calls"] = tool_calls

            return {
                "id": f"chatcmpl_{turn.correlation_id}",
                "object": "chat.completion",
                "model": turn.model,
                "choices": [
                    {
                        "index": 0,
                        "message": msg_dict,
                        "finish_reason": stop_reason or "stop",
                    }
                ],
                "usage": usage.to_dict(),
            }

        if turn.provider == Provider.GEMINI:
            parts: list[dict[str, Any]] = []
            for b in blocks:
                if b.block_type == "text" and b.text:
                    parts.append({"text": b.text})
                elif b.block_type == "tool_use":
                    parts.append(
                        {
                            "functionCall": {
                                "name": b.tool_name or "",
                                "args": b.parsed_input or {},
                            }
                        }
                    )
            return {
                "candidates": [
                    {
                        "content": {"parts": parts, "role": "model"},
                        "finishReason": stop_reason or "STOP",
                        "index": 0,
                    }
                ],
                "usageMetadata": usage.to_dict(),
            }

        return {
            "content": [b.to_dict() for b in blocks],
            "usage": usage.to_dict(),
            "stop_reason": stop_reason,
        }

    def _extract_usage_from_payload(
        self, provider: Provider, payload: Dict[str, Any]
    ) -> UsageMetrics:
        """Extract usage metrics from a non-streaming response JSON payload."""
        usage = UsageMetrics()
        inner = payload.get("response", payload) if isinstance(payload.get("response"), dict) else payload
        raw = inner.get("usage") or inner.get("usageMetadata") or payload.get("usage") or payload.get("usageMetadata") or {}
        if not isinstance(raw, dict):
            return usage

        if provider == Provider.ANTHROPIC:
            usage.input_tokens = raw.get("input_tokens", 0)
            usage.output_tokens = raw.get("output_tokens", 0)
            usage.cache_creation_input_tokens = raw.get("cache_creation_input_tokens", 0)
            usage.cache_read_input_tokens = raw.get("cache_read_input_tokens", 0)
        elif provider in (
            Provider.OPENAI,
            Provider.AZURE_OPENAI,
            Provider.OPENROUTER,
            Provider.OLLAMA,
        ):
            usage.input_tokens = raw.get("prompt_tokens", 0)
            usage.output_tokens = raw.get("completion_tokens", 0)
            details = raw.get("completion_tokens_details", {})
            if isinstance(details, dict):
                usage.reasoning_tokens = details.get("reasoning_tokens", 0)
        elif provider == Provider.GEMINI:
            usage.input_tokens = raw.get("promptTokenCount", 0)
            usage.output_tokens = raw.get("candidatesTokenCount", 0)
            if "cachedContentTokenCount" in raw:
                usage.cache_read_input_tokens = raw["cachedContentTokenCount"]
            if "thoughtsTokenCount" in raw:
                usage.reasoning_tokens = raw["thoughtsTokenCount"]

        return usage

    def _extract_stop_reason(
        self, provider: Provider, payload: Dict[str, Any]
    ) -> Optional[str]:
        """Extract stop/finish reason from a non-streaming response JSON payload."""
        inner = payload.get("response", payload) if isinstance(payload.get("response"), dict) else payload
        if provider == Provider.ANTHROPIC:
            return inner.get("stop_reason")
        elif provider in (
            Provider.OPENAI,
            Provider.AZURE_OPENAI,
            Provider.OPENROUTER,
            Provider.OLLAMA,
        ):
            choices = inner.get("choices", [])
            if choices and isinstance(choices[0], dict):
                return choices[0].get("finish_reason")
        elif provider == Provider.GEMINI:
            candidates = inner.get("candidates", [])
            if candidates and isinstance(candidates[0], dict):
                return candidates[0].get("finishReason")
        return None

    def _parse_error_body(self, flow: Any) -> Dict[str, Any]:
        """Extract structured error body from response if available."""
        if hasattr(flow, "response") and flow.response and hasattr(flow.response, "content"):
            if flow.response.content:
                try:
                    data = json.loads(flow.response.content.decode("utf-8", errors="replace"))
                    if isinstance(data, dict):
                        return data
                except Exception:
                    pass
        return {}

    # --------------------------------------------------------------------------
    # Mitmproxy Lifecycle Hooks
    # --------------------------------------------------------------------------

    def running(self) -> None:
        """Called by mitmproxy when the proxy starts up."""
        try:
            self.start()
            logger.info("Ctxins mitmproxy addon initialized on port %s", os.environ.get("CTXINS_PROXY_PORT", "8080"))
        except Exception as e:
            logger.error("Error starting CtxinsAddon in running hook: %s", e)

    def done(self) -> None:
        """Called by mitmproxy when shutting down."""
        try:
            self.stop()
        except Exception as e:
            logger.error("Error stopping CtxinsAddon in done hook: %s", e)

    def _rewrite_gateway_request(
        self, flow: Any, provider: Provider, target_env: Optional[str] = None
    ) -> None:
        """Rewrite destination when acting as a transparent reverse-proxy gateway."""
        req = getattr(flow, "request", None)
        if req is None:
            return

        headers = getattr(req, "headers", {})
        target = headers.get("x-ctxins-target") or target_env
        if target:
            if "://" not in target:
                target = f"http://{target}"
            parsed = urllib.parse.urlsplit(target)
            if hasattr(req, "scheme"):
                req.scheme = parsed.scheme or "http"
            req.host = parsed.hostname or "127.0.0.1"
            req.port = parsed.port or (443 if getattr(req, "scheme", "http") == "https" else 80)
            if hasattr(req, "headers"):
                req.headers["host"] = req.host if not parsed.port else f"{req.host}:{req.port}"
        else:
            if provider == Provider.ANTHROPIC:
                if hasattr(req, "scheme"):
                    req.scheme = "https"
                req.host = "api.anthropic.com"
                req.port = 443
                if hasattr(req, "headers"):
                    req.headers["host"] = "api.anthropic.com"
            elif provider in (Provider.OPENAI, Provider.AZURE_OPENAI, Provider.OPENROUTER):
                if hasattr(req, "scheme"):
                    req.scheme = "https"
                req.host = "api.openai.com"
                req.port = 443
                if hasattr(req, "headers"):
                    req.headers["host"] = "api.openai.com"
            elif provider == Provider.OLLAMA:
                if hasattr(req, "scheme"):
                    req.scheme = "http"
                req.host = "127.0.0.1"
                req.port = 11434
                if hasattr(req, "headers"):
                    req.headers["host"] = "127.0.0.1:11434"

    def requestheaders(self, flow: Any) -> None:
        """Hook called when request headers are received."""
        try:
            req = getattr(flow, "request", None)
            if req is None:
                return

            host = getattr(req, "pretty_host", "") or getattr(req, "host", "")
            path = getattr(req, "path", "")
            port = getattr(req, "port", None)

            # Check if this is a direct gateway request to ctxins proxy itself
            proxy_port = int(os.environ.get("CTXINS_PROXY_PORT", "8080"))
            is_gateway = (
                host.lower() in ("localhost", "127.0.0.1") and port == proxy_port
            )
            target_env = os.environ.get("CTXINS_TARGET")

            is_match, provider = self.router.match(host, path, port)
            if not is_match and is_gateway and target_env:
                parsed_target = urllib.parse.urlsplit(
                    target_env if "://" in target_env else f"http://{target_env}"
                )
                is_match, provider = self.router.match(parsed_target.netloc, path)

            if not is_match:
                logger.debug("Proxy pass-through non-LLM request: host=%s path=%s", host, path)
                return

            if is_gateway or target_env:
                self._rewrite_gateway_request(flow, provider, target_env)

            client_conn = getattr(flow, "client_conn", None)
            peer = getattr(client_conn, "peername", None)
            client_ip, client_port = (peer[0], peer[1]) if (peer and len(peer) >= 2) else ("127.0.0.1", 0)
            raw_headers = dict(req.headers) if hasattr(req, "headers") else {}

            # Identify client process FIRST before intercepting traffic
            agent_identity = self.process_detector.identify_client(
                client_ip, client_port, headers=raw_headers
            )

            session_id, correlation_id = self._extract_ids(flow)
            logger.info("Intercepting %s LLM request: %s %s (session=%s)", provider.value, host, path, session_id)

            # Format default session_id with detected agent name and pid
            if session_id == self.default_session_id and agent_identity.is_known:
                session_id = f"sess_{agent_identity.name}_{agent_identity.pid or uuid.uuid4().hex[:6]}"

            # Associate client connection with session
            if client_port > 0:
                peer_tuple = (client_ip, client_port)
                self._client_to_session[peer_tuple] = session_id
                self._session_to_clients.setdefault(session_id, set()).add(peer_tuple)
                self._session_agents[session_id] = agent_identity

            if not hasattr(flow, "metadata"):
                flow.metadata = {}
            flow.metadata["ctxins_intercepted"] = True
            flow.metadata["ctxins_correlation_id"] = correlation_id
            flow.metadata["ctxins_provider"] = provider
            flow.metadata["ctxins_agent"] = agent_identity

            timing = TimingMetrics(request_dispatched_at=time.monotonic())
            sanitized = self.sanitizer.sanitize_headers(raw_headers)
            client_meta = self._extract_client_metadata(flow)
            client_meta["agent"] = agent_identity.to_dict()
            client_meta["harness"] = agent_identity.name
            if agent_identity.process_info:
                client_meta["process"] = agent_identity.process_info.to_dict()

            turn = ActiveTurnContext(
                correlation_id=correlation_id,
                session_id=session_id,
                provider=provider,
                model="unknown",
                timing=timing,
                endpoint=path,
                client_metadata=client_meta,
                sanitized_headers=sanitized,
                request_payload={},
            )
            self.tracker.register(turn)
        except Exception as e:
            logger.error("Error in CtxinsAddon.requestheaders: %s", e, exc_info=True)

    def request(self, flow: Any) -> None:
        """Hook called when complete request including payload is available."""
        try:
            metadata = getattr(flow, "metadata", {})
            if not metadata.get("ctxins_intercepted"):
                return

            corr_id = metadata.get("ctxins_correlation_id")
            if not isinstance(corr_id, str):
                return
            turn = self.tracker.get(corr_id)
            if turn is None:
                return

            req = getattr(flow, "request", None)
            payload_dict: Dict[str, Any] = {}
            if req is not None and hasattr(req, "content") and req.content:
                try:
                    raw_json = json.loads(req.content.decode("utf-8", errors="replace"))
                    if isinstance(raw_json, dict):
                        payload_dict = self.sanitizer.sanitize_payload(raw_json)
                    else:
                        payload_dict = {"_data": raw_json}
                except Exception:
                    payload_dict = {"_raw": req.text if hasattr(req, "text") else ""}

            model = self._extract_model(
                turn.provider,
                getattr(req, "path", ""),
                payload_dict,
            )
            turn.model = model
            turn.request_payload = payload_dict

            # Check if session ID can be resolved or switched from body, headers, or message history
            extracted_sid, _ = self._extract_ids(flow, body=payload_dict)
            agent_identity = metadata.get("ctxins_agent")
            pid = agent_identity.pid if (agent_identity and agent_identity.pid) else None
            harness_name = agent_identity.name if (agent_identity and agent_identity.is_known) else "agent"

            if extracted_sid and extracted_sid != self.default_session_id:
                # Explicit session ID present in headers or request body
                computed_sid = extracted_sid
                if pid:
                    self._pid_active_session[pid] = computed_sid
            elif pid:
                # No explicit session ID; inspect conversation messages to detect session switches within this process
                messages = []
                if "messages" in payload_dict and isinstance(payload_dict["messages"], list):
                    messages = [m for m in payload_dict["messages"] if isinstance(m, dict)]
                elif "contents" in payload_dict and isinstance(payload_dict["contents"], list):
                    messages = [m for m in payload_dict["contents"] if isinstance(m, dict)]
                elif "request" in payload_dict and isinstance(payload_dict["request"], dict):
                    req_inner = payload_dict["request"]
                    if "messages" in req_inner and isinstance(req_inner["messages"], list):
                        messages = [m for m in req_inner["messages"] if isinstance(m, dict)]
                    elif "contents" in req_inner and isinstance(req_inner["contents"], list):
                        messages = [m for m in req_inner["contents"] if isinstance(m, dict)]

                first_content = ""
                if messages:
                    first_msg = messages[0]
                    first_content = str(first_msg.get("content", first_msg.get("parts", "")))[:200]
                first_hash = hashlib.sha256(first_content.encode("utf-8")).hexdigest()[:8] if first_content else ""
                msg_count = len(messages)

                curr_sid = self._pid_active_session.get(pid)
                prev_first_hash = self._pid_last_first_msg_hash.get(pid)
                prev_count = self._pid_last_history_len.get(pid, 0)

                is_new_session = False
                if curr_sid is not None:
                    # If first message content hash changed, or message count dropped back to initial turn
                    if prev_first_hash and first_hash and prev_first_hash != first_hash:
                        is_new_session = True
                    elif msg_count <= 1 and prev_count >= 2:
                        is_new_session = True

                if is_new_session or curr_sid is None:
                    counter = self._pid_session_counter.get(pid, 0) + 1
                    self._pid_session_counter[pid] = counter
                    counter_suffix = f"_{counter}" if counter > 1 else ""
                    computed_sid = f"sess_{harness_name}_{pid}{counter_suffix}"
                    self._pid_active_session[pid] = computed_sid
                else:
                    computed_sid = curr_sid

                self._pid_last_history_len[pid] = msg_count
                if first_hash:
                    self._pid_last_first_msg_hash[pid] = first_hash
            elif agent_identity and agent_identity.is_known:
                computed_sid = f"sess_{agent_identity.name}_{agent_identity.pid or uuid.uuid4().hex[:6]}"
            else:
                computed_sid = turn.session_id or self.default_session_id

            turn.session_id = computed_sid

            # Update client and agent connection mappings for this session
            client_conn = getattr(flow, "client_conn", None)
            peer = getattr(client_conn, "peername", None)
            if peer and len(peer) >= 2:
                peer_tuple = (peer[0], peer[1])
                self._client_to_session[peer_tuple] = computed_sid
                self._session_to_clients.setdefault(computed_sid, set()).add(peer_tuple)
            if agent_identity:
                self._session_agents[computed_sid] = agent_identity

            if req is not None and hasattr(req, "headers"):
                turn.sanitized_headers = self.sanitizer.sanitize_headers(dict(req.headers))

            agent_identity = metadata.get("ctxins_agent")
            init_envelope = WireEnvelope(
                event_type=WireEventType.REQUEST_INITIATED,
                correlation_id=turn.correlation_id,
                session_id=turn.session_id,
                timestamp=time.time(),
                payload={
                    "provider": turn.provider.value,
                    "model": turn.model,
                    "endpoint": turn.endpoint,
                    "sanitized_headers": turn.sanitized_headers,
                    "request_payload": turn.request_payload,
                    "timing": turn.timing.to_dict() if turn.timing is not None else None,
                    "client_metadata": turn.client_metadata,
                    "harness": agent_identity.name if agent_identity else turn.client_metadata.get("harness", "unknown"),
                    "agent": agent_identity.to_dict() if agent_identity else turn.client_metadata.get("agent"),
                },
            )
            self.emit_envelope(init_envelope)
        except Exception as e:
            logger.error("Error in CtxinsAddon.request: %s", e, exc_info=True)

    def responseheaders(self, flow: Any) -> None:
        """Hook called when response headers arrive from upstream."""
        try:
            metadata = getattr(flow, "metadata", {})
            if not metadata.get("ctxins_intercepted"):
                return

            corr_id = metadata.get("ctxins_correlation_id")
            if not isinstance(corr_id, str):
                return
            turn = self.tracker.get(corr_id)
            if turn is None:
                return

            resp = getattr(flow, "response", None)
            if resp is not None:
                raw_resp_headers = dict(resp.headers) if hasattr(resp, "headers") else {}
                turn.response_headers = self.sanitizer.sanitize_headers(raw_resp_headers)
                turn.response_status_code = getattr(resp, "status_code", None)

            if self._is_streaming_response(flow):
                accumulator = self._create_accumulator(turn.provider)
                turn.accumulator = accumulator
                self.passthrough.hook_stream(flow, turn)
        except Exception as e:
            logger.error("Error in CtxinsAddon.responseheaders: %s", e, exc_info=True)

    def response(self, flow: Any) -> None:
        """Hook called when response stream or body is fully transferred."""
        try:
            metadata = getattr(flow, "metadata", {})
            if not metadata.get("ctxins_intercepted"):
                return

            corr_id = metadata.get("ctxins_correlation_id")
            if not isinstance(corr_id, str):
                return
            turn = self.tracker.get(corr_id)
            if turn is None:
                return

            resp = getattr(flow, "response", None)
            if resp is not None:
                stream_obj = getattr(resp, "stream", None)
                if stream_obj is not None:
                    close_fn = getattr(stream_obj, "close", None)
                    if callable(close_fn):
                        try:
                            close_fn()
                        except Exception:
                            pass

            # Drain any buffered chunks from StreamPassthrough into the tracker
            self.drain_chunk_queue()

            resp = getattr(flow, "response", None)
            status_code = getattr(resp, "status_code", 200) if resp is not None else 200
            turn.response_status_code = status_code

            # Handle HTTP errors (status code >= 400)
            if status_code >= 400:
                err_payload = self._parse_error_body(flow)
                err_msg = (
                    err_payload.get("error", {}).get("message")
                    if isinstance(err_payload.get("error"), dict)
                    else f"HTTP error {status_code}"
                )
                envelope = self.tracker.abort_turn(
                    correlation_id=corr_id,
                    reason="HTTP_ERROR",
                    error_message=err_msg,
                    http_status=status_code,
                )
                if envelope is not None and err_payload:
                    envelope.payload["error"] = err_payload
                return

            blocks: List[ContentBlock] = []
            usage = UsageMetrics()
            stop_reason: Optional[str] = None
            response_payload: Optional[Dict[str, Any]] = None

            if turn.accumulator is not None:
                # Flush accumulator
                turn.accumulator.feed_chunk(b"")
                blocks = turn.accumulator.get_content_blocks()
                usage = turn.accumulator.get_usage()
                stop_reason = turn.accumulator.get_stop_reason()
                response_payload = self._build_synthetic_response(
                    turn, blocks, usage, stop_reason
                )
            else:
                # Non-streaming response body
                now_mono = time.monotonic()
                if turn.timing.first_byte_received_at is None:
                    turn.timing.first_byte_received_at = now_mono
                if turn.timing.stream_closed_at is None:
                    turn.timing.stream_closed_at = now_mono

                if resp is not None and hasattr(resp, "content") and resp.content:
                    try:
                        raw_json = json.loads(resp.content.decode("utf-8", errors="replace"))
                        if isinstance(raw_json, dict):
                            response_payload = self.sanitizer.sanitize_payload(raw_json)
                            usage = self._extract_usage_from_payload(turn.provider, response_payload)
                            stop_reason = self._extract_stop_reason(turn.provider, response_payload)
                        else:
                            response_payload = {"_data": raw_json}
                    except Exception:
                        response_payload = {
                            "_raw": resp.text if hasattr(resp, "text") else ""
                        }

            agent_identity = metadata.get("ctxins_agent")
            completed_envelope = WireEnvelope(
                event_type=WireEventType.TURN_COMPLETED,
                correlation_id=turn.correlation_id,
                session_id=turn.session_id,
                timestamp=time.time(),
                payload={
                    "http_status": status_code,
                    "provider": turn.provider.value,
                    "model": turn.model,
                    "endpoint": turn.endpoint,
                    "sanitized_headers": turn.sanitized_headers,
                    "request_payload": turn.request_payload,
                    "response_headers": turn.response_headers,
                    "response_payload": response_payload,
                    "content_blocks": [b.to_dict() for b in blocks],
                    "usage": usage.to_dict(),
                    "timing": turn.timing.to_dict() if turn.timing is not None else None,
                    "stop_reason": stop_reason,
                    "client_metadata": turn.client_metadata,
                    "harness": agent_identity.name if agent_identity else turn.client_metadata.get("harness", "unknown"),
                    "agent": agent_identity.to_dict() if agent_identity else turn.client_metadata.get("agent"),
                },
            )
            self.emit_envelope(completed_envelope)
            tot_tok = usage.input_tokens + usage.output_tokens
            logger.info(
                "Completed %s turn for session '%s' (tokens=%d, model=%s)",
                turn.provider.value,
                turn.session_id,
                tot_tok,
                turn.model,
            )

            # Free turn context from tracker
            self.tracker.remove(corr_id)
        except Exception as e:
            logger.error("Error in CtxinsAddon.response: %s", e, exc_info=True)

    def error(self, flow: Any) -> None:
        """Hook called when a flow encounters a network, socket, or proxy error."""
        try:
            metadata = getattr(flow, "metadata", {})
            if not metadata.get("ctxins_intercepted"):
                return

            corr_id = metadata.get("ctxins_correlation_id")
            if not isinstance(corr_id, str):
                return

            err_msg = "Unknown flow error"
            flow_err = getattr(flow, "error", None)
            if flow_err is not None:
                err_msg = getattr(flow_err, "msg", str(flow_err))

            lower_msg = err_msg.lower()
            if any(k in lower_msg for k in ("client", "disconnect", "abort", "cancel", "reset")):
                status = "CLIENT_ABORTED"
            else:
                status = "ERROR"

            self.tracker.abort_turn(
                correlation_id=corr_id,
                reason=status,
                error_message=err_msg,
            )
        except Exception as e:
            logger.error("Error in CtxinsAddon.error: %s", e, exc_info=True)

    def client_connected(self, client: Any) -> None:
        """Hook called by mitmproxy when a client connects."""
        try:
            peer = getattr(client, "peername", None)
            if peer and isinstance(peer, (tuple, list)) and len(peer) >= 2:
                client_ip, client_port = peer[0], peer[1]
                agent_identity = self.process_detector.identify_client(client_ip, client_port)
                logger.info(
                    "Client connected from %s:%s - Identified process: %s (PID: %s, Agent: %s)",
                    client_ip,
                    client_port,
                    agent_identity.command,
                    agent_identity.pid,
                    agent_identity.display_name,
                )
        except Exception as e:
            logger.debug("Error in client_connected: %s", e)

    def client_disconnected(self, client: Any) -> None:
        """Hook called by mitmproxy when a client connection drops."""
        try:
            peer = getattr(client, "peername", None)
            if peer and isinstance(peer, (tuple, list)) and len(peer) >= 2:
                peer_tuple = (peer[0], peer[1])
                session_id = self._client_to_session.pop(peer_tuple, None)
                if session_id:
                    client_set = self._session_to_clients.get(session_id)
                    if client_set:
                        client_set.discard(peer_tuple)
                        if len(client_set) == 0:
                            self._session_to_clients.pop(session_id, None)
                            agent_id = self._session_agents.get(session_id)
                            disc_envelope = WireEnvelope(
                                event_type=WireEventType.SESSION_DISCONNECTED,
                                correlation_id=f"disc-{session_id}",
                                session_id=session_id,
                                timestamp=time.time(),
                                payload={
                                    "sessionId": session_id,
                                    "peer": peer_tuple,
                                    "agent": agent_id.to_dict() if agent_id else None,
                                    "reason": "client_disconnected",
                                },
                            )
                            self.emit_envelope(disc_envelope)
                            logger.info(
                                "Session '%s' client disconnected from %s:%s",
                                session_id,
                                peer[0],
                                peer[1],
                            )
        except Exception as e:
            logger.error("Error in CtxinsAddon.client_disconnected: %s", e, exc_info=True)

    def tls_failed_client(self, data: Any) -> None:
        """Hook called when a client TLS handshake fails."""
        try:
            conn = getattr(data, "conn", None) or getattr(data, "client", None)
            peer = getattr(conn, "peername", None) or getattr(conn, "address", None)
            err = getattr(conn, "error", None) or getattr(data, "error", None)
            logger.warning(
                "Client TLS handshake failed for %s (error: %s). Agent may need ~/.mitmproxy/mitmproxy-ca-cert.pem in CA bundle (export SSL_CERT_FILE, REQUESTS_CA_BUNDLE, or NODE_EXTRA_CA_CERTS).",
                peer or "local client",
                err or "unknown TLS error",
            )
        except Exception as e:
            logger.debug("Error in tls_failed_client hook: %s", e)


# Mitmproxy entrypoint
addons = [CtxinsAddon()]
