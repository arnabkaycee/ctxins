"""Correlation registry and TTL reaper for in-flight LLM turns."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional

from src.schema.wire import ActiveTurnContext, WireEnvelope, WireEventType

logger = logging.getLogger(__name__)


def create_turn_error_envelope(
    turn: ActiveTurnContext,
    status: str = "CLIENT_ABORTED",
    error_message: Optional[str] = None,
    http_status: Optional[int] = None,
) -> WireEnvelope:
    """Build a standard TURN_ERROR wire envelope from an ActiveTurnContext."""
    now = time.time()
    msg = error_message or (
        f"Turn aborted: {status}" if status == "CLIENT_ABORTED" else f"Turn error: {status}"
    )

    payload: Dict[str, Any] = {
        "provider": turn.provider.value,
        "model": turn.model,
        "endpoint": turn.endpoint,
        "status": status,
        "error_message": msg,
        "sanitized_headers": turn.sanitized_headers,
        "request_payload": turn.request_payload,
        "timing": turn.timing.to_dict() if turn.timing is not None else None,
        "client_metadata": turn.client_metadata,
    }

    if http_status is not None:
        payload["http_status"] = http_status
    elif turn.response_status_code is not None:
        payload["http_status"] = turn.response_status_code

    return WireEnvelope(
        event_type=WireEventType.TURN_ERROR,
        correlation_id=turn.correlation_id,
        session_id=turn.session_id,
        timestamp=now,
        payload=payload,
    )


class ActiveTurnTracker:
    """Thread-safe registry for managing active, in-flight turns with TTL reaping.

    Stores ActiveTurnContext keyed by correlation_id. Tracks request/response
    headers, payloads, streaming chunks, and timing metrics. Reaps abandoned
    or orphaned requests (e.g. client disconnects or timeouts) and emits
    corresponding TURN_ERROR wire envelopes.
    """

    def __init__(
        self,
        default_ttl: float = 300.0,
        reaper_interval: float = 10.0,
        on_turn_error: Optional[Callable[[WireEnvelope], Any]] = None,
        auto_start_reaper: bool = False,
    ) -> None:
        """Initialize ActiveTurnTracker.

        Args:
            default_ttl: Seconds after which an unfinished turn is considered abandoned.
            reaper_interval: Interval in seconds between TTL sweep cycles.
            on_turn_error: Optional callback invoked when a turn is aborted or reaped.
            auto_start_reaper: Whether to start the background reaper thread immediately.
        """
        self.default_ttl = default_ttl
        self.reaper_interval = reaper_interval
        self.on_turn_error: Optional[Callable[[WireEnvelope], Any]] = on_turn_error

        self._turns: Dict[str, ActiveTurnContext] = {}
        self._last_active: Dict[str, float] = {}
        self._lock = threading.RLock()

        self._running = False
        self._stop_event = threading.Event()
        self._reaper_thread: Optional[threading.Thread] = None

        if auto_start_reaper:
            self.start_reaper()

    @property
    def is_reaper_running(self) -> bool:
        """Return True if background reaper thread is alive."""
        return (
            self._running
            and self._reaper_thread is not None
            and self._reaper_thread.is_alive()
        )

    def register_turn(self, turn: ActiveTurnContext) -> ActiveTurnContext:
        """Register an active turn context in the tracker."""
        with self._lock:
            self._turns[turn.correlation_id] = turn
            self._last_active[turn.correlation_id] = time.time()
        return turn

    def register(self, turn: ActiveTurnContext) -> ActiveTurnContext:
        """Alias for register_turn."""
        return self.register_turn(turn)

    def get_turn(self, correlation_id: str) -> Optional[ActiveTurnContext]:
        """Retrieve an active turn by correlation_id."""
        with self._lock:
            return self._turns.get(correlation_id)

    def get(
        self, correlation_id: str, default: Optional[ActiveTurnContext] = None
    ) -> Optional[ActiveTurnContext]:
        """Dict-like get for turn context."""
        with self._lock:
            return self._turns.get(correlation_id, default)

    def remove_turn(self, correlation_id: str) -> Optional[ActiveTurnContext]:
        """Remove a turn from the registry and return it if present."""
        with self._lock:
            self._last_active.pop(correlation_id, None)
            return self._turns.pop(correlation_id, None)

    def remove(self, correlation_id: str) -> Optional[ActiveTurnContext]:
        """Alias for remove_turn."""
        return self.remove_turn(correlation_id)

    def __getitem__(self, correlation_id: str) -> ActiveTurnContext:
        with self._lock:
            return self._turns[correlation_id]

    def __setitem__(self, correlation_id: str, turn: ActiveTurnContext) -> None:
        with self._lock:
            self._turns[correlation_id] = turn
            self._last_active[correlation_id] = time.time()

    def __delitem__(self, correlation_id: str) -> None:
        with self._lock:
            self._last_active.pop(correlation_id, None)
            del self._turns[correlation_id]

    def __contains__(self, correlation_id: str) -> bool:
        with self._lock:
            return correlation_id in self._turns

    def __len__(self) -> int:
        with self._lock:
            return len(self._turns)

    def __iter__(self) -> Iterator[str]:
        with self._lock:
            return iter(list(self._turns.keys()))

    def keys(self) -> List[str]:
        with self._lock:
            return list(self._turns.keys())

    def values(self) -> List[ActiveTurnContext]:
        with self._lock:
            return list(self._turns.values())

    def items(self) -> List[tuple[str, ActiveTurnContext]]:
        with self._lock:
            return list(self._turns.items())

    def update_request_headers(
        self, correlation_id: str, headers: Mapping[str, str]
    ) -> None:
        """Update sanitized request headers for an active turn."""
        with self._lock:
            turn = self._turns.get(correlation_id)
            if turn is not None:
                turn.sanitized_headers.update(headers)
                self._last_active[correlation_id] = time.time()

    def update_request_payload(
        self, correlation_id: str, payload: Dict[str, Any]
    ) -> None:
        """Update request payload for an active turn."""
        with self._lock:
            turn = self._turns.get(correlation_id)
            if turn is not None:
                turn.request_payload = payload
                self._last_active[correlation_id] = time.time()

    def update_response_headers(
        self, correlation_id: str, headers: Mapping[str, str]
    ) -> None:
        """Update response headers for an active turn."""
        with self._lock:
            turn = self._turns.get(correlation_id)
            if turn is not None:
                turn.response_headers.update(headers)
                self._last_active[correlation_id] = time.time()

    def update_response_payload(
        self, correlation_id: str, payload: Dict[str, Any]
    ) -> None:
        """Update response payload for an active turn."""
        with self._lock:
            turn = self._turns.get(correlation_id)
            if turn is not None:
                turn.response_payload = payload
                self._last_active[correlation_id] = time.time()

    def update_status_code(self, correlation_id: str, status_code: int) -> None:
        """Update response HTTP status code for an active turn."""
        with self._lock:
            turn = self._turns.get(correlation_id)
            if turn is not None:
                turn.response_status_code = status_code
                self._last_active[correlation_id] = time.time()

    def record_chunk(
        self, correlation_id: str, chunk: bytes, timestamp: Optional[float] = None
    ) -> bool:
        """Feed a streaming chunk to the active turn and its accumulator.

        Updates TTFT timing on the first non-empty chunk, and marks stream close
        on EOF sentinel (b"").

        Args:
            correlation_id: Unique identifier for the turn.
            chunk: Raw byte chunk.
            timestamp: Optional monotonic timestamp of chunk arrival.

        Returns:
            True if turn was found and chunk recorded, False otherwise.
        """
        with self._lock:
            turn = self._turns.get(correlation_id)
            if turn is None:
                return False

            now_mono = timestamp if timestamp is not None else time.monotonic()
            self._last_active[correlation_id] = time.time()

            # Record TTFT on first non-empty chunk
            if turn.timing is not None:
                if turn.timing.first_byte_received_at is None and len(chunk.strip()) > 0:
                    turn.timing.first_byte_received_at = now_mono

                if chunk == b"":
                    turn.timing.stream_closed_at = now_mono

            # Feed to accumulator if attached
            if turn.accumulator is not None:
                try:
                    turn.accumulator.feed_chunk(chunk)
                except Exception as e:
                    logger.warning(
                        "Error feeding chunk to accumulator for %s: %s",
                        correlation_id,
                        e,
                    )

            return True

    def abort_turn(
        self,
        correlation_id: str,
        reason: str = "CLIENT_ABORTED",
        error_message: Optional[str] = None,
        http_status: Optional[int] = None,
    ) -> Optional[WireEnvelope]:
        """Abort an active turn, generate a TURN_ERROR envelope, and free turn state.

        Args:
            correlation_id: ID of the turn to abort.
            reason: Abort reason code (default "CLIENT_ABORTED").
            error_message: Human-readable error description.
            http_status: Optional HTTP status code associated with error.

        Returns:
            Generated WireEnvelope, or None if turn was not found.
        """
        with self._lock:
            turn = self.remove_turn(correlation_id)
            if turn is None:
                return None

            envelope = create_turn_error_envelope(
                turn,
                status=reason,
                error_message=error_message,
                http_status=http_status,
            )

        if self.on_turn_error is not None:
            try:
                self.on_turn_error(envelope)
            except Exception as e:
                logger.error("Error executing on_turn_error callback: %s", e)

        return envelope

    def reap_expired(
        self, ttl: Optional[float] = None, current_time: Optional[float] = None
    ) -> List[WireEnvelope]:
        """Reap active turns exceeding TTL and emit TURN_ERROR wire envelopes.

        Args:
            ttl: Custom TTL in seconds. Defaults to self.default_ttl.
            current_time: Reference wall clock time (time.time()). Defaults to now.

        Returns:
            List of generated TURN_ERROR WireEnvelope instances for all reaped turns.
        """
        effective_ttl = ttl if ttl is not None else self.default_ttl
        now = current_time if current_time is not None else time.time()
        expired_turns: List[ActiveTurnContext] = []

        with self._lock:
            for corr_id, turn in list(self._turns.items()):
                created_at = getattr(turn, "created_at_wall", None) or self._last_active.get(
                    corr_id, now
                )
                if (now - created_at) > effective_ttl:
                    self._last_active.pop(corr_id, None)
                    self._turns.pop(corr_id, None)
                    expired_turns.append(turn)

        envelopes: List[WireEnvelope] = []
        for turn in expired_turns:
            envelope = create_turn_error_envelope(
                turn,
                status="TIMEOUT",
                error_message=f"Turn expired after {effective_ttl:.1f}s TTL",
            )
            envelopes.append(envelope)
            if self.on_turn_error is not None:
                try:
                    self.on_turn_error(envelope)
                except Exception as e:
                    logger.error("Error executing on_turn_error callback on reap: %s", e)

        if envelopes:
            logger.info("Reaped %d expired turns (TTL=%.1fs)", len(envelopes), effective_ttl)

        return envelopes

    def start_reaper(self) -> None:
        """Start the background daemon thread for periodic TTL sweeps."""
        with self._lock:
            if self.is_reaper_running:
                return

            self._running = True
            self._stop_event.clear()
            self._reaper_thread = threading.Thread(
                target=self._reaper_loop,
                daemon=True,
                name="ActiveTurnTrackerReaper",
            )
            self._reaper_thread.start()

    def stop_reaper(self, timeout: float = 2.0) -> None:
        """Stop background reaper thread."""
        self._running = False
        self._stop_event.set()

        thread = self._reaper_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
            self._reaper_thread = None

    def _reaper_loop(self) -> None:
        """Background loop executing reap_expired at configured intervals."""
        while self._running and not self._stop_event.is_set():
            try:
                self.reap_expired()
            except Exception as e:
                logger.error("Error in TTL reaper loop: %s", e)

            self._stop_event.wait(self.reaper_interval)

    def clear(self) -> None:
        """Clear all active turns without firing error callbacks."""
        with self._lock:
            self._turns.clear()
            self._last_active.clear()

    def __enter__(self) -> "ActiveTurnTracker":
        self.start_reaper()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop_reaper()


# Alias CorrelationTracker to ActiveTurnTracker
CorrelationTracker = ActiveTurnTracker
