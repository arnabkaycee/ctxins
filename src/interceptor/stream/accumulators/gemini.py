"""Gemini stream accumulator supporting SSE, JSON array stream, and NDJSON."""

from __future__ import annotations

import codecs
import json
from typing import Any, Dict, List, Optional

from src.interceptor.stream.accumulators.base import BaseAccumulator
from src.interceptor.stream.sse_parser import SSEParser
from src.schema.wire import ContentBlock, UsageMetrics


class GeminiAccumulator(BaseAccumulator):
    """Accumulates Google Gemini stream chunks (SSE, JSON array, NDJSON) into canonical turn output.

    Handles candidates, parts (text, thinking, functionCall), finishReason,
    and usageMetadata.
    """

    def __init__(self) -> None:
        self._parser = SSEParser()
        self._blocks: List[ContentBlock] = []
        self._stop_reason: Optional[str] = None
        self._usage = UsageMetrics()
        self._is_done: bool = False
        self._mode: Optional[str] = None  # "sse" or "json"
        self._json_buffer: str = ""
        self._utf8_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._json_decoder = json.JSONDecoder()

    def feed_chunk(self, chunk: bytes) -> None:
        """Feed a raw byte chunk into the accumulator."""
        if not chunk:
            if self._mode == "sse":
                events = self._parser.close()
                self._process_events(events)
            else:
                # Flush any remaining JSON buffer
                rem = self._json_buffer.strip()
                if rem:
                    try:
                        data = json.loads(rem)
                        if isinstance(data, list):
                            for item in data:
                                if isinstance(item, dict):
                                    self._handle_chunk(item)
                        elif isinstance(data, dict):
                            self._handle_chunk(data)
                    except Exception:
                        pass
            self._is_done = True
            return

        if self._mode is None:
            trimmed = chunk.lstrip()
            if trimmed.startswith(b"data:") or trimmed.startswith(b"event:") or trimmed.startswith(b":"):
                self._mode = "sse"
            elif trimmed.startswith(b"[") or trimmed.startswith(b"{"):
                self._mode = "json"

        if self._mode == "sse":
            events = self._parser.feed(chunk)
            self._process_events(events)
        else:
            # Handle JSON array stream, NDJSON, or plain JSON
            text = self._utf8_decoder.decode(chunk, final=False)
            self._json_buffer += text
            idx = 0
            buf_len = len(self._json_buffer)
            while idx < buf_len:
                while idx < buf_len and self._json_buffer[idx] in " \t\r\n,[":
                    idx += 1
                if idx >= buf_len or self._json_buffer[idx] == "]":
                    break
                try:
                    data, next_idx = self._json_decoder.raw_decode(self._json_buffer, idx)
                    if isinstance(data, dict):
                        self._handle_chunk(data)
                    elif isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict):
                                self._handle_chunk(item)
                    idx = next_idx
                except json.JSONDecodeError:
                    # Partial JSON chunk, await next chunk
                    break
            if idx > 0:
                self._json_buffer = self._json_buffer[idx:]

    def is_done(self) -> bool:
        """Return True if completion finishReason received or stream closed."""
        return self._is_done

    def get_content_blocks(self) -> List[ContentBlock]:
        """Return accumulated content blocks sorted by index."""
        return sorted(self._blocks, key=lambda b: b.index)

    def get_usage(self) -> UsageMetrics:
        """Return token usage metrics."""
        return self._usage

    def get_stop_reason(self) -> Optional[str]:
        """Return the finish reason if available."""
        return self._stop_reason

    def _process_events(self, events: List[Any]) -> None:
        for ev in events:
            raw_data = ev.data.strip()
            if not raw_data:
                continue

            try:
                data = json.loads(raw_data)
            except (json.JSONDecodeError, TypeError):
                continue

            if isinstance(data, dict):
                self._handle_chunk(data)
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        self._handle_chunk(item)

    def _handle_chunk(self, data: Dict[str, Any]) -> None:
        # Handle wrapped payload from Cloud Code / AI Code endpoints
        if "response" in data and isinstance(data["response"], dict):
            data = data["response"]

        # Extract usageMetadata if present
        usage_meta = data.get("usageMetadata")
        if usage_meta:
            if "promptTokenCount" in usage_meta:
                self._usage.input_tokens = usage_meta["promptTokenCount"]
            if "candidatesTokenCount" in usage_meta:
                self._usage.output_tokens = usage_meta["candidatesTokenCount"]
            if "cachedContentTokenCount" in usage_meta:
                self._usage.cache_read_input_tokens = usage_meta["cachedContentTokenCount"]
            if "thoughtsTokenCount" in usage_meta:
                self._usage.reasoning_tokens = usage_meta["thoughtsTokenCount"]

            for detail in usage_meta.get("candidatesTokensDetails", []):
                if detail.get("modality") in ("THINKING", "REASONING"):
                    self._usage.reasoning_tokens = detail.get("tokenCount", 0)

        top_finish = data.get("finishReason")
        if top_finish:
            self._stop_reason = top_finish
            self._is_done = True

        candidates = data.get("candidates", [])
        for cand in candidates:
            finish_reason = cand.get("finishReason")
            if finish_reason:
                self._stop_reason = finish_reason
                self._is_done = True

            content = cand.get("content", {})
            parts = content.get("parts", [])
            for part in parts:
                self._handle_part(part)

    def _handle_part(self, part: Dict[str, Any]) -> None:
        if "functionCall" in part:
            fc = part["functionCall"]
            name = fc.get("name")
            args = fc.get("args")
            call_id = fc.get("id")

            if isinstance(args, dict):
                parsed_input = args
                partial_json = json.dumps(args)
            elif isinstance(args, str):
                partial_json = args
                try:
                    parsed_input = json.loads(args)
                except json.JSONDecodeError:
                    parsed_input = {"_raw": args}
            else:
                parsed_input = {}
                partial_json = "{}"

            block = ContentBlock(
                index=len(self._blocks),
                block_type="tool_use",
                tool_id=call_id,
                tool_name=name,
                partial_json=partial_json,
                parsed_input=parsed_input,
            )
            self._blocks.append(block)
            return

        # Check for thinking/reasoning parts
        is_thought = part.get("thought") is True
        text = part.get("text", "")
        if "thought" in part and isinstance(part["thought"], str):
            is_thought = True
            text = part["thought"]

        if is_thought:
            if self._blocks and self._blocks[-1].block_type == "thinking":
                self._blocks[-1].text = (self._blocks[-1].text or "") + text
            else:
                block = ContentBlock(
                    index=len(self._blocks),
                    block_type="thinking",
                    text=text,
                )
                self._blocks.append(block)
        elif "text" in part:
            if self._blocks and self._blocks[-1].block_type == "text":
                self._blocks[-1].text = (self._blocks[-1].text or "") + text
            else:
                block = ContentBlock(
                    index=len(self._blocks),
                    block_type="text",
                    text=text,
                )
                self._blocks.append(block)
