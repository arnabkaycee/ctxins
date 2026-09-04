"""Wire event data structures and provider enums for IPC communication."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


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
    """Monotonic timestamps capturing request lifecycle phases."""

    request_dispatched_at: float  # T0 (monotonic seconds)
    first_byte_received_at: Optional[float] = None  # T_first (monotonic seconds)
    stream_closed_at: Optional[float] = None  # T_end (monotonic seconds)

    @property
    def ttft_ms(self) -> Optional[float]:
        """Time-to-first-token in milliseconds."""
        if self.first_byte_received_at is not None:
            return (self.first_byte_received_at - self.request_dispatched_at) * 1000.0
        return None

    @property
    def total_duration_ms(self) -> Optional[float]:
        """Total duration from dispatch to stream close in milliseconds."""
        if self.stream_closed_at is not None:
            return (self.stream_closed_at - self.request_dispatched_at) * 1000.0
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_dispatched_at": self.request_dispatched_at,
            "first_byte_received_at": self.first_byte_received_at,
            "stream_closed_at": self.stream_closed_at,
            "ttft_ms": self.ttft_ms,
            "total_duration_ms": self.total_duration_ms,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TimingMetrics:
        return cls(
            request_dispatched_at=data["request_dispatched_at"],
            first_byte_received_at=data.get("first_byte_received_at"),
            stream_closed_at=data.get("stream_closed_at"),
        )


@dataclass(slots=True)
class UsageMetrics:
    """Token usage counters across providers."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    reasoning_tokens: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> UsageMetrics:
        return cls(
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            cache_creation_input_tokens=data.get("cache_creation_input_tokens", 0),
            cache_read_input_tokens=data.get("cache_read_input_tokens", 0),
            reasoning_tokens=data.get("reasoning_tokens", 0),
        )


@dataclass(slots=True)
class ContentBlock:
    """Reconstructed content block from streaming or non-streaming responses."""

    index: int
    block_type: str  # "text" | "tool_use" | "thinking"
    text: Optional[str] = None
    tool_id: Optional[str] = None
    tool_name: Optional[str] = None
    partial_json: Optional[str] = None
    parsed_input: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ContentBlock:
        return cls(
            index=data["index"],
            block_type=data["block_type"],
            text=data.get("text"),
            tool_id=data.get("tool_id"),
            tool_name=data.get("tool_name"),
            partial_json=data.get("partial_json"),
            parsed_input=data.get("parsed_input"),
        )


@dataclass(slots=True)
class ActiveTurnContext:
    """In-flight turn metadata maintained by the interceptor."""

    correlation_id: str
    session_id: str
    provider: Provider
    model: str
    timing: TimingMetrics
    endpoint: str
    client_metadata: Dict[str, Any] = field(default_factory=dict)
    sanitized_headers: Dict[str, str] = field(default_factory=dict)
    request_payload: Dict[str, Any] = field(default_factory=dict)
    accumulator: Optional[Any] = None
    created_at_wall: float = field(default_factory=time.time)
    response_headers: Dict[str, str] = field(default_factory=dict)
    response_status_code: Optional[int] = None
    response_payload: Optional[Dict[str, Any]] = None



@dataclass(slots=True)
class WireEnvelope:
    """Top-level frame payload transmitted over Unix Domain Socket IPC."""

    event_type: WireEventType
    correlation_id: str
    session_id: str
    timestamp: float
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "correlation_id": self.correlation_id,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    def to_bytes(self) -> bytes:
        return self.to_json().encode("utf-8")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> WireEnvelope:
        return cls(
            event_type=WireEventType(data["event_type"]),
            correlation_id=data["correlation_id"],
            session_id=data["session_id"],
            timestamp=float(data["timestamp"]),
            payload=data.get("payload", {}),
        )

    @classmethod
    def from_json(cls, text: str) -> WireEnvelope:
        return cls.from_dict(json.loads(text))

    @classmethod
    def from_bytes(cls, raw: bytes) -> WireEnvelope:
        return cls.from_json(raw.decode("utf-8"))
