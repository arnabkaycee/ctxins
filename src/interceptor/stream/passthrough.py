"""Zero-buffering stream passthrough tap and asynchronous queue tee."""

from __future__ import annotations

import asyncio
import queue
import time
from collections.abc import AsyncIterable, AsyncIterator, Iterable, Iterator
from typing import Any

from src.schema.wire import ActiveTurnContext


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

        if callable(current_stream):
            orig_stream = current_stream
            flow.response.stream = lambda chunks: self._tee_generator(orig_stream(chunks), turn)
        elif isinstance(current_stream, (Iterator, Iterable)) and not isinstance(current_stream, (str, bytes)):
            flow.response.stream = self._tee_generator(current_stream, turn)
        else:
            # mitmproxy streaming mode: stream modifier callable that receives upstream chunks
            flow.response.stream = lambda chunks: self._tee_generator(chunks, turn)

    def _tee_generator(
        self,
        upstream_generator: Iterable[bytes],
        turn: ActiveTurnContext,
    ) -> Iterator[bytes]:
        """Tee a synchronous stream of byte chunks to client and async queue.

        Args:
            upstream_generator: Source iterable yielding byte chunks.
            turn: In-flight turn context to update timing metrics.

        Yields:
            Raw byte chunks immediately as they arrive.
        """
        first_chunk = True
        try:
            for chunk in upstream_generator:
                now = time.monotonic()
                if first_chunk and len(chunk.strip()) > 0:
                    if turn.timing is not None:
                        turn.timing.first_byte_received_at = now
                    first_chunk = False

                # Non-blocking clone into processing queue
                try:
                    self.queue.put_nowait((turn.correlation_id, chunk, now))
                except (asyncio.QueueFull, queue.Full):
                    # Drop chunk from telemetry if queue is saturated; NEVER block proxy stream
                    pass

                # Forward immediately to client
                yield chunk
        finally:
            stream_end = time.monotonic()
            if turn.timing is not None:
                turn.timing.stream_closed_at = stream_end
            try:
                # b"" sentinel denotes EOF for stream accumulators
                self.queue.put_nowait((turn.correlation_id, b"", stream_end))
            except (asyncio.QueueFull, queue.Full):
                pass

    def tee(
        self,
        upstream_generator: Iterable[bytes],
        turn: ActiveTurnContext,
    ) -> Iterator[bytes]:
        """Public alias for _tee_generator."""
        return self._tee_generator(upstream_generator, turn)

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
