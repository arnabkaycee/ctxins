# Low-Level Design (LLD): Interceptor Component

## 1. Module Structure & Package Layout

The Interceptor is built as a high-performance `mitmproxy` addon with an asynchronous, non-blocking UDS egress worker.

```text
interceptor/
├── __init__.py
├── addon.py                  # Entrypoint: Mitmproxy lifecycle hooks
├── config.py                 # Configuration (paths, timeouts, buffers)
├── filter/
│   ├── __init__.py
│   ├── provider_router.py    # URL/SNI routing & endpoint recognition
│   └── sanitizer.py          # Header & payload redaction engine
├── stream/
│   ├── __init__.py
│   ├── passthrough.py        # Zero-copy chunk forwarder & tee
│   ├── sse_parser.py         # Raw SSE line/event chunk tokenizer
│   └── accumulators/
│       ├── base.py           # Abstract stream accumulator
│       ├── anthropic.py      # Anthropic Messages SSE state machine
│       ├── openai.py         # OpenAI Chat Completion SSE state machine
│       └── gemini.py         # Gemini GenerateContent SSE state machine
├── correlation/
│   ├── __init__.py
│   └── tracker.py            # Correlation registry & TTL reaper
└── egress/
    ├── __init__.py
    ├── framing.py            # 4-byte big-endian framing & serialization
    ├── ring_buffer.py        # Thread-safe / async bounded ring buffer
    └── uds_client.py         # Non-blocking Unix Domain Socket client
```

---

## 2. Type Definitions & Data Structures

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
import time

class Provider(str, Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GEMINI = "gemini"
    AZURE_OPENAI = "azure_openai"
    OPENROUTER = "openrouter"
    OLLAMA = "ollama"
    UNKNOWN = "unknown"

class WireEventType(str, Enum):
    REQUEST_INITIATED = "REQUEST_INITIATED"
    TURN_COMPLETED = "TURN_COMPLETED"
    TURN_ERROR = "TURN_ERROR"
    SYSTEM_TELEMETRY = "SYSTEM_TELEMETRY"

@dataclass(slots=True)
class TimingMetrics:
    request_dispatched_at: float           # T0 (monotonic seconds)
    first_byte_received_at: Optional[float] = None  # T_first (monotonic seconds)
    stream_closed_at: Optional[float] = None        # T_end (monotonic seconds)

    @property
    def ttft_ms(self) -> Optional[float]:
        if self.first_byte_received_at:
            return (self.first_byte_received_at - self.request_dispatched_at) * 1000.0
        return None

    @property
    def total_duration_ms(self) -> Optional[float]:
        if self.stream_closed_at:
            return (self.stream_closed_at - self.request_dispatched_at) * 1000.0
        return None

@dataclass(slots=True)
class UsageMetrics:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    reasoning_tokens: int = 0

@dataclass(slots=True)
class ContentBlock:
    index: int
    block_type: str                         # "text" | "tool_use" | "thinking"
    text: Optional[str] = None
    tool_id: Optional[str] = None
    tool_name: Optional[str] = None
    partial_json: Optional[str] = None
    parsed_input: Optional[Dict[str, Any]] = None

@dataclass(slots=True)
class ActiveTurnContext:
    correlation_id: str
    session_id: str
    provider: Provider
    model: str
    timing: TimingMetrics
    endpoint: str
    client_metadata: Dict[str, Any]
    sanitized_headers: Dict[str, str]
    request_payload: Dict[str, Any]
    accumulator: Optional["BaseAccumulator"] = None
    created_at_wall: float = field(default_factory=time.time)
```

---

## 3. Detailed Component Implementations

### Component 1: `ProviderRouter` & `HeaderSanitizer`

```python
import re
from typing import Tuple

class ProviderRouter:
    """Matches incoming requests against recognized LLM provider hosts and paths."""
    
    # Fast compiled routing patterns
    ROUTES = [
        (re.compile(r"^api\.anthropic\.com$"), re.compile(r"^/v1/messages"), Provider.ANTHROPIC),
        (re.compile(r"^api\.openai\.com$"), re.compile(r"^/v1/(chat/completions|responses)"), Provider.OPENAI),
        (re.compile(r"^generativelanguage\.googleapis\.com$"), re.compile(r"^/v1beta/models/.*:(generateContent|streamGenerateContent)"), Provider.GEMINI),
        (re.compile(r".*\.openai\.azure\.com$"), re.compile(r"^/openai/deployments/.*/chat/completions"), Provider.AZURE_OPENAI),
        (re.compile(r"^openrouter\.ai$"), re.compile(r"^/api/v1/chat/completions"), Provider.OPENROUTER),
        (re.compile(r"^(localhost|127\.0\.0\.1):11434$"), re.compile(r"^/(api/chat|v1/chat/completions)"), Provider.OLLAMA),
    ]

    def match(self, host: str, path: str) -> Tuple[bool, Provider]:
        for host_regex, path_regex, provider in self.ROUTES:
            if host_regex.match(host) and path_regex.match(path):
                return True, provider
        return False, Provider.UNKNOWN


class HeaderSanitizer:
    """Redacts authentication keys while keeping diagnostic headers."""
    
    SENSITIVE_HEADERS = {
        "authorization", "x-api-key", "api-key", 
        "proxy-authorization", "cookie", "set-cookie"
    }

    def sanitize_headers(self, headers: Dict[str, str]) -> Dict[str, str]:
        sanitized = {}
        for k, v in headers.items():
            lower_k = k.lower()
            if lower_k in self.SENSITIVE_HEADERS:
                sanitized[k] = "[REDACTED]"
            else:
                sanitized[k] = v
        return sanitized
```

---

### Component 2: Zero-Buffering Passthrough & Async Tee

In `mitmproxy`, response streaming is intercepted using `responseheaders` and `response_stream`:

```python
from mitmproxy import http
import asyncio

class StreamPassthrough:
    """
    Hooks into mitmproxy's chunk generator to tap SSE tokens without delaying delivery.
    """
    def __init__(self, queue: asyncio.Queue):
        self.queue = queue

    def hook_stream(self, flow: http.HTTPFlow, turn: ActiveTurnContext):
        # Turn on mitmproxy streaming passthrough
        flow.response.stream = self._tee_generator(flow.response.stream, turn)

    def _tee_generator(self, upstream_generator, turn: ActiveTurnContext):
        first_chunk = True
        try:
            for chunk in upstream_generator:
                now = time.monotonic()
                if first_chunk and len(chunk.strip()) > 0:
                    turn.timing.first_byte_received_at = now
                    first_chunk = False
                
                # Non-blocking clone into async processing queue
                try:
                    self.queue.put_nowait((turn.correlation_id, chunk, now))
                except asyncio.QueueFull:
                    # Drop chunk from telemetry if queue is saturated; NEVER block proxy stream
                    pass

                # Forward immediately to client
                yield chunk
        finally:
            turn.timing.stream_closed_at = time.monotonic()
            self.queue.put_nowait((turn.correlation_id, b"", turn.timing.stream_closed_at))
```

---

### Component 3: SSE State Machine (`AnthropicAccumulator`)

```python
import json

class AnthropicAccumulator:
    """Reassembles Anthropic SSE chunk stream into canonical turn output."""
    
    def __init__(self, turn: ActiveTurnContext):
        self.turn = turn
        self.blocks: Dict[int, ContentBlock] = {}
        self.stop_reason: Optional[str] = None
        self.usage = UsageMetrics()
        self.buffer = ""

    def feed(self, chunk: bytes) -> Optional[Dict[str, Any]]:
        """Consumes a byte chunk. Returns synthesized response if stream closed."""
        if not chunk:
            return self._finalize()

        self.buffer += chunk.decode("utf-8", errors="replace")
        lines = self.buffer.split("\n")
        # Keep trailing incomplete line in buffer
        self.buffer = lines[-1]

        current_event = None
        for line in lines[:-1]:
            line = line.strip()
            if not line:
                continue
            if line.startswith("event: "):
                current_event = line[7:]
            elif line.startswith("data: "):
                data_str = line[6:]
                if current_event:
                    self._handle_event(current_event, data_str)
                    current_event = None
        return None

    def _handle_event(self, event_type: str, data_str: str):
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            return

        if event_type == "message_start":
            msg = data.get("message", {})
            raw_usage = msg.get("usage", {})
            self.usage.input_tokens = raw_usage.get("input_tokens", 0)
            self.usage.cache_creation_input_tokens = raw_usage.get("cache_creation_input_tokens", 0)
            self.usage.cache_read_input_tokens = raw_usage.get("cache_read_input_tokens", 0)

        elif event_type == "content_block_start":
            idx = data.get("index", 0)
            block = data.get("content_block", {})
            b_type = block.get("type", "text")
            self.blocks[idx] = ContentBlock(
                index=idx,
                block_type=b_type,
                text="" if b_type == "text" else None,
                tool_id=block.get("id"),
                tool_name=block.get("name"),
                partial_json="" if b_type == "tool_use" else None
            )

        elif event_type == "content_block_delta":
            idx = data.get("index", 0)
            delta = data.get("delta", {})
            d_type = delta.get("type")
            if idx in self.blocks:
                if d_type == "text_delta":
                    self.blocks[idx].text += delta.get("text", "")
                elif d_type == "input_json_delta":
                    self.blocks[idx].partial_json += delta.get("partial_json", "")

        elif event_type == "content_block_stop":
            idx = data.get("index", 0)
            if idx in self.blocks and self.blocks[idx].partial_json is not None:
                try:
                    self.blocks[idx].parsed_input = json.loads(self.blocks[idx].partial_json)
                except json.JSONDecodeError:
                    self.blocks[idx].parsed_input = {"_raw": self.blocks[idx].partial_json}

        elif event_type == "message_delta":
            delta = data.get("delta", {})
            self.stop_reason = delta.get("stop_reason")
            delta_usage = data.get("usage", {})
            self.usage.output_tokens = delta_usage.get("output_tokens", 0)

    def _finalize(self) -> Dict[str, Any]:
        return {
            "stop_reason": self.stop_reason,
            "usage": self.usage,
            "blocks": sorted(self.blocks.values(), key=lambda b: b.index)
        }
```

---

### Component 4: UDS Egress Pipeline & Ring Buffer

```python
import socket
import struct
from collections import deque
import threading

class BoundedRingBuffer:
    """Lock-free/thread-safe ring buffer with fail-open drop semantics."""
    
    def __init__(self, capacity: int = 1000):
        self.capacity = capacity
        self.queue = deque(maxlen=capacity)
        self.lock = threading.Lock()
        self.dropped_count = 0

    def push(self, item: bytes) -> bool:
        with self.lock:
            if len(self.queue) == self.capacity:
                self.dropped_count += 1
                # Evicts oldest automatically because of maxlen
            self.queue.append(item)
            return True

    def pop(self) -> Optional[bytes]:
        with self.lock:
            if self.queue:
                return self.queue.popleft()
            return None


class UDSClient:
    """Non-blocking length-prefixed Unix Domain Socket writer."""
    
    def __init__(self, socket_path: str, buffer: BoundedRingBuffer):
        self.socket_path = socket_path
        self.buffer = buffer
        self.sock: Optional[socket.socket] = None
        self.running = True

    def start(self):
        worker = threading.Thread(target=self._egress_loop, daemon=True)
        worker.start()

    def _connect(self) -> bool:
        try:
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.setblocking(False)
            self.sock.connect(self.socket_path)
            return True
        except (socket.error, FileNotFoundError):
            self.sock = None
            return False

    def _egress_loop(self):
        while self.running:
            if not self.sock:
                if not self._connect():
                    time.sleep(0.5)
                    continue

            payload = self.buffer.pop()
            if not payload:
                time.sleep(0.005)
                continue

            # 4-byte length prefix (big-endian uint32) + payload
            frame = struct.pack(">I", len(payload)) + payload
            try:
                # Non-blocking write
                total_sent = 0
                while total_sent < len(frame):
                    sent = self.sock.send(frame[total_sent:])
                    if sent == 0:
                        raise socket.error("Socket broken")
                    total_sent += sent
            except (socket.error, BlockingIOError):
                # Socket disconnected; put frame back or drop to preserve memory
                if self.sock:
                    self.sock.close()
                self.sock = None
                time.sleep(0.1)
```

---

## 4. Concurrency & Failure Recovery Rules

1. **Client Disconnect (`SIGINT` / `Ctrl+C`)**:
   If the client harness drops the connection mid-stream, `mitmproxy` calls `client_disconnect`. The `ActiveTurnTracker` generates a `TURN_ERROR` event with status `"CLIENT_ABORTED"` and frees the turn state.
2. **Listener Process Death**:
   If `ctxins` Core Listener crashes or is restarted, the `UDSClient` catches `EPIPE`/`ECONNRESET`, discards buffered frames once capacity is reached without blocking the proxy, and reconnects automatically when the socket reappears.
