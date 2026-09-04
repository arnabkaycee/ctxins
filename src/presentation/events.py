"""Presentation UI event models and serializable payloads."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict


class UIEventType(str, Enum):
    """Types of real-time UI presentation events."""

    SESSION_CREATED = "session_created"
    TURN_STARTED = "turn_started"
    TURN_STREAMING = "turn_streaming"
    TURN_COMPLETED = "turn_completed"
    VIOLATION_DETECTED = "violation_detected"
    SESSION_SUMMARY_UPDATED = "session_summary_updated"
    SESSION_ENDED = "session_ended"


@dataclass(slots=True)
class UIEvent:
    """Real-time presentation event envelope dispatched across subscribers."""

    event_type: UIEventType
    session_id: str
    timestamp: float = field(default_factory=time.time)
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize UIEvent into wire dictionary."""
        return {
            "type": self.event_type.value,
            "sessionId": self.session_id,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> UIEvent:
        """Deserialize UIEvent from wire dictionary."""
        raw_type = data.get("type", "")
        try:
            event_type = UIEventType(raw_type)
        except ValueError:
            # Fallback or pass through if string matches enum member
            event_type = UIEventType[raw_type.upper()] if raw_type.upper() in UIEventType.__members__ else UIEventType.TURN_COMPLETED

        return cls(
            event_type=event_type,
            session_id=data.get("sessionId", ""),
            timestamp=float(data.get("timestamp", time.time())),
            payload=dict(data.get("payload", {})),
        )
