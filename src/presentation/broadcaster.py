"""Async pub/sub event bus & fan-out engine for presentation subscribers."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Optional, Set

from src.presentation.events import UIEvent

logger = logging.getLogger(__name__)


class PresentationBroadcaster:
    """Thread-safe, non-blocking pub/sub event bus with bounded subscriber queues."""

    def __init__(self, queue_capacity: int = 100) -> None:
        """Initialize broadcaster with default queue capacity for subscribers.

        Args:
            queue_capacity: Maximum unconsumed events queued per subscriber before dropping.
        """
        self.queue_capacity = queue_capacity
        self._subscribers: Set[asyncio.Queue[UIEvent]] = set()
        self._async_lock: Optional[asyncio.Lock] = None
        self._thread_lock = threading.Lock()

    def _get_async_lock(self) -> asyncio.Lock:
        if self._async_lock is None:
            self._async_lock = asyncio.Lock()
        return self._async_lock

    @property
    def subscriber_count(self) -> int:
        """Number of active registered subscribers."""
        with self._thread_lock:
            return len(self._subscribers)

    async def subscribe(self, capacity: Optional[int] = None) -> asyncio.Queue[UIEvent]:
        """Register a new subscriber queue.

        Args:
            capacity: Override queue capacity for this subscriber if specified.

        Returns:
            Bounded asyncio.Queue receiving published UIEvent instances.
        """
        cap = capacity if capacity is not None else self.queue_capacity
        queue: asyncio.Queue[UIEvent] = asyncio.Queue(maxsize=cap)
        async with self._get_async_lock():
            with self._thread_lock:
                self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[UIEvent]) -> None:
        """Unregister an existing subscriber queue.

        Args:
            queue: The subscriber queue to remove.
        """
        async with self._get_async_lock():
            with self._thread_lock:
                self._subscribers.discard(queue)

    def publish_nowait(self, event: UIEvent) -> None:
        """Non-blocking publish. Drops events for slow subscribers to preserve fail-open safety.

        Args:
            event: UIEvent instance to broadcast to all registered queues.
        """
        with self._thread_lock:
            subscribers_snapshot = list(self._subscribers)

        for q in subscribers_snapshot:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Drop frame for laggy subscriber to prevent Core Engine backpressure
                logger.debug(
                    "Subscriber queue full (%d items); dropping presentation event %s",
                    q.maxsize,
                    event.event_type.value,
                )
