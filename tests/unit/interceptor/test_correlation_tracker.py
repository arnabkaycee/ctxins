"""Unit tests for ActiveTurnTracker and CorrelationTracker."""

import threading
import time
from unittest.mock import MagicMock

from src.interceptor.correlation.tracker import (
    ActiveTurnTracker,
    CorrelationTracker,
    create_turn_error_envelope,
)
from src.schema.wire import (
    ActiveTurnContext,
    Provider,
    TimingMetrics,
    WireEnvelope,
    WireEventType,
)


def create_sample_turn(
    correlation_id: str = "corr-1",
    session_id: str = "sess-1",
    provider: Provider = Provider.ANTHROPIC,
    model: str = "claude-3-5-sonnet-20241022",
    endpoint: str = "/v1/messages",
    created_at_wall: float = 1000.0,
) -> ActiveTurnContext:
    return ActiveTurnContext(
        correlation_id=correlation_id,
        session_id=session_id,
        provider=provider,
        model=model,
        timing=TimingMetrics(request_dispatched_at=time.monotonic()),
        endpoint=endpoint,
        client_metadata={"client": "test-runner"},
        sanitized_headers={"x-api-key": "[REDACTED]"},
        request_payload={"messages": [{"role": "user", "content": "hello"}]},
        created_at_wall=created_at_wall,
    )


class TestActiveTurnTracker:
    def test_alias_equality(self):
        assert CorrelationTracker is ActiveTurnTracker

    def test_register_and_retrieve_turn(self):
        tracker = ActiveTurnTracker()
        turn = create_sample_turn("corr-123")

        assert len(tracker) == 0
        assert "corr-123" not in tracker

        tracker.register_turn(turn)

        assert len(tracker) == 1
        assert "corr-123" in tracker
        assert tracker.get_turn("corr-123") is turn
        assert tracker.get("corr-123") is turn
        assert tracker["corr-123"] is turn
        assert tracker.get("non-existent") is None
        assert tracker.get("non-existent", "default") == "default"

        # Keys, values, items, iteration
        assert tracker.keys() == ["corr-123"]
        assert tracker.values() == [turn]
        assert len(tracker.items()) == 1
        assert list(tracker) == ["corr-123"]

    def test_dict_style_assignment_and_deletion(self):
        tracker = ActiveTurnTracker()
        turn = create_sample_turn("corr-abc")

        tracker["corr-abc"] = turn
        assert len(tracker) == 1
        assert tracker["corr-abc"] is turn

        del tracker["corr-abc"]
        assert len(tracker) == 0
        assert "corr-abc" not in tracker

    def test_remove_turn(self):
        tracker = ActiveTurnTracker()
        turn = create_sample_turn("corr-remove")
        tracker.register_turn(turn)

        removed = tracker.remove_turn("corr-remove")
        assert removed is turn
        assert len(tracker) == 0
        assert tracker.remove_turn("corr-remove") is None

    def test_update_headers_payloads_and_status(self):
        tracker = ActiveTurnTracker()
        turn = create_sample_turn("corr-updates")
        tracker.register_turn(turn)

        tracker.update_request_headers("corr-updates", {"x-custom": "header-val"})
        assert turn.sanitized_headers["x-custom"] == "header-val"

        tracker.update_request_payload("corr-updates", {"model": "claude-3-opus"})
        assert turn.request_payload["model"] == "claude-3-opus"

        tracker.update_response_headers("corr-updates", {"content-type": "application/json"})
        assert turn.response_headers["content-type"] == "application/json"

        tracker.update_response_payload("corr-updates", {"content": [{"text": "done"}]})
        assert turn.response_payload == {"content": [{"text": "done"}]}

        tracker.update_status_code("corr-updates", 200)
        assert turn.response_status_code == 200

    def test_record_chunk_timing_and_accumulator(self):
        tracker = ActiveTurnTracker()
        turn = create_sample_turn("corr-chunk")
        acc_mock = MagicMock()
        turn.accumulator = acc_mock
        tracker.register_turn(turn)

        assert turn.timing.first_byte_received_at is None
        assert turn.timing.stream_closed_at is None

        # Whitespace chunk does not trigger TTFT
        tracker.record_chunk("corr-chunk", b"   \n")
        assert turn.timing.first_byte_received_at is None
        acc_mock.feed_chunk.assert_called_with(b"   \n")

        # Non-empty chunk triggers TTFT
        t0 = time.monotonic()
        tracker.record_chunk("corr-chunk", b"data: hello\n\n", timestamp=t0)
        assert turn.timing.first_byte_received_at == t0
        acc_mock.feed_chunk.assert_called_with(b"data: hello\n\n")

        # EOF sentinel chunk triggers stream_closed_at
        t1 = t0 + 0.5
        tracker.record_chunk("corr-chunk", b"", timestamp=t1)
        assert turn.timing.stream_closed_at == t1
        acc_mock.feed_chunk.assert_called_with(b"")

        # Non-existent correlation_id returns False
        assert tracker.record_chunk("unknown", b"test") is False

    def test_abort_turn_emits_turn_error(self):
        callback_mock = MagicMock()
        tracker = ActiveTurnTracker(on_turn_error=callback_mock)
        turn = create_sample_turn("corr-abort")
        tracker.register_turn(turn)

        envelope = tracker.abort_turn(
            "corr-abort",
            reason="CLIENT_ABORTED",
            error_message="Connection closed by client",
            http_status=499,
        )

        assert envelope is not None
        assert isinstance(envelope, WireEnvelope)
        assert envelope.event_type == WireEventType.TURN_ERROR
        assert envelope.correlation_id == "corr-abort"
        assert envelope.session_id == "sess-1"
        assert envelope.payload["status"] == "CLIENT_ABORTED"
        assert envelope.payload["error_message"] == "Connection closed by client"
        assert envelope.payload["http_status"] == 499
        assert "corr-abort" not in tracker

        callback_mock.assert_called_once_with(envelope)

        # Aborting non-existent returns None
        assert tracker.abort_turn("non-existent") is None

    def test_ttl_reaper_cleans_expired_turns(self):
        errors: list[WireEnvelope] = []
        tracker = ActiveTurnTracker(
            default_ttl=60.0,
            on_turn_error=lambda env: errors.append(env),
        )

        t_now = 2000.0
        # Expired turn (created 100s ago > 60s TTL)
        expired_turn = create_sample_turn("corr-exp", created_at_wall=t_now - 100.0)
        # Active turn (created 10s ago < 60s TTL)
        active_turn = create_sample_turn("corr-act", created_at_wall=t_now - 10.0)

        tracker.register_turn(expired_turn)
        tracker.register_turn(active_turn)
        assert len(tracker) == 2

        reaped = tracker.reap_expired(current_time=t_now)
        assert len(reaped) == 1
        assert reaped[0].correlation_id == "corr-exp"
        assert reaped[0].event_type == WireEventType.TURN_ERROR
        assert reaped[0].payload["status"] == "TIMEOUT"

        assert "corr-exp" not in tracker
        assert "corr-act" in tracker
        assert len(tracker) == 1
        assert len(errors) == 1
        assert errors[0].correlation_id == "corr-exp"

    def test_background_reaper_lifecycle(self):
        tracker = ActiveTurnTracker(default_ttl=0.1, reaper_interval=0.05)
        assert not tracker.is_reaper_running

        with tracker:
            assert tracker.is_reaper_running
            # Register a turn with past timestamp
            turn = create_sample_turn("corr-bg", created_at_wall=time.time() - 1.0)
            tracker.register_turn(turn)

            # Wait briefly for background thread to reap
            time.sleep(0.15)
            assert "corr-bg" not in tracker

        assert not tracker.is_reaper_running

    def test_thread_safe_concurrent_access(self):
        tracker = ActiveTurnTracker()
        n_threads = 8
        ops_per_thread = 50

        def worker(tid: int):
            for i in range(ops_per_thread):
                cid = f"t-{tid}-c-{i}"
                turn = create_sample_turn(cid)
                tracker.register_turn(turn)
                tracker.update_request_headers(cid, {"header": "val"})
                tracker.record_chunk(cid, b"data: chunk\n\n")
                if i % 2 == 0:
                    tracker.remove_turn(cid)
                else:
                    tracker.abort_turn(cid)

        threads = [threading.Thread(target=worker, args=(tid,)) for tid in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All turns were either removed or aborted
        assert len(tracker) == 0

    def test_create_turn_error_envelope_defaults(self):
        turn = create_sample_turn("corr-default-err")
        env = create_turn_error_envelope(turn)
        assert env.event_type == WireEventType.TURN_ERROR
        assert env.payload["status"] == "CLIENT_ABORTED"
        assert "Turn aborted: CLIENT_ABORTED" in env.payload["error_message"]
