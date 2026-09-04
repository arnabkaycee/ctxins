"""OpenAI SSE stream accumulator."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from src.interceptor.stream.accumulators.base import BaseAccumulator
from src.interceptor.stream.sse_parser import SSEParser
from src.schema.wire import ContentBlock, UsageMetrics


class OpenAIAccumulator(BaseAccumulator):
    """Accumulates OpenAI chat completion SSE stream chunks into canonical turn output.

    Handles choices[].delta (content, reasoning_content, tool_calls),
    finish_reason, usage metrics, and stream termination via [DONE] or EOF.
    """

    def __init__(self) -> None:
        self._parser = SSEParser()
        self._ordered_blocks: List[ContentBlock] = []
        self._thinking_block: Optional[ContentBlock] = None
        self._text_block: Optional[ContentBlock] = None
        self._tool_blocks: Dict[int, ContentBlock] = {}
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
        """Return True if stream has finished ([DONE] received or stream closed)."""
        return self._is_done

    def get_content_blocks(self) -> List[ContentBlock]:
        """Return accumulated content blocks sorted by index."""
        return sorted(self._ordered_blocks, key=lambda b: b.index)

    def get_usage(self) -> UsageMetrics:
        """Return token usage metrics."""
        return self._usage

    def get_stop_reason(self) -> Optional[str]:
        """Return the stop/finish reason."""
        return self._stop_reason

    def _process_events(self, events: List[Any]) -> None:
        for ev in events:
            raw_data = ev.data.strip()
            if not raw_data:
                continue

            if raw_data == "[DONE]":
                self._finalize()
                self._is_done = True
                continue

            try:
                data = json.loads(raw_data)
            except (json.JSONDecodeError, TypeError):
                continue

            self._handle_chunk(data)

    def _handle_chunk(self, data: Dict[str, Any]) -> None:
        # Extract usage if present (e.g. stream_options: {include_usage: true})
        usage_data = data.get("usage")
        if usage_data:
            if "prompt_tokens" in usage_data:
                self._usage.input_tokens = usage_data["prompt_tokens"]
            if "completion_tokens" in usage_data:
                self._usage.output_tokens = usage_data["completion_tokens"]

            prompt_details = usage_data.get("prompt_tokens_details") or {}
            if "cached_tokens" in prompt_details:
                self._usage.cache_read_input_tokens = prompt_details["cached_tokens"]

            comp_details = usage_data.get("completion_tokens_details") or {}
            if "reasoning_tokens" in comp_details:
                self._usage.reasoning_tokens = comp_details["reasoning_tokens"]

        choices = data.get("choices", [])
        for choice in choices:
            finish_reason = choice.get("finish_reason")
            if finish_reason is not None:
                self._stop_reason = finish_reason

            delta = choice.get("delta", {})

            # Handle reasoning_content (o1 / o3 / DeepSeek reasoning models)
            reasoning = delta.get("reasoning_content")
            if reasoning is not None:
                if self._thinking_block is None:
                    self._thinking_block = ContentBlock(
                        index=len(self._ordered_blocks),
                        block_type="thinking",
                        text="",
                    )
                    self._ordered_blocks.append(self._thinking_block)
                self._thinking_block.text = (self._thinking_block.text or "") + reasoning

            # Handle standard content text
            content = delta.get("content")
            if content is not None:
                if self._text_block is None:
                    self._text_block = ContentBlock(
                        index=len(self._ordered_blocks),
                        block_type="text",
                        text="",
                    )
                    self._ordered_blocks.append(self._text_block)
                self._text_block.text = (self._text_block.text or "") + content

            # Handle tool calls
            tool_calls = delta.get("tool_calls", [])
            for tc in tool_calls:
                tc_idx = tc.get("index", 0)
                fn = tc.get("function", {})
                call_id = tc.get("id")
                fn_name = fn.get("name")
                fn_args = fn.get("arguments", "")

                if tc_idx not in self._tool_blocks:
                    tool_block = ContentBlock(
                        index=len(self._ordered_blocks),
                        block_type="tool_use",
                        tool_id=call_id,
                        tool_name=fn_name,
                        partial_json=fn_args or "",
                    )
                    self._tool_blocks[tc_idx] = tool_block
                    self._ordered_blocks.append(tool_block)
                else:
                    tool_block = self._tool_blocks[tc_idx]
                    if call_id:
                        tool_block.tool_id = call_id
                    if fn_name:
                        tool_block.tool_name = (tool_block.tool_name or "") + fn_name
                    if fn_args:
                        tool_block.partial_json = (tool_block.partial_json or "") + fn_args

    def _finalize(self) -> None:
        for tool_block in self._tool_blocks.values():
            if tool_block.parsed_input is None and tool_block.partial_json is not None:
                if tool_block.partial_json == "":
                    tool_block.parsed_input = {}
                else:
                    try:
                        tool_block.parsed_input = json.loads(tool_block.partial_json)
                    except json.JSONDecodeError:
                        tool_block.parsed_input = {"_raw": tool_block.partial_json}
