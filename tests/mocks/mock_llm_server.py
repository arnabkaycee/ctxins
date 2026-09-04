"""Mock LLM upstream server supporting Anthropic, OpenAI, and Gemini APIs.

Provides both streaming (SSE) and non-streaming HTTP responses for:
- Anthropic: /v1/messages
- OpenAI: /v1/chat/completions
- Gemini: /v1beta/models/...
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional


@dataclass
class ReceivedRequest:
    """Captured incoming HTTP request details."""

    method: str
    path: str
    headers: Dict[str, str]
    body: bytes
    json: Optional[Dict[str, Any]] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class MockResponseConfig:
    """Configuration for a mock response."""

    status_code: int = 200
    headers: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    is_streaming: bool = False
    chunks: List[bytes] = field(default_factory=list)
    chunk_delay_sec: float = 0.0


class MockLLMServer:
    """Lightweight in-process HTTP server mocking Anthropic, OpenAI, and Gemini APIs."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port
        self.server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.requests: List[ReceivedRequest] = []
        self._response_queue: List[MockResponseConfig] = []
        self._handlers: Dict[str, Callable[[ReceivedRequest], Optional[MockResponseConfig]]] = {}
        self._lock = threading.Lock()

    @property
    def base_url(self) -> str:
        """Return base URL of the running server."""
        if not self.server:
            raise RuntimeError("Server not started")
        return f"http://{self.host}:{self.server.server_port}"

    def register_handler(
        self,
        path_pattern: str,
        handler: Callable[[ReceivedRequest], Optional[MockResponseConfig]],
    ) -> None:
        """Register a callback for requests matching a regex path pattern."""
        with self._lock:
            self._handlers[path_pattern] = handler

    def enqueue_response(self, response: MockResponseConfig) -> None:
        """Enqueue a response to be returned on subsequent requests (FIFO)."""
        with self._lock:
            self._response_queue.append(response)

    def start(self) -> None:
        """Start the mock server on a background thread."""
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                # Suppress standard logging to avoid noisy test output
                pass

            def do_GET(self) -> None:
                self._handle("GET")

            def do_POST(self) -> None:
                self._handle("POST")

            def _handle(self, method: str) -> None:
                content_len = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_len) if content_len > 0 else b""
                json_data = None
                if body:
                    try:
                        json_data = json.loads(body.decode("utf-8"))
                    except Exception:
                        json_data = None

                req = ReceivedRequest(
                    method=method,
                    path=self.path,
                    headers=dict(self.headers),
                    body=body,
                    json=json_data,
                )

                with parent._lock:
                    parent.requests.append(req)

                # 1. Check custom handlers first
                matched_config: Optional[MockResponseConfig] = None
                with parent._lock:
                    for pattern, h in parent._handlers.items():
                        if re.search(pattern, self.path):
                            matched_config = h(req)
                            if matched_config is not None:
                                break

                    # 2. Check queued responses
                    if matched_config is None and parent._response_queue:
                        matched_config = parent._response_queue.pop(0)

                # 3. Default to built-in provider mock responses
                if matched_config is None:
                    matched_config = parent._default_response(req)

                # Send response
                self.send_response(matched_config.status_code)
                for k, v in matched_config.headers.items():
                    self.send_header(k, v)
                self.end_headers()

                if matched_config.is_streaming:
                    for chunk in matched_config.chunks:
                        if matched_config.chunk_delay_sec > 0:
                            time.sleep(matched_config.chunk_delay_sec)
                        self.wfile.write(chunk)
                        self.wfile.flush()
                else:
                    if matched_config.body:
                        self.wfile.write(matched_config.body)
                        self.wfile.flush()

        self.server = ThreadingHTTPServer((self.host, self.port), Handler)
        self.port = self.server.server_port
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop and clean up the server."""
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def __enter__(self) -> "MockLLMServer":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()

    def _default_response(self, req: ReceivedRequest) -> MockResponseConfig:
        """Route to provider-specific default responses based on path and payload."""
        req_json = req.json or {}
        is_stream = req_json.get("stream", False) or ":streamGenerateContent" in req.path

        if "/v1/messages" in req.path:
            return (
                self.build_anthropic_stream(
                    text="Hello from mock Anthropic assistant!",
                    input_tokens=req_json.get("max_tokens", 50),
                    output_tokens=10,
                )
                if is_stream
                else self.build_anthropic_response(
                    text="Hello from mock Anthropic assistant!",
                    input_tokens=50,
                    output_tokens=10,
                )
            )

        if "/v1/chat/completions" in req.path:
            return (
                self.build_openai_stream(
                    text="Hello from mock OpenAI assistant!",
                    prompt_tokens=40,
                    completion_tokens=10,
                )
                if is_stream
                else self.build_openai_response(
                    text="Hello from mock OpenAI assistant!",
                    prompt_tokens=40,
                    completion_tokens=10,
                )
            )

        if "/v1beta/models" in req.path:
            return (
                self.build_gemini_stream(
                    text="Hello from mock Gemini model!",
                    prompt_tokens=30,
                    candidate_tokens=10,
                )
                if is_stream
                else self.build_gemini_response(
                    text="Hello from mock Gemini model!",
                    prompt_tokens=30,
                    candidate_tokens=10,
                )
            )

        # Fallback generic JSON
        payload = json.dumps({"status": "ok", "path": req.path}).encode("utf-8")
        return MockResponseConfig(
            status_code=200,
            headers={"Content-Type": "application/json", "Content-Length": str(len(payload))},
            body=payload,
        )

    # -----------------------------------------------------------------------
    # Anthropic Mock Response Builders
    # -----------------------------------------------------------------------

    @staticmethod
    def build_anthropic_response(
        text: str = "Hello from Claude",
        model: str = "claude-3-5-sonnet-20241022",
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        input_tokens: int = 50,
        output_tokens: int = 15,
        stop_reason: Optional[str] = None,
    ) -> MockResponseConfig:
        """Construct non-streaming Anthropic /v1/messages response."""
        content: List[Dict[str, Any]] = []
        if text:
            content.append({"type": "text", "text": text})
        if tool_calls:
            for tc in tool_calls:
                content.append({
                    "type": "tool_use",
                    "id": tc.get("id", "toolu_01"),
                    "name": tc.get("name", "bash"),
                    "input": tc.get("input", {}),
                })

        resolved_stop_reason = stop_reason or ("tool_use" if tool_calls else "end_turn")

        resp = {
            "id": f"msg_{int(time.time()*1000)}",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": content,
            "stop_reason": resolved_stop_reason,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        }
        body = json.dumps(resp).encode("utf-8")
        return MockResponseConfig(
            status_code=200,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Content-Length": str(len(body)),
            },
            body=body,
        )

    @staticmethod
    def build_anthropic_stream(
        text: str = "Hello from Claude",
        model: str = "claude-3-5-sonnet-20241022",
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        input_tokens: int = 50,
        output_tokens: int = 15,
        stop_reason: Optional[str] = None,
        chunk_delay_sec: float = 0.0,
    ) -> MockResponseConfig:
        """Construct streaming SSE Anthropic /v1/messages response chunks."""
        msg_id = f"msg_{int(time.time()*1000)}"
        resolved_stop_reason = stop_reason or ("tool_use" if tool_calls else "end_turn")

        chunks: List[bytes] = [
            f'event: message_start\ndata: {{"type":"message_start","message":{{"id":"{msg_id}","type":"message","role":"assistant","model":"{model}","usage":{{"input_tokens":{input_tokens},"cache_creation_input_tokens":0,"cache_read_input_tokens":0}}}}\n\n'.encode("utf-8")
        ]

        block_index = 0
        if text:
            chunks.append(
                f'event: content_block_start\ndata: {{"type":"content_block_start","index":{block_index},"content_block":{{"type":"text","text":""}}}}\n\n'.encode("utf-8")
            )
            # Split text into 2 chunks to test streaming reassembly
            mid = len(text) // 2
            t1, t2 = text[:mid], text[mid:]
            chunks.append(
                f'event: content_block_delta\ndata: {{"type":"content_block_delta","index":{block_index},"delta":{{"type":"text_delta","text":{json.dumps(t1)}}}}}\n\n'.encode("utf-8")
            )
            chunks.append(
                f'event: content_block_delta\ndata: {{"type":"content_block_delta","index":{block_index},"delta":{{"type":"text_delta","text":{json.dumps(t2)}}}}}\n\n'.encode("utf-8")
            )
            chunks.append(
                f'event: content_block_stop\ndata: {{"type":"content_block_stop","index":{block_index}}}\n\n'.encode("utf-8")
            )
            block_index += 1

        if tool_calls:
            for tc in tool_calls:
                tid = tc.get("id", "toolu_01")
                tname = tc.get("name", "bash")
                tinput = json.dumps(tc.get("input", {}))

                chunks.append(
                    f'event: content_block_start\ndata: {{"type":"content_block_start","index":{block_index},"content_block":{{"type":"tool_use","id":"{tid}","name":"{tname}","input":{{}}}}\n\n'.encode("utf-8")
                )
                chunks.append(
                    f'event: content_block_delta\ndata: {{"type":"content_block_delta","index":{block_index},"delta":{{"type":"input_json_delta","partial_json":{json.dumps(tinput)}}}}}\n\n'.encode("utf-8")
                )
                chunks.append(
                    f'event: content_block_stop\ndata: {{"type":"content_block_stop","index":{block_index}}}\n\n'.encode("utf-8")
                )
                block_index += 1

        chunks.append(
            f'event: message_delta\ndata: {{"type":"message_delta","delta":{{"stop_reason":"{resolved_stop_reason}"}},"usage":{{"output_tokens":{output_tokens}}}}}\n\n'.encode("utf-8")
        )
        chunks.append(b'event: message_stop\ndata: {"type":"message_stop"}\n\n')

        return MockResponseConfig(
            status_code=200,
            headers={
                "Content-Type": "text/event-stream; charset=utf-8",
                "Cache-Control": "no-cache",
                "Connection": "close",
            },
            is_streaming=True,
            chunks=chunks,
            chunk_delay_sec=chunk_delay_sec,
        )

    # -----------------------------------------------------------------------
    # OpenAI Mock Response Builders
    # -----------------------------------------------------------------------

    @staticmethod
    def build_openai_response(
        text: str = "Hello from GPT",
        model: str = "gpt-4o",
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        prompt_tokens: int = 40,
        completion_tokens: int = 15,
        finish_reason: Optional[str] = None,
    ) -> MockResponseConfig:
        """Construct non-streaming OpenAI /v1/chat/completions response."""
        msg: Dict[str, Any] = {"role": "assistant", "content": text if text else None}
        if tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.get("id", "call_01"),
                    "type": "function",
                    "function": {
                        "name": tc.get("name", "read_file"),
                        "arguments": json.dumps(tc.get("input", tc.get("arguments", {}))),
                    },
                }
                for tc in tool_calls
            ]

        resolved_finish = finish_reason or ("tool_calls" if tool_calls else "stop")
        resp = {
            "id": f"chatcmpl-{int(time.time()*1000)}",
            "object": "chat.completion",
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": msg,
                    "finish_reason": resolved_finish,
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }
        body = json.dumps(resp).encode("utf-8")
        return MockResponseConfig(
            status_code=200,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Content-Length": str(len(body)),
            },
            body=body,
        )

    @staticmethod
    def build_openai_stream(
        text: str = "Hello from GPT",
        model: str = "gpt-4o",
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        prompt_tokens: int = 40,
        completion_tokens: int = 15,
        finish_reason: Optional[str] = None,
        chunk_delay_sec: float = 0.0,
    ) -> MockResponseConfig:
        """Construct streaming SSE OpenAI /v1/chat/completions response chunks."""
        cmpl_id = f"chatcmpl-{int(time.time()*1000)}"
        resolved_finish = finish_reason or ("tool_calls" if tool_calls else "stop")
        chunks: List[bytes] = []

        # Role start chunk
        start_chunk = {
            "id": cmpl_id,
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}],
        }
        chunks.append(f"data: {json.dumps(start_chunk)}\n\n".encode("utf-8"))

        if text:
            mid = len(text) // 2
            t1, t2 = text[:mid], text[mid:]
            chunks.append(
                f'data: {json.dumps({"id": cmpl_id, "object": "chat.completion.chunk", "model": model, "choices": [{"index": 0, "delta": {"content": t1}, "finish_reason": None}]})}\n\n'.encode("utf-8")
            )
            chunks.append(
                f'data: {json.dumps({"id": cmpl_id, "object": "chat.completion.chunk", "model": model, "choices": [{"index": 0, "delta": {"content": t2}, "finish_reason": None}]})}\n\n'.encode("utf-8")
            )

        if tool_calls:
            for idx, tc in enumerate(tool_calls):
                call_id = tc.get("id", f"call_{idx}")
                fn_name = tc.get("name", "read_file")
                args_str = json.dumps(tc.get("input", tc.get("arguments", {})))

                # Start tool call chunk
                tc_start = {
                    "id": cmpl_id,
                    "object": "chat.completion.chunk",
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": idx,
                                        "id": call_id,
                                        "type": "function",
                                        "function": {"name": fn_name, "arguments": ""},
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ],
                }
                chunks.append(f"data: {json.dumps(tc_start)}\n\n".encode("utf-8"))

                # Delta arguments chunk
                tc_delta = {
                    "id": cmpl_id,
                    "object": "chat.completion.chunk",
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": idx,
                                        "function": {"arguments": args_str},
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ],
                }
                chunks.append(f"data: {json.dumps(tc_delta)}\n\n".encode("utf-8"))

        # Final chunk with finish reason and usage
        final_chunk = {
            "id": cmpl_id,
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": resolved_finish}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }
        chunks.append(f"data: {json.dumps(final_chunk)}\n\n".encode("utf-8"))
        chunks.append(b"data: [DONE]\n\n")

        return MockResponseConfig(
            status_code=200,
            headers={
                "Content-Type": "text/event-stream; charset=utf-8",
                "Cache-Control": "no-cache",
                "Connection": "close",
            },
            is_streaming=True,
            chunks=chunks,
            chunk_delay_sec=chunk_delay_sec,
        )

    # -----------------------------------------------------------------------
    # Gemini Mock Response Builders
    # -----------------------------------------------------------------------

    @staticmethod
    def build_gemini_response(
        text: str = "Hello from Gemini",
        model: str = "gemini-1.5-pro",
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        prompt_tokens: int = 30,
        candidate_tokens: int = 15,
        finish_reason: str = "STOP",
    ) -> MockResponseConfig:
        """Construct non-streaming Gemini generateContent response."""
        parts: List[Dict[str, Any]] = []
        if text:
            parts.append({"text": text})
        if tool_calls:
            for tc in tool_calls:
                parts.append({
                    "functionCall": {
                        "name": tc.get("name", "query_db"),
                        "args": tc.get("input", tc.get("args", {})),
                    }
                })

        resp = {
            "candidates": [
                {
                    "content": {"parts": parts, "role": "model"},
                    "finishReason": finish_reason,
                    "index": 0,
                }
            ],
            "usageMetadata": {
                "promptTokenCount": prompt_tokens,
                "candidatesTokenCount": candidate_tokens,
                "totalTokenCount": prompt_tokens + candidate_tokens,
            },
        }
        body = json.dumps(resp).encode("utf-8")
        return MockResponseConfig(
            status_code=200,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Content-Length": str(len(body)),
            },
            body=body,
        )

    @staticmethod
    def build_gemini_stream(
        text: str = "Hello from Gemini",
        model: str = "gemini-1.5-pro",
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        prompt_tokens: int = 30,
        candidate_tokens: int = 15,
        finish_reason: str = "STOP",
        chunk_delay_sec: float = 0.0,
    ) -> MockResponseConfig:
        """Construct streaming SSE Gemini streamGenerateContent response chunks."""
        chunks: List[bytes] = []

        if text:
            mid = len(text) // 2
            t1, t2 = text[:mid], text[mid:]
            c1 = {
                "candidates": [{"content": {"parts": [{"text": t1}], "role": "model"}, "index": 0}]
            }
            chunks.append(f"data: {json.dumps(c1)}\n\n".encode("utf-8"))

            c2 = {
                "candidates": [
                    {
                        "content": {"parts": [{"text": t2}], "role": "model"},
                        "finishReason": finish_reason if not tool_calls else None,
                        "index": 0,
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": prompt_tokens,
                    "candidatesTokenCount": candidate_tokens,
                    "totalTokenCount": prompt_tokens + candidate_tokens,
                },
            }
            chunks.append(f"data: {json.dumps(c2)}\n\n".encode("utf-8"))

        if tool_calls:
            for tc in tool_calls:
                c_tool = {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "functionCall": {
                                            "name": tc.get("name", "query_db"),
                                            "args": tc.get("input", tc.get("args", {})),
                                        }
                                    }
                                ],
                                "role": "model",
                            },
                            "finishReason": finish_reason,
                            "index": 0,
                        }
                    ],
                    "usageMetadata": {
                        "promptTokenCount": prompt_tokens,
                        "candidatesTokenCount": candidate_tokens,
                        "totalTokenCount": prompt_tokens + candidate_tokens,
                    },
                }
                chunks.append(f"data: {json.dumps(c_tool)}\n\n".encode("utf-8"))

        return MockResponseConfig(
            status_code=200,
            headers={
                "Content-Type": "text/event-stream; charset=utf-8",
                "Cache-Control": "no-cache",
                "Connection": "close",
            },
            is_streaming=True,
            chunks=chunks,
            chunk_delay_sec=chunk_delay_sec,
        )
