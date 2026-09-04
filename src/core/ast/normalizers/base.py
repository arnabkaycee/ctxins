"""Abstract base normalizer converting provider-specific payloads to CanonicalTurn AST."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Optional

from src.schema.ast import CanonicalTurn
from src.schema.wire import TimingMetrics, UsageMetrics, WireEnvelope


def default_token_estimator(text: str) -> int:
    """Default fallback token estimator: max(1, len(text) // 4) for non-empty text."""
    if not text:
        return 0
    return max(1, len(text) // 4)


class BaseNormalizer(ABC):
    """Abstract interface and common utilities for provider AST normalizers."""

    def __init__(
        self,
        token_counter: Optional[Callable[[str], int]] = None,
    ) -> None:
        """Initialize normalizer.

        Args:
            token_counter: Optional callable computing token counts from string.
                           Defaults to character-based heuristic (len // 4).
        """
        self._token_counter = token_counter or default_token_estimator

    def estimate_tokens(self, text: str) -> int:
        """Estimate or compute token count for a text string."""
        return self._token_counter(text)

    @abstractmethod
    def normalize(
        self,
        turn_data: Dict[str, Any],
        turn_index: Optional[int] = None,
    ) -> CanonicalTurn:
        """Normalize raw turn payload into a provider-agnostic CanonicalTurn.

        Args:
            turn_data: Dictionary containing wire event or turn payload.
            turn_index: Optional turn index override.

        Returns:
            CanonicalTurn AST representation.
        """
        pass

    def _extract_base_fields(
        self,
        turn_data: Dict[str, Any] | WireEnvelope,
        turn_index: Optional[int] = None,
        default_provider: str = "unknown",
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Extract top-level metadata, request payload, and response payload.

        Returns:
            Tuple of (metadata_dict, request_payload, response_payload).
        """
        if isinstance(turn_data, WireEnvelope):
            raw = turn_data.to_dict()
        else:
            raw = dict(turn_data)

        # Handle nested WireEnvelope payload if present
        raw_payload = raw.get("payload")
        payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}

        # 1. Turn index
        if turn_index is not None:
            resolved_index = turn_index
        elif "turn_index" in raw:
            resolved_index = int(raw["turn_index"])
        elif "turnIndex" in raw:
            resolved_index = int(raw["turnIndex"])
        elif "turn_index" in payload:
            resolved_index = int(payload["turn_index"])
        elif "turnIndex" in payload:
            resolved_index = int(payload["turnIndex"])
        else:
            resolved_index = 0

        # 2. Correlation and session IDs
        corr_id = (
            raw.get("correlation_id")
            or raw.get("correlationId")
            or payload.get("correlation_id")
            or payload.get("correlationId")
            or f"turn_{resolved_index}"
        )
        sess_id = (
            raw.get("session_id")
            or raw.get("sessionId")
            or payload.get("session_id")
            or payload.get("sessionId")
            or "sess_default"
        )
        turn_id = (
            raw.get("turn_id")
            or raw.get("turnId")
            or payload.get("turn_id")
            or payload.get("turnId")
            or corr_id
        )

        timestamp = float(
            raw.get("timestamp")
            or payload.get("timestamp")
            or time.time()
        )

        provider = (
            raw.get("provider")
            or payload.get("provider")
            or default_provider
        )

        # 3. Request payload
        req = (
            raw.get("request_payload")
            or raw.get("requestPayload")
            or raw.get("request")
            or payload.get("request_payload")
            or payload.get("requestPayload")
            or payload.get("request")
            or (payload if any(k in payload for k in ("messages", "contents", "system", "tools")) else {})
        )
        if not isinstance(req, dict):
            req = {}

        # 4. Response payload
        resp = (
            raw.get("response_payload")
            or raw.get("responsePayload")
            or raw.get("response")
            or payload.get("response_payload")
            or payload.get("responsePayload")
            or payload.get("response")
            or (payload if any(k in payload for k in ("content", "choices", "candidates")) else {})
        )
        if not isinstance(resp, dict):
            resp = {}

        # 5. Model
        model = (
            req.get("model")
            or raw.get("model")
            or payload.get("model")
            or resp.get("model")
            or "unknown"
        )

        # 6. Usage & Timing
        usage_raw = (
            raw.get("usage")
            or raw.get("usage_metrics")
            or payload.get("usage")
            or payload.get("usage_metrics")
            or resp.get("usage")
            or resp.get("usageMetadata")
            or {}
        )
        timing_raw = (
            raw.get("timing")
            or raw.get("timing_metrics")
            or payload.get("timing")
            or payload.get("timing_metrics")
            or {}
        )

        metadata = {
            "turn_id": turn_id,
            "correlation_id": corr_id,
            "session_id": sess_id,
            "turn_index": resolved_index,
            "timestamp": timestamp,
            "provider": provider,
            "model": model,
            "usage": usage_raw,
            "timing": timing_raw,
        }

        return metadata, req, resp

    def _parse_usage(self, usage: Any) -> tuple[int, int, int, int]:
        """Extract (input_tokens, output_tokens, cached_read_tokens, cached_created_tokens)."""
        if isinstance(usage, UsageMetrics):
            return (
                usage.input_tokens,
                usage.output_tokens,
                usage.cache_read_input_tokens,
                usage.cache_creation_input_tokens,
            )

        if not isinstance(usage, dict):
            return (0, 0, 0, 0)

        # Standard and provider-specific keys
        input_tokens = (
            usage.get("input_tokens")
            or usage.get("inputTokens")
            or usage.get("prompt_tokens")
            or usage.get("promptTokenCount")
            or 0
        )
        output_tokens = (
            usage.get("output_tokens")
            or usage.get("outputTokens")
            or usage.get("completion_tokens")
            or usage.get("candidatesTokenCount")
            or 0
        )

        cached_read = (
            usage.get("cache_read_input_tokens")
            or usage.get("cacheReadInputTokens")
            or usage.get("cachedContentTokenCount")
            or usage.get("cached_read_tokens")
            or 0
        )
        if not cached_read and isinstance(usage.get("prompt_tokens_details"), dict):
            cached_read = usage["prompt_tokens_details"].get("cached_tokens", 0)

        cached_created = (
            usage.get("cache_creation_input_tokens")
            or usage.get("cacheCreationInputTokens")
            or usage.get("cached_created_tokens")
            or 0
        )
        if not cached_created and isinstance(usage.get("prompt_tokens_details"), dict):
            cached_created = usage["prompt_tokens_details"].get("cache_creation_tokens", 0)

        return int(input_tokens), int(output_tokens), int(cached_read), int(cached_created)

    def _parse_timing(self, timing: Any) -> tuple[float, Optional[float]]:
        """Extract (duration_ms, ttft_ms)."""
        if isinstance(timing, TimingMetrics):
            return (timing.total_duration_ms or 0.0, timing.ttft_ms)

        if not isinstance(timing, dict):
            return (0.0, None)

        duration_ms = float(
            timing.get("duration_ms")
            or timing.get("durationMs")
            or timing.get("total_duration_ms")
            or 0.0
        )

        ttft_ms = timing.get("ttft_ms") or timing.get("ttftMs")
        if ttft_ms is not None:
            return duration_ms, float(ttft_ms)

        if "first_byte_received_at" in timing and "request_dispatched_at" in timing:
            if timing["first_byte_received_at"] is not None and timing["request_dispatched_at"] is not None:
                return duration_ms, (timing["first_byte_received_at"] - timing["request_dispatched_at"]) * 1000.0

        if "firstByteReceivedMs" in timing and "requestDispatchedMs" in timing:
            if timing["firstByteReceivedMs"] is not None and timing["requestDispatchedMs"] is not None:
                return duration_ms, float(timing["firstByteReceivedMs"] - timing["requestDispatchedMs"])

        return duration_ms, None
