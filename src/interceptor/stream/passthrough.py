"""Zero-buffering stream passthrough tap and asynchronous queue tee."""

from __future__ import annotations

import asyncio
import logging
import queue
import time
from collections.abc import AsyncIterable, AsyncIterator, Iterable, Iterator
from typing import Any

from src.schema.wire import ActiveTurnContext

logger = logging.getLogger("ctxins.interceptor.passthrough")


class StreamTap:
    """Callable stream hook conforming to mitmproxy's response.stream interface.

    In mitmproxy, response.stream is invoked as:
        stream_fn(chunk: bytes) -> bytes | Iterable[bytes]
    for each received ResponseData chunk, and stream_fn(b"") on ResponseEndOfMessage.

    StreamTap also supports receiving an Iterable[bytes] (e.g. from unit tests or generators).
    All chunks are teed into the egress queue without delaying client delivery, honoring
    fail-open semantics.
    """

    def __init__(
        self,
        queue: asyncio.Queue[Any] | queue.Queue[Any] | Any,
        turn: ActiveTurnContext,
        orig_stream: Any = None,
    ) -> None:
        self.queue = queue
        self.turn = turn
        self.orig_stream = orig_stream
        self._first_chunk = True
        self._closed = False

    def process_chunk(self, chunk: bytes) -> bytes:
        """Record timing and tee a single byte chunk into the queue."""
        if not chunk:
            self.close()
            return b""

        now = time.monotonic()
        if self._first_chunk and isinstance(chunk, (bytes, bytearray, memoryview)) and len(chunk.strip()) > 0:
            if self.turn.timing is not None:
                self.turn.timing.first_byte_received_at = now
            self._first_chunk = False

        try:
            self.queue.put_nowait((self.turn.correlation_id, chunk, now))
        except (asyncio.QueueFull, queue.Full):
            # Drop chunk from telemetry if queue is saturated; NEVER block proxy stream
            pass
        except Exception as e:
            logger.debug("Error putting chunk to queue: %s", e)

        return chunk

    def close(self) -> None:
        """Mark stream closed, finalize timing, and push EOF sentinel."""
        if self._closed:
            return
        self._closed = True
        stream_end = time.monotonic()
        if self.turn.timing is not None:
            self.turn.timing.stream_closed_at = stream_end
        try:
            # b"" sentinel denotes EOF for stream accumulators
            self.queue.put_nowait((self.turn.correlation_id, b"", stream_end))
        except (asyncio.QueueFull, queue.Full):
            pass
        except Exception as e:
            logger.debug("Error putting EOF sentinel to queue: %s", e)

    def __call__(self, chunk_or_chunks: Any) -> Any:
        """Invoked by mitmproxy for each raw data chunk or trailers/end-of-message."""
        try:
            # 1. Standard mitmproxy invocation: individual bytes object per packet
            if isinstance(chunk_or_chunks, (bytes, bytearray, memoryview)):
                raw = bytes(chunk_or_chunks)
                if callable(self.orig_stream):
                    transformed = self.orig_stream(raw)
                    if isinstance(transformed, (bytes, bytearray, memoryview)):
                        return self.process_chunk(bytes(transformed))
                    elif isinstance(transformed, Iterable):
                        return [self.process_chunk(bytes(c)) for c in transformed]
                    return self.process_chunk(raw)
                return self.process_chunk(raw)

            # 2. Iterable/generator of chunks (e.g. unit test mocks)
            if isinstance(chunk_or_chunks, Iterable):
                return self._tee_iterable(chunk_or_chunks)

            # Fallback for unexpected types: pass-through unchanged
            return chunk_or_chunks
        except Exception as e:
            logger.error("Error in StreamTap.__call__: %s", e, exc_info=True)
            return chunk_or_chunks

    def _tee_iterable(self, upstream: Iterable[bytes]) -> Iterator[bytes]:
        try:
            for item in upstream:
                if isinstance(item, (bytes, bytearray, memoryview)):
                    raw = bytes(item)
                    if callable(self.orig_stream):
                        transformed = self.orig_stream(raw)
                        if isinstance(transformed, (bytes, bytearray, memoryview)):
                            yield self.process_chunk(bytes(transformed))
                        elif isinstance(transformed, Iterable):
                            for sub in transformed:
                                yield self.process_chunk(bytes(sub))
                        else:
                            yield self.process_chunk(raw)
                    else:
                        yield self.process_chunk(raw)
                else:
                    yield item
        finally:
            self.close()


class StreamPassthrough:
    """Hooks into response chunk generators to tap SSE tokens without delaying delivery.

    Yields chunks to the client downstream immediately while teeing chunks into
    an asynchronous queue for background processing. Drops telemetry if the queue
    is full, ensuring client latency is never impacted.
    """

    def __init__(self, queue: asyncio.Queue[Any] | queue.Queue[Any] | Any) -> None:
        self.queue = queue

    def hook_stream(self, flow: Any, turn: ActiveTurnContext) -> None:
        """Hook into a mitmproxy flow's response stream.

        Args:
            flow: mitmproxy HTTPFlow object with a response attribute.
            turn: ActiveTurnContext tracking the current turn's metadata and timing.
        """
        if not hasattr(flow, "response") or flow.response is None:
            return

        current_stream = getattr(flow.response, "stream", None)
        orig_stream = current_stream if callable(current_stream) else None

        if isinstance(current_stream, (Iterator, Iterable)) and not isinstance(
            current_stream, (str, bytes, bytearray, memoryview)
        ):
            tap = StreamTap(self.queue, turn, orig_stream=None)
            flow.response.stream = tap._tee_iterable(current_stream)
        else:
            tap = StreamTap(self.queue, turn, orig_stream=orig_stream)
            flow.response.stream = tap

    def tee(
        self,
        upstream_generator: Iterable[bytes],
        turn: ActiveTurnContext,
    ) -> Iterator[bytes]:
        """Tee a synchronous stream of byte chunks to client and async queue."""
        tap = StreamTap(self.queue, turn)
        return tap._tee_iterable(upstream_generator)

    async def _tee_async_generator(
        self,
        upstream_generator: AsyncIterable[bytes],
        turn: ActiveTurnContext,
    ) -> AsyncIterator[bytes]:
        """Tee an asynchronous stream of byte chunks to client and async queue.

        Args:
            upstream_generator: Async iterable yielding byte chunks.
            turn: In-flight turn context to update timing metrics.

        Yields:
            Raw byte chunks immediately as they arrive.
        """
        first_chunk = True
        try:
            async for chunk in upstream_generator:
                now = time.monotonic()
                if first_chunk and len(chunk.strip()) > 0:
                    if turn.timing is not None:
                        turn.timing.first_byte_received_at = now
                    first_chunk = False

                try:
                    self.queue.put_nowait((turn.correlation_id, chunk, now))
                except (asyncio.QueueFull, queue.Full):
                    pass

                yield chunk
        finally:
            stream_end = time.monotonic()
            if turn.timing is not None:
                turn.timing.stream_closed_at = stream_end
            try:
                self.queue.put_nowait((turn.correlation_id, b"", stream_end))
            except (asyncio.QueueFull, queue.Full):
                pass

    def tee_async(
        self,
        upstream_generator: AsyncIterable[bytes],
        turn: ActiveTurnContext,
    ) -> AsyncIterator[bytes]:
        """Public alias for _tee_async_generator."""
        return self._tee_async_generator(upstream_generator, turn)
