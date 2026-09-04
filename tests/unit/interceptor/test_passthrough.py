"""Unit tests for StreamPassthrough zero-delay token tap and async tee."""

import asyncio
import queue
import time

import pytest

from src.interceptor.stream.passthrough import StreamPassthrough
from src.schema.wire import ActiveTurnContext, Provider, TimingMetrics


def create_turn_context(correlation_id: str = "corr-test-1") -> ActiveTurnContext:
    return ActiveTurnContext(
        correlation_id=correlation_id,
        session_id="session-test-1",
        provider=Provider.ANTHROPIC,
        model="claude-3-5-sonnet",
        timing=TimingMetrics(request_dispatched_at=time.monotonic()),
        endpoint="/v1/messages",
    )


class TestStreamPassthrough:
    """Test suite for StreamPassthrough chunk teeing, timing and fail-open queue."""

    def test_synchronous_generator_passthrough(self):
        async_queue: asyncio.Queue = asyncio.Queue()
        passthrough = StreamPassthrough(async_queue)
        turn = create_turn_context()

        upstream_chunks = [
            b"event: message_start\ndata: {}\n\n",
            b"event: content_block_delta\ndata: {\"text\":\"Hello\"}\n\n",
            b"event: content_block_delta\ndata: {\"text\":\" world\"}\n\n",
            b"event: message_stop\ndata: {}\n\n",
        ]

        downstream_chunks = list(passthrough.tee(upstream_chunks, turn))

        # Downstream client must receive exact same byte chunks
        assert downstream_chunks == upstream_chunks

        # Verify timing was updated
        assert turn.timing.first_byte_received_at is not None
        assert turn.timing.stream_closed_at is not None
        assert turn.timing.stream_closed_at >= turn.timing.first_byte_received_at
        assert turn.timing.ttft_ms is not None
        assert turn.timing.total_duration_ms is not None

        # Verify items teed into async queue
        assert async_queue.qsize() == len(upstream_chunks) + 1  # Chunks + EOF sentinel

        for original in upstream_chunks:
            corr_id, chunk, ts = async_queue.get_nowait()
            assert corr_id == turn.correlation_id
            assert chunk == original
            assert isinstance(ts, float)

        # Verify EOF sentinel
        corr_id, chunk, ts = async_queue.get_nowait()
        assert corr_id == turn.correlation_id
        assert chunk == b""
        assert ts == turn.timing.stream_closed_at

    def test_first_byte_timing_skips_empty_whitespace(self):
        async_queue: asyncio.Queue = asyncio.Queue()
        passthrough = StreamPassthrough(async_queue)
        turn = create_turn_context()

        chunks = [
            b"",
            b"   \n\r  ",
            b"event: message_start\n",
        ]

        gen = passthrough.tee(chunks, turn)

        # Consume empty chunk
        next(gen)
        assert turn.timing.first_byte_received_at is None

        # Consume whitespace chunk
        next(gen)
        assert turn.timing.first_byte_received_at is None

        # Consume non-empty chunk
        next(gen)
        assert turn.timing.first_byte_received_at is not None

        # Complete stream
        with pytest.raises(StopIteration):
            next(gen)

    def test_fail_open_when_queue_is_full(self):
        # Queue with capacity 1
        sync_q: queue.Queue = queue.Queue(maxsize=1)
        passthrough = StreamPassthrough(sync_q)
        turn = create_turn_context()

        chunks = [b"chunk1", b"chunk2", b"chunk3", b"chunk4", b"chunk5"]

        # Stream must yield ALL chunks immediately despite queue saturation
        yielded = list(passthrough.tee(chunks, turn))

        assert yielded == chunks
        assert sync_q.full()
        assert sync_q.qsize() == 1

    def test_generator_early_close(self):
        async_queue: asyncio.Queue = asyncio.Queue()
        passthrough = StreamPassthrough(async_queue)
        turn = create_turn_context()

        chunks = [b"chunk1", b"chunk2", b"chunk3"]
        gen = passthrough.tee(chunks, turn)

        chunk1 = next(gen)
        assert chunk1 == b"chunk1"

        # Client disconnects or aborts early
        gen.close()

        assert turn.timing.stream_closed_at is not None

        # Queue should have received chunk1 and EOF sentinel
        assert async_queue.qsize() == 2
        item1 = async_queue.get_nowait()
        assert item1[1] == b"chunk1"
        eof = async_queue.get_nowait()
        assert eof[1] == b""

    def test_hook_stream_with_iterable(self):
        async_queue: asyncio.Queue = asyncio.Queue()
        passthrough = StreamPassthrough(async_queue)
        turn = create_turn_context()

        class MockResponse:
            stream = [b"chunkA", b"chunkB"]

        class MockFlow:
            response = MockResponse()

        flow = MockFlow()
        passthrough.hook_stream(flow, turn)

        # flow.response.stream is hooked and yields chunks
        result = list(flow.response.stream)
        assert result == [b"chunkA", b"chunkB"]
        assert async_queue.qsize() == 3

    def test_hook_stream_with_callable_and_bool(self):
        async_queue: asyncio.Queue = asyncio.Queue()
        passthrough = StreamPassthrough(async_queue)
        turn = create_turn_context()

        class MockResponse:
            stream = True

        class MockFlow:
            response = MockResponse()

        flow = MockFlow()
        passthrough.hook_stream(flow, turn)

        assert callable(flow.response.stream)
        result = list(flow.response.stream([b"chunk1", b"chunk2"]))
        assert result == [b"chunk1", b"chunk2"]
        assert async_queue.qsize() == 3

    @pytest.mark.asyncio
    async def test_async_generator_passthrough(self):
        async_queue: asyncio.Queue = asyncio.Queue()
        passthrough = StreamPassthrough(async_queue)
        turn = create_turn_context()

        async def upstream_async():
            yield b"async_chunk1"
            yield b"async_chunk2"

        collected = []
        async for c in passthrough.tee_async(upstream_async(), turn):
            collected.append(c)

        assert collected == [b"async_chunk1", b"async_chunk2"]
        assert turn.timing.first_byte_received_at is not None
        assert turn.timing.stream_closed_at is not None
        assert async_queue.qsize() == 3  # 2 chunks + EOF
