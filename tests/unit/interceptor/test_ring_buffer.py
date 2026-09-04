"""Unit tests for BoundedRingBuffer."""

import concurrent.futures
import threading

import pytest

from src.interceptor.egress.ring_buffer import BoundedRingBuffer


class TestBoundedRingBuffer:
    """Tests for BoundedRingBuffer behavior and concurrency."""

    def test_invalid_capacity(self):
        """Buffer capacity must be positive."""
        with pytest.raises(ValueError, match="Buffer capacity must be positive"):
            BoundedRingBuffer(capacity=0)

        with pytest.raises(ValueError, match="Buffer capacity must be positive"):
            BoundedRingBuffer(capacity=-10)

    def test_basic_push_and_pop(self):
        """Test standard FIFO push and pop operations."""
        buf = BoundedRingBuffer(capacity=5)
        assert len(buf) == 0
        assert buf.is_empty()
        assert not buf.is_full()
        assert buf.dropped_count == 0

        assert buf.pop() is None

        buf.push(b"item-1")
        buf.push(b"item-2")
        assert len(buf) == 2
        assert not buf.is_empty()

        assert buf.pop() == b"item-1"
        assert buf.pop() == b"item-2"
        assert buf.pop() is None
        assert buf.is_empty()
        assert buf.dropped_count == 0

    def test_capacity_limit_and_eviction(self):
        """Enqueue 1005 items into a buffer sized for 1000.

        Assert size remains 1000 and dropped_count == 5.
        Oldest 5 items should be evicted, and next pop should yield item 5.
        """
        capacity = 1000
        total_items = 1005
        buf = BoundedRingBuffer(capacity=capacity)

        for i in range(total_items):
            buf.push(f"item-{i}".encode("utf-8"))

        assert len(buf) == capacity
        assert buf.is_full()
        assert buf.dropped_count == 5

        # Oldest items (0..4) should have been dropped; item-5 is the first remaining
        first_popped = buf.pop()
        assert first_popped == b"item-5"
        assert len(buf) == capacity - 1

    def test_clear_resets_buffer_and_drop_count(self):
        """Test that clear() empties buffer and resets dropped_count."""
        buf = BoundedRingBuffer(capacity=3)
        for i in range(5):
            buf.push(f"item-{i}".encode("utf-8"))

        assert buf.dropped_count == 2
        assert len(buf) == 3

        buf.clear()
        assert len(buf) == 0
        assert buf.is_empty()
        assert buf.dropped_count == 0
        assert buf.pop() is None

    def test_unpop_behavior(self):
        """Test putting items back to the head of the buffer with capacity bounds."""
        buf = BoundedRingBuffer(capacity=3)
        buf.push(b"item-1")
        buf.push(b"item-2")

        item = buf.pop()
        assert item == b"item-1"
        assert len(buf) == 1

        # Unpop item-1 back to front
        buf.unpop(item)
        assert len(buf) == 2
        assert buf.pop() == b"item-1"

        # Test unpop when buffer is at capacity: item is dropped to respect capacity
        buf.push(b"item-3")
        buf.push(b"item-4")
        assert len(buf) == 3
        assert buf.is_full()
        assert buf.dropped_count == 0

        buf.unpop(b"overflow-item")
        assert len(buf) == 3
        assert buf.dropped_count == 1

    def test_concurrent_producers_and_consumers(self):
        """Verify thread-safety with concurrent producers and consumers.

        Ensure no data corruption, deadlocks, or race conditions occur.
        """
        capacity = 500
        buf = BoundedRingBuffer(capacity=capacity)

        num_producers = 8
        items_per_producer = 250  # 2000 total pushed items
        num_consumers = 4

        start_barrier = threading.Barrier(num_producers + num_consumers)
        consumed_items: list[bytes] = []
        consumed_lock = threading.Lock()
        stop_consumers = threading.Event()

        def producer_task(producer_id: int):
            start_barrier.wait()
            for i in range(items_per_producer):
                item = f"p{producer_id}-item{i}".encode("utf-8")
                buf.push(item)

        def consumer_task():
            start_barrier.wait()
            while not stop_consumers.is_set() or not buf.is_empty():
                item = buf.pop()
                if item is not None:
                    with consumed_lock:
                        consumed_items.append(item)
                else:
                    # Brief yield if empty
                    threading.Event().wait(0.001)

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=num_producers + num_consumers
        ) as executor:
            consumer_futures = [executor.submit(consumer_task) for _ in range(num_consumers)]
            producer_futures = [
                executor.submit(producer_task, pid) for pid in range(num_producers)
            ]

            # Wait for all producers to finish
            for pf in producer_futures:
                pf.result()

            # Signal consumers to finish remaining items and stop
            stop_consumers.set()
            for cf in consumer_futures:
                cf.result()

        total_pushed = num_producers * items_per_producer
        total_accounted = len(consumed_items) + buf.dropped_count + len(buf)
        assert total_accounted == total_pushed, (
            f"Expected total {total_pushed} == consumed ({len(consumed_items)}) "
            f"+ dropped ({buf.dropped_count}) + remaining ({len(buf)})"
        )
