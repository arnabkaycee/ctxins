"""Base abstract class for provider SSE stream accumulators."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from src.schema.wire import ContentBlock, UsageMetrics


class BaseAccumulator(ABC):
    """Abstract base class for SSE response stream accumulators."""

    @abstractmethod
    def feed_chunk(self, chunk: bytes) -> None:
        """Feed a raw byte chunk from the SSE stream into the accumulator."""
        ...

    @abstractmethod
    def is_done(self) -> bool:
        """Return True if the stream has completed."""
        ...

    @abstractmethod
    def get_content_blocks(self) -> List[ContentBlock]:
        """Return accumulated content blocks sorted by index."""
        ...

    @abstractmethod
    def get_usage(self) -> UsageMetrics:
        """Return token usage metrics extracted from the stream."""
        ...

    @abstractmethod
    def get_stop_reason(self) -> Optional[str]:
        """Return the stop/finish reason if available."""
        ...
