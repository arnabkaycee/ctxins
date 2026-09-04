"""Unit tests for UIEvent serialization and PresentationBroadcaster pub/sub."""

from __future__ import annotations

import asyncio

import pytest

from src.presentation.broadcaster import PresentationBroadcaster
from src.presentation.events import UIEvent, UIEventType


def test_ui_event_serialization() -> None:
    """Verify UIEvent to_dict and from_dict serialization roundtrip."""
    event = UIEvent(
        event_type=UIEventType.TURN_COMPLETED,
        session_id="sess_test_123",
        timestamp=1700000000.0,
        payload={"turnIndex": 3, "tokens": 1250, "model": "claude-3-5-sonnet"},
    )

    d = event.to_dict()
    assert d["type"] == "turn_completed"
    assert d["sessionId"] == "sess_test_123"
    assert d["timestamp"] == 1700000000.0
    assert d["payload"]["turnIndex"] == 3

    restored = UIEvent.from_dict(d)
    assert restored.event_type == UIEventType.TURN_COMPLETED
    assert restored.session_id == "sess_test_123"
    assert restored.timestamp == 1700000000.0
    assert restored.payload == event.payload


def test_all_ui_event_types() -> None:
    """Verify all defined UIEventType values."""
    expected_types = {
        "session_created",
        "turn_started",
        "turn_streaming",
        "turn_completed",
        "violation_detected",
        "session_summary_updated",
        "session_ended",
    }
    actual_types = {e.value for e in UIEventType}
    assert expected_types.issubset(actual_types)


@pytest.mark.asyncio
async def test_broadcaster_subscribe_and_fifo_delivery() -> None:
    """Verify subscribers receive events in FIFO order."""
    broadcaster = PresentationBroadcaster(queue_capacity=10)
    assert broadcaster.subscriber_count == 0

    q1 = await broadcaster.subscribe()
    q2 = await broadcaster.subscribe()
    assert broadcaster.subscriber_count == 2

    # Publish 3 events
    events = [
        UIEvent(event_type=UIEventType.SESSION_CREATED, session_id="sess_1"),
        UIEvent(event_type=UIEventType.TURN_STARTED, session_id="sess_1", payload={"turnIndex": 0}),
        UIEvent(event_type=UIEventType.TURN_COMPLETED, session_id="sess_1", payload={"turnIndex": 0}),
    ]

    for ev in events:
        broadcaster.publish_nowait(ev)

    for ev in events:
        item1 = await asyncio.wait_for(q1.get(), timeout=1.0)
        item2 = await asyncio.wait_for(q2.get(), timeout=1.0)
        assert item1 == ev
        assert item2 == ev

    await broadcaster.unsubscribe(q1)
    assert broadcaster.subscriber_count == 1
    await broadcaster.unsubscribe(q2)
    assert broadcaster.subscriber_count == 0


@pytest.mark.asyncio
async def test_broadcaster_graceful_frame_dropping() -> None:
    """Verify broadcaster drops frames gracefully when subscriber queue is saturated."""
    # Queue capacity = 2
    broadcaster = PresentationBroadcaster(queue_capacity=2)
    q_slow = await broadcaster.subscribe(capacity=2)
    q_fast = await broadcaster.subscribe(capacity=10)

    # Publish 5 events
    for i in range(5):
        broadcaster.publish_nowait(
            UIEvent(
                event_type=UIEventType.TURN_STREAMING,
                session_id="sess_1",
                payload={"delta": i},
            )
        )

    # q_slow should contain only 2 items without blocking
    assert q_slow.qsize() == 2
    first = await q_slow.get()
    second = await q_slow.get()
    assert first.payload["delta"] == 0
    assert second.payload["delta"] == 1
    assert q_slow.empty()

    # q_fast had capacity=10, so it received all 5 items
    assert q_fast.qsize() == 5
    for i in range(5):
        item = await q_fast.get()
        assert item.payload["delta"] == i

    await broadcaster.unsubscribe(q_slow)
    await broadcaster.unsubscribe(q_fast)
