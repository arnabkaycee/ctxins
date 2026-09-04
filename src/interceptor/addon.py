"""Mitmproxy addon for LLM network interception, stream tapping, and telemetry egress."""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from src.interceptor.correlation.tracker import ActiveTurnTracker
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

    def _extract_ids(self, flow: Any) -> tuple[str, str]:
        """Extract or generate session_id and correlation_id for flow."""
        headers = flow.request.headers if hasattr(flow, "request") and flow.request else {}

        session_id = (
            headers.get("x-session-id")
            or headers.get("x-ctxins-session-id")
            or headers.get("ctxins-session-id")
            or self.default_session_id
        )

        metadata = getattr(flow, "metadata", {})
        correlation_id = (
            metadata.get("ctxins_correlation_id")
            or headers.get("x-correlation-id")
            or headers.get("x-ctxins-correlation-id")
            or headers.get("x-request-id")
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
        except Exception as e:
            logger.error("Error starting CtxinsAddon in running hook: %s", e)

    def done(self) -> None:
        """Called by mitmproxy when shutting down."""
        try:
            self.stop()
        except Exception as e:
            logger.error("Error stopping CtxinsAddon in done hook: %s", e)

    def requestheaders(self, flow: Any) -> None:
        """Hook called when request headers are received."""
        try:
            req = getattr(flow, "request", None)
            if req is None:
                return

            host = getattr(req, "pretty_host", "") or getattr(req, "host", "")
            path = getattr(req, "path", "")
            port = getattr(req, "port", None)

            is_match, provider = self.router.match(host, path, port)
            if not is_match:
                return

            session_id, correlation_id = self._extract_ids(flow)

            if not hasattr(flow, "metadata"):
                flow.metadata = {}
            flow.metadata["ctxins_intercepted"] = True
            flow.metadata["ctxins_correlation_id"] = correlation_id
            flow.metadata["ctxins_provider"] = provider

            timing = TimingMetrics(request_dispatched_at=time.monotonic())
            raw_headers = dict(req.headers) if hasattr(req, "headers") else {}
            sanitized = self.sanitizer.sanitize_headers(raw_headers)
            client_meta = self._extract_client_metadata(flow)

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

            body_session_id = None
            if isinstance(payload_dict, dict):
                body_session_id = payload_dict.get("sessionId")
                if not body_session_id and "request" in payload_dict and isinstance(payload_dict["request"], dict):
                    body_session_id = payload_dict["request"].get("sessionId")
            if body_session_id and turn.session_id == self.default_session_id:
                turn.session_id = str(body_session_id)

            if req is not None and hasattr(req, "headers"):
                turn.sanitized_headers = self.sanitizer.sanitize_headers(dict(req.headers))

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
                },
            )
            self.emit_envelope(completed_envelope)

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

    def client_disconnect(self, client: Any) -> None:
        """Hook called by mitmproxy when a client connection drops."""
        # Mitmproxy dispatches flow-level error() hooks for active flows on client drop.
        pass


# Mitmproxy entrypoint
addons = [CtxinsAddon()]
