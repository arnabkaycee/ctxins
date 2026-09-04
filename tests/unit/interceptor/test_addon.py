"""Unit tests for CtxinsAddon mitmproxy integration."""

import json
from typing import Any

from src.interceptor.addon import CtxinsAddon
from src.interceptor.egress.ring_buffer import BoundedRingBuffer
from src.schema.wire import WireEnvelope, WireEventType


class MockRequest:
    def __init__(
        self,
        host: str = "api.anthropic.com",
        path: str = "/v1/messages",
        port: int = 443,
        method: str = "POST",
        headers: dict[str, str] | None = None,
        content: bytes = b"",
    ):
        self.host = host
        self.pretty_host = host
        self.path = path
        self.port = port
        self.method = method
        self.headers = headers or {}
        self.content = content

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


class MockResponse:
    def __init__(
        self,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        content: bytes = b"",
        stream: Any = None,
    ):
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content
        self.stream = stream

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


class MockClientConn:
    def __init__(self, peername: tuple[str, int] = ("127.0.0.1", 54321)):
        self.peername = peername


class MockFlowError:
    def __init__(self, msg: str = "Client disconnected"):
        self.msg = msg


class MockFlow:
    def __init__(
        self,
        request: MockRequest,
        response: MockResponse | None = None,
        flow_id: str = "flow-test-1",
    ):
        self.id = flow_id
        self.request = request
        self.response = response
        self.metadata: dict[str, Any] = {}
        self.client_conn = MockClientConn()
        self.error: MockFlowError | None = None


class TestCtxinsAddon:
    def test_non_llm_request_bypassed(self):
        buffer = BoundedRingBuffer(100)
        addon = CtxinsAddon(ring_buffer=buffer)

        req = MockRequest(
            host="example.com",
            path="/index.html",
            content=b"hello",
        )
        flow = MockFlow(request=req)

        addon.requestheaders(flow)
        addon.request(flow)

        assert not flow.metadata.get("ctxins_intercepted")
        assert len(buffer) == 0

    def test_anthropic_streaming_lifecycle(self):
        buffer = BoundedRingBuffer(100)
        addon = CtxinsAddon(ring_buffer=buffer, default_session_id="sess-anthropic-1")

        req_payload = {
            "model": "claude-3-5-sonnet-20241022",
            "messages": [{"role": "user", "content": "What is 2+2?"}],
            "stream": True,
        }
        req = MockRequest(
            host="api.anthropic.com",
            path="/v1/messages",
            headers={
                "x-api-key": "sk-ant-secret123",
                "anthropic-version": "2023-06-01",
                "x-correlation-id": "corr-ant-1",
            },
            content=json.dumps(req_payload).encode("utf-8"),
        )
        flow = MockFlow(request=req, flow_id="flow-ant-1")

        # 1. requestheaders hook
        addon.requestheaders(flow)
        assert flow.metadata.get("ctxins_intercepted") is True
        assert flow.metadata.get("ctxins_correlation_id") == "corr-ant-1"
        assert "corr-ant-1" in addon.tracker

        # 2. request hook
        addon.request(flow)
        assert len(buffer) == 1

        init_raw = buffer.pop()
        assert init_raw is not None
        init_envelope = WireEnvelope.from_bytes(init_raw)
        assert init_envelope.event_type == WireEventType.REQUEST_INITIATED
        assert init_envelope.correlation_id == "corr-ant-1"
        assert init_envelope.session_id == "sess-anthropic-1"
        assert init_envelope.payload["sanitized_headers"]["x-api-key"] == "[REDACTED]"
        assert init_envelope.payload["model"] == "claude-3-5-sonnet-20241022"

        # 3. responseheaders hook (streaming SSE)
        resp = MockResponse(
            status_code=200,
            headers={"content-type": "text/event-stream; charset=utf-8"},
        )
        flow.response = resp
        addon.responseheaders(flow)

        # Ensure response stream was hooked by StreamPassthrough
        assert callable(flow.response.stream)

        # 4. Stream chunks through passthrough
        chunks = [
            b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1","type":"message","role":"assistant","usage":{"input_tokens":50,"cache_creation_input_tokens":0,"cache_read_input_tokens":0}}}\n\n',
            b'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n',
            b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"4"}}\n\n',
            b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
            b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":1}}\n\n',
            b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]
        downstream = list(flow.response.stream(chunks))
        assert downstream == chunks

        # 5. response hook
        addon.response(flow)

        # Check completed envelope in buffer
        assert len(buffer) == 1
        comp_raw = buffer.pop()
        assert comp_raw is not None
        comp_envelope = WireEnvelope.from_bytes(comp_raw)
        assert comp_envelope.event_type == WireEventType.TURN_COMPLETED
        assert comp_envelope.correlation_id == "corr-ant-1"
        assert comp_envelope.payload["usage"]["input_tokens"] == 50
        assert comp_envelope.payload["usage"]["output_tokens"] == 1
        assert comp_envelope.payload["stop_reason"] == "end_turn"

        blocks = comp_envelope.payload["content_blocks"]
        assert len(blocks) == 1
        assert blocks[0]["text"] == "4"

        # Turn context must be cleared from tracker
        assert "corr-ant-1" not in addon.tracker

    def test_openai_streaming_tool_call(self):
        buffer = BoundedRingBuffer(100)
        addon = CtxinsAddon(ring_buffer=buffer, default_session_id="sess-oai-1")

        req_payload = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Check weather"}],
            "stream": True,
        }
        req = MockRequest(
            host="api.openai.com",
            path="/v1/chat/completions",
            headers={
                "authorization": "Bearer sk-proj-12345",
                "x-request-id": "req-oai-99",
            },
            content=json.dumps(req_payload).encode("utf-8"),
        )
        flow = MockFlow(request=req, flow_id="flow-oai-1")

        addon.requestheaders(flow)
        addon.request(flow)

        # Pop REQUEST_INITIATED
        init_raw = buffer.pop()
        assert init_raw is not None
        init_env = WireEnvelope.from_bytes(init_raw)
        assert init_env.event_type == WireEventType.REQUEST_INITIATED
        assert init_env.payload["sanitized_headers"]["authorization"] == "[REDACTED]"
        assert init_env.payload["model"] == "gpt-4o"

        # Stream SSE response
        flow.response = MockResponse(
            status_code=200,
            headers={"content-type": "text/event-stream"},
        )
        addon.responseheaders(flow)

        chunks = [
            b'data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{"role":"assistant","content":null,"tool_calls":[{"index":0,"id":"call_123","type":"function","function":{"name":"get_weather","arguments":""}}]}}]}\n\n',
            b'data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"city\\": \\"Paris\\"}"}}]}}]}\n\n',
            b'data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":20,"completion_tokens":15}}\n\n',
            b"data: [DONE]\n\n",
        ]
        list(flow.response.stream(chunks))
        addon.response(flow)

        assert len(buffer) == 1
        comp_env = WireEnvelope.from_bytes(buffer.pop())
        assert comp_env.event_type == WireEventType.TURN_COMPLETED
        assert comp_env.payload["usage"]["input_tokens"] == 20
        assert comp_env.payload["usage"]["output_tokens"] == 15
        assert comp_env.payload["stop_reason"] == "tool_calls"
        assert len(comp_env.payload["content_blocks"]) == 1
        assert comp_env.payload["content_blocks"][0]["tool_name"] == "get_weather"
        assert comp_env.payload["content_blocks"][0]["parsed_input"] == {"city": "Paris"}

    def test_gemini_url_model_extraction_and_streaming(self):
        buffer = BoundedRingBuffer(100)
        addon = CtxinsAddon(ring_buffer=buffer)

        req = MockRequest(
            host="generativelanguage.googleapis.com",
            path="/v1beta/models/gemini-1.5-flash:streamGenerateContent",
            headers={"x-goog-api-key": "[REDACTED]"},
            content=b'{"contents":[{"parts":[{"text":"Hello"}]}]}',
        )
        flow = MockFlow(request=req, flow_id="flow-gem-1")

        addon.requestheaders(flow)
        addon.request(flow)

        init_env = WireEnvelope.from_bytes(buffer.pop())
        assert init_env.event_type == WireEventType.REQUEST_INITIATED
        assert init_env.payload["model"] == "gemini-1.5-flash"
        assert init_env.payload["provider"] == "gemini"

        # Stream response
        flow.response = MockResponse(
            status_code=200,
            headers={"content-type": "text/event-stream"},
        )
        addon.responseheaders(flow)

        chunks = [
            b'data: {"candidates":[{"content":{"parts":[{"text":"Greetings!"}],"role":"model"},"finishReason":"STOP","index":0}],"usageMetadata":{"promptTokenCount":5,"candidatesTokenCount":3}}\n\n'
        ]
        list(flow.response.stream(chunks))
        addon.response(flow)

        comp_env = WireEnvelope.from_bytes(buffer.pop())
        assert comp_env.event_type == WireEventType.TURN_COMPLETED
        assert comp_env.payload["model"] == "gemini-1.5-flash"
        assert comp_env.payload["usage"]["input_tokens"] == 5
        assert comp_env.payload["usage"]["output_tokens"] == 3
        assert comp_env.payload["stop_reason"] == "STOP"

    def test_non_streaming_json_response(self):
        buffer = BoundedRingBuffer(100)
        addon = CtxinsAddon(ring_buffer=buffer)

        req_payload = {
            "model": "claude-3-haiku-20240307",
            "messages": [{"role": "user", "content": "Ping"}],
        }
        req = MockRequest(
            host="api.anthropic.com",
            path="/v1/messages",
            content=json.dumps(req_payload).encode("utf-8"),
        )
        flow = MockFlow(request=req, flow_id="flow-non-stream")

        addon.requestheaders(flow)
        addon.request(flow)
        buffer.pop()  # Drop REQUEST_INITIATED

        resp_payload = {
            "id": "msg_non_stream",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Pong"}],
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 2,
            },
        }
        flow.response = MockResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            content=json.dumps(resp_payload).encode("utf-8"),
        )

        addon.responseheaders(flow)
        addon.response(flow)

        assert len(buffer) == 1
        comp_env = WireEnvelope.from_bytes(buffer.pop())
        assert comp_env.event_type == WireEventType.TURN_COMPLETED
        assert comp_env.payload["usage"]["input_tokens"] == 10
        assert comp_env.payload["usage"]["output_tokens"] == 2
        assert comp_env.payload["response_payload"]["content"][0]["text"] == "Pong"

    def test_client_abort_emits_turn_error(self):
        buffer = BoundedRingBuffer(100)
        addon = CtxinsAddon(ring_buffer=buffer)

        req = MockRequest(
            host="api.anthropic.com",
            path="/v1/messages",
            content=b'{"model":"claude-3-opus"}',
        )
        flow = MockFlow(request=req, flow_id="flow-abort")

        addon.requestheaders(flow)
        addon.request(flow)
        buffer.pop()  # Drop REQUEST_INITIATED

        # Flow aborted due to client disconnect (e.g. SIGINT / Ctrl+C)
        flow.error = MockFlowError("Client disconnected mid-stream")
        addon.error(flow)

        assert len(buffer) == 1
        err_env = WireEnvelope.from_bytes(buffer.pop())
        assert err_env.event_type == WireEventType.TURN_ERROR
        assert err_env.payload["status"] == "CLIENT_ABORTED"
        assert "Client disconnected mid-stream" in err_env.payload["error_message"]
        assert "flow-abort" not in addon.tracker

    def test_upstream_http_error_emits_turn_error(self):
        buffer = BoundedRingBuffer(100)
        addon = CtxinsAddon(ring_buffer=buffer)

        req = MockRequest(
            host="api.openai.com",
            path="/v1/chat/completions",
            content=b'{"model":"gpt-4"}',
        )
        flow = MockFlow(request=req, flow_id="flow-429")

        addon.requestheaders(flow)
        addon.request(flow)
        buffer.pop()  # Drop REQUEST_INITIATED

        err_body = {
            "error": {
                "message": "Rate limit reached for requests",
                "type": "requests",
                "param": None,
                "code": "rate_limit_exceeded",
            }
        }
        flow.response = MockResponse(
            status_code=429,
            headers={"content-type": "application/json"},
            content=json.dumps(err_body).encode("utf-8"),
        )
        addon.responseheaders(flow)
        addon.response(flow)

        assert len(buffer) == 1
        err_env = WireEnvelope.from_bytes(buffer.pop())
        assert err_env.event_type == WireEventType.TURN_ERROR
        assert err_env.payload["http_status"] == 429
        assert "Rate limit reached" in err_env.payload["error_message"]
        assert "flow-429" not in addon.tracker

    def test_fail_open_on_malformed_flow(self):
        buffer = BoundedRingBuffer(100)
        addon = CtxinsAddon(ring_buffer=buffer)

        class BrokenFlow:
            metadata = {}
            request = None

        flow = BrokenFlow()

        # None of these should raise or block
        addon.requestheaders(flow)
        addon.request(flow)
        addon.responseheaders(flow)
        addon.response(flow)
        addon.error(flow)

        assert len(buffer) == 0

    def test_addon_lifecycle_methods(self):
        buffer = BoundedRingBuffer(100)
        addon = CtxinsAddon(ring_buffer=buffer)

        assert not addon.is_running
        addon.running()
        assert addon.is_running
        addon.done()
        assert not addon.is_running

    def test_cloudcode_agy_streaming_lifecycle(self):
        buffer = BoundedRingBuffer(100)
        addon = CtxinsAddon(ring_buffer=buffer)

        req_payload = {
            "model": "gemini-3.1-flash-lite",
            "request": {
                "systemInstruction": {"parts": [{"text": "You are agy."}]},
                "contents": [{"role": "user", "parts": [{"text": "hello"}]}],
                "sessionId": "-4090532296711904797",
            },
        }
        req = MockRequest(
            host="daily-cloudcode-pa.googleapis.com",
            path="/v1internal:streamGenerateContent?alt=json",
            content=json.dumps(req_payload).encode("utf-8"),
        )
        flow = MockFlow(request=req, flow_id="flow-cloudcode-1")

        addon.requestheaders(flow)
        assert flow.metadata.get("ctxins_intercepted") is True
        addon.request(flow)

        assert len(buffer) == 1
        init_env = WireEnvelope.from_bytes(buffer.pop())
        assert init_env.event_type == WireEventType.REQUEST_INITIATED
        assert init_env.session_id == "-4090532296711904797"
        assert init_env.payload["model"] == "gemini-3.1-flash-lite"

        flow.response = MockResponse(
            status_code=200,
            headers={"content-type": "text/event-stream"},
        )
        addon.responseheaders(flow)
        assert flow.response.stream is not None

        chunk = (
            b'data: {"response": {"candidates": [{"content": {"parts": [{"text": "Hi from agy!"}], "role": "model"}, "finishReason": "STOP", "index": 0}], '
            b'"usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 5, "thoughtsTokenCount": 10}}}\n\n'
        )
        list(flow.response.stream([chunk]))
        addon.drain_chunk_queue()
        addon.response(flow)

        assert len(buffer) == 1
        turn_env = WireEnvelope.from_bytes(buffer.pop())
        assert turn_env.event_type == WireEventType.TURN_COMPLETED
        assert turn_env.session_id == "-4090532296711904797"
        assert turn_env.payload["usage"]["input_tokens"] == 100
        assert turn_env.payload["usage"]["output_tokens"] == 5
        assert turn_env.payload["usage"]["reasoning_tokens"] == 10

    def test_cloudcode_agy_streaming_lifecycle_single_bytes_invocations(self):
        """Verify real mitmproxy streaming lifecycle where stream(chunk: bytes) is called directly."""
        buffer = BoundedRingBuffer(100)
        addon = CtxinsAddon(ring_buffer=buffer)

        req_payload = {
            "model": "gemini-3.8-flash",
            "request": {
                "systemInstruction": {"parts": [{"text": "You are agy."}]},
                "contents": [{"role": "user", "parts": [{"text": "hello"}]}],
                "sessionId": "-123456",
            },
        }
        req = MockRequest(
            host="daily-cloudcode-pa.googleapis.com",
            path="/v1internal:streamGenerateContent?alt=sse",
            content=json.dumps(req_payload).encode("utf-8"),
        )
        flow = MockFlow(request=req, flow_id="flow-mitm-prod-1")

        addon.requestheaders(flow)
        addon.request(flow)
        buffer.pop()  # Drop REQUEST_INITIATED

        flow.response = MockResponse(
            status_code=200,
            headers={"content-type": "text/event-stream"},
        )
        addon.responseheaders(flow)

        chunk1 = (
            b'data: {"response": {"candidates": [{"content": {"parts": [{"text": "Part 1"}]}}], '
            b'"usageMetadata": {"promptTokenCount": 50, "candidatesTokenCount": 2}}}\r\n\r\n'
        )
        chunk2 = (
            b'data: {"response": {"candidates": [{"content": {"parts": [{"text": " and 2"}]},"finishReason": "STOP"}], '
            b'"usageMetadata": {"promptTokenCount": 50, "candidatesTokenCount": 4, "thoughtsTokenCount": 8}}}\r\n\r\n'
        )

        # Mitmproxy invokes stream with single bytes per packet
        ret1 = flow.response.stream(chunk1)
        assert ret1 == chunk1
        ret2 = flow.response.stream(chunk2)
        assert ret2 == chunk2

        # Mitmproxy invokes stream with b"" on ResponseEndOfMessage
        ret_eof = flow.response.stream(b"")
        assert ret_eof == b""

        addon.response(flow)

        assert len(buffer) == 1
        turn_env = WireEnvelope.from_bytes(buffer.pop())
        assert turn_env.event_type == WireEventType.TURN_COMPLETED
        assert turn_env.payload["usage"]["input_tokens"] == 50
        assert turn_env.payload["usage"]["output_tokens"] == 4
        assert turn_env.payload["usage"]["reasoning_tokens"] == 8


