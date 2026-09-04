"""Thread-safe bounded ring buffer with fail-open drop semantics."""

import threading
from collections import deque
from typing import Optional


class BoundedRingBuffer:
    """Thread-safe, non-blocking bounded ring buffer.

    When buffer capacity is reached, new items cause the oldest item
    to be evicted immediately, incrementing `dropped_count` without
    blocking or raising exceptions.
    """

    def __init__(self, capacity: int = 1000):
        """Initialize the ring buffer with a fixed capacity.

        Args:
            capacity: Maximum number of items the buffer can hold. Must be > 0.

        Raises:
            ValueError: If capacity is less than or equal to 0.
        """
        if capacity <= 0:
            raise ValueError(f"Buffer capacity must be positive, got {capacity}")

        self.capacity: int = capacity
        self.queue: deque[bytes] = deque(maxlen=capacity)
        self.lock: threading.Lock = threading.Lock()
        self._dropped_count: int = 0

    @property
    def dropped_count(self) -> int:
        """Total number of items dropped due to buffer saturation."""
        with self.lock:
            return self._dropped_count

    @dropped_count.setter
    def dropped_count(self, value: int) -> None:
        """Set the dropped count (used primarily for resets/testing)."""
        with self.lock:
            self._dropped_count = value

    def push(self, item: bytes) -> bool:
        """Push an item into the buffer.

        If the buffer is at capacity, the oldest item is evicted and
        dropped_count is incremented.

        Args:
            item: Item payload to enqueue.

        Returns:
            Always returns True.
        """
        with self.lock:
            if len(self.queue) == self.capacity:
                self._dropped_count += 1
            # deque with maxlen automatically drops leftmost item when full
            self.queue.append(item)
            return True

    def pop(self) -> Optional[bytes]:
        """Pop the oldest item from the buffer.

        Returns:
            Oldest item in buffer, or None if buffer is empty.
        """
        with self.lock:
            if self.queue:
                return self.queue.popleft()
            return None

    def unpop(self, item: bytes) -> None:
        """Put an unconsumed or failed item back to the front of the buffer.

        If the buffer is already at capacity, the item is dropped to maintain
        capacity limits and dropped_count is incremented.

        Args:
            item: Item payload to put back at head of buffer.
        """
        with self.lock:
            if len(self.queue) < self.capacity:
                self.queue.appendleft(item)
            else:
                self._dropped_count += 1

    def __len__(self) -> int:
        """Return the current number of items in the buffer."""
        with self.lock:
            return len(self.queue)

    def is_empty(self) -> bool:
        """Check if buffer has no items."""
        with self.lock:
            return len(self.queue) == 0

    def is_full(self) -> bool:
        """Check if buffer has reached capacity."""
        with self.lock:
            return len(self.queue) >= self.capacity

    def clear(self) -> None:
        """Clear all items from buffer and reset dropped count."""
        with self.lock:
            self.queue.clear()
            self._dropped_count = 0
