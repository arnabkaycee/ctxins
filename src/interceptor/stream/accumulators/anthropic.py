"""Anthropic SSE stream accumulator."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from src.interceptor.stream.accumulators.base import BaseAccumulator
from src.interceptor.stream.sse_parser import SSEParser
from src.schema.wire import ContentBlock, UsageMetrics


class AnthropicAccumulator(BaseAccumulator):
    """Accumulates Anthropic streaming SSE events into canonical turn output.

    Handles message_start, content_block_start, content_block_delta (text_delta,
    input_json_delta, thinking_delta), content_block_stop, message_delta, and message_stop.
    Stitches fragmented tool call JSON across chunks.
    """

    def __init__(self) -> None:
        self._parser = SSEParser()
        self._blocks: Dict[int, ContentBlock] = {}
        self._stop_reason: Optional[str] = None
        self._usage = UsageMetrics()
        self._is_done: bool = False

    def feed_chunk(self, chunk: bytes) -> None:
        """Feed a raw byte chunk into the accumulator."""
        if not chunk:
            events = self._parser.close()
            self._process_events(events)
            self._finalize()
            self._is_done = True
            return

        events = self._parser.feed(chunk)
        self._process_events(events)

    def is_done(self) -> bool:
        """Return True if message_stop has been processed or stream closed."""
        return self._is_done

    def get_content_blocks(self) -> List[ContentBlock]:
        """Return all reconstructed content blocks sorted by index."""
        return [self._blocks[idx] for idx in sorted(self._blocks.keys())]

    def get_usage(self) -> UsageMetrics:
        """Return token usage metrics."""
        return self._usage

    def get_stop_reason(self) -> Optional[str]:
        """Return the stop reason if reached."""
        return self._stop_reason

    def _process_events(self, events: List[Any]) -> None:
        for ev in events:
            if not ev.data or ev.event == "ping":
                continue

            try:
                data = json.loads(ev.data)
            except (json.JSONDecodeError, TypeError):
                continue

            event_type = ev.event or data.get("type", "")
            self._handle_event(event_type, data)

    def _handle_event(self, event_type: str, data: Dict[str, Any]) -> None:
        if event_type == "message_start":
            self._handle_message_start(data)
        elif event_type == "content_block_start":
            self._handle_content_block_start(data)
        elif event_type == "content_block_delta":
            self._handle_content_block_delta(data)
        elif event_type == "content_block_stop":
            self._handle_content_block_stop(data)
        elif event_type == "message_delta":
            self._handle_message_delta(data)
        elif event_type == "message_stop":
            self._finalize()
            self._is_done = True

    def _handle_message_start(self, data: Dict[str, Any]) -> None:
        msg = data.get("message", {})
        raw_usage = msg.get("usage", {})
        if "input_tokens" in raw_usage:
            self._usage.input_tokens = raw_usage["input_tokens"]
        if "cache_creation_input_tokens" in raw_usage:
            self._usage.cache_creation_input_tokens = raw_usage["cache_creation_input_tokens"]
        if "cache_read_input_tokens" in raw_usage:
            self._usage.cache_read_input_tokens = raw_usage["cache_read_input_tokens"]

    def _handle_content_block_start(self, data: Dict[str, Any]) -> None:
        idx = data.get("index", len(self._blocks))
        block_info = data.get("content_block", {})
        b_type = block_info.get("type", "text")

        if b_type == "text":
            self._blocks[idx] = ContentBlock(
                index=idx,
                block_type="text",
                text=block_info.get("text", "") or "",
            )
        elif b_type == "tool_use":
            self._blocks[idx] = ContentBlock(
                index=idx,
                block_type="tool_use",
                tool_id=block_info.get("id"),
                tool_name=block_info.get("name"),
                partial_json="",
            )
        elif b_type == "thinking":
            self._blocks[idx] = ContentBlock(
                index=idx,
                block_type="thinking",
                text=block_info.get("thinking", "") or "",
            )
        else:
            self._blocks[idx] = ContentBlock(
                index=idx,
                block_type=b_type,
                text=block_info.get("text"),
            )

    def _handle_content_block_delta(self, data: Dict[str, Any]) -> None:
        idx = data.get("index", 0)
        delta = data.get("delta", {})
        d_type = delta.get("type")

        if idx not in self._blocks:
            # Defensive instantiation if block_start was missing
            if d_type == "input_json_delta":
                self._blocks[idx] = ContentBlock(
                    index=idx,
                    block_type="tool_use",
                    partial_json="",
                )
            elif d_type == "thinking_delta":
                self._blocks[idx] = ContentBlock(
                    index=idx,
                    block_type="thinking",
                    text="",
                )
            else:
                self._blocks[idx] = ContentBlock(
                    index=idx,
                    block_type="text",
                    text="",
                )

        block = self._blocks[idx]
        if d_type == "text_delta":
            block.text = (block.text or "") + delta.get("text", "")
        elif d_type == "thinking_delta":
            block.text = (block.text or "") + delta.get("thinking", "")
        elif d_type == "input_json_delta":
            block.partial_json = (block.partial_json or "") + delta.get("partial_json", "")

    def _handle_content_block_stop(self, data: Dict[str, Any]) -> None:
        idx = data.get("index", 0)
        if idx in self._blocks:
            block = self._blocks[idx]
            if block.block_type == "tool_use" and block.partial_json is not None:
                self._parse_tool_json(block)

    def _handle_message_delta(self, data: Dict[str, Any]) -> None:
        delta = data.get("delta", {})
        if "stop_reason" in delta and delta["stop_reason"] is not None:
            self._stop_reason = delta["stop_reason"]

        delta_usage = data.get("usage", {})
        if "output_tokens" in delta_usage:
            self._usage.output_tokens = delta_usage["output_tokens"]

    def _parse_tool_json(self, block: ContentBlock) -> None:
        if block.partial_json == "":
            block.parsed_input = {}
        elif block.partial_json is not None:
            try:
                block.parsed_input = json.loads(block.partial_json)
            except json.JSONDecodeError:
                block.parsed_input = {"_raw": block.partial_json}

    def _finalize(self) -> None:
        for block in self._blocks.values():
            if (
                block.block_type == "tool_use"
                and block.parsed_input is None
                and block.partial_json is not None
            ):
                self._parse_tool_json(block)
