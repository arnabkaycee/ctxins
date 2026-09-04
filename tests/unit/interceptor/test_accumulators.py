"""Unit tests for SSE parser and accumulators (Anthropic, OpenAI, Gemini)."""

from __future__ import annotations

import json

from src.interceptor.stream.accumulators.anthropic import AnthropicAccumulator
from src.interceptor.stream.accumulators.gemini import GeminiAccumulator
from src.interceptor.stream.accumulators.openai import OpenAIAccumulator
from src.interceptor.stream.sse_parser import SSEParser


class TestSSEParser:
    def test_fragmented_chunk_boundaries(self) -> None:
        """Verify parsing across arbitrary byte boundaries matching test-strategy.md spec."""
        parser = SSEParser()

        chunk1 = b"event: content_block_delta\ndat"
        chunk2 = b'a: {"type":"text_delta","text":"Hello'
        chunk3 = b' world"}\n\n'

        events1 = parser.feed(chunk1)
        assert len(events1) == 0

        events2 = parser.feed(chunk2)
        assert len(events2) == 0

        events3 = parser.feed(chunk3)
        assert len(events3) == 1
        assert events3[0].event == "content_block_delta"
        data = json.loads(events3[0].data)
        assert data["type"] == "text_delta"
        assert data["text"] == "Hello world"

    def test_multibyte_utf8_split(self) -> None:
        """Verify handling of multi-byte UTF-8 characters split mid-sequence."""
        parser = SSEParser()
        # "🎉" in UTF-8 is b'\xf0\x9f\x8e\x89' (4 bytes)
        raw = 'data: {"emoji": "🎉"}\n\n'.encode("utf-8")
        split_idx = raw.index(b"\xf0") + 2  # Split in the middle of the 4-byte sequence

        ev1 = parser.feed(raw[:split_idx])
        assert len(ev1) == 0

        ev2 = parser.feed(raw[split_idx:])
        assert len(ev2) == 1
        data = json.loads(ev2[0].data)
        assert data["emoji"] == "🎉"

    def test_crlf_and_cr_terminators(self) -> None:
        """Verify CRLF and standalone CR line endings are parsed properly."""
        parser = SSEParser()
        chunk = b"event: ping\r\ndata: pong\r\n\r\n"
        events = parser.feed(chunk)
        assert len(events) == 1
        assert events[0].event == "ping"
        assert events[0].data == "pong"

    def test_comments_and_multiline_data(self) -> None:
        """Verify comments are ignored and multi-line data is joined with newline."""
        parser = SSEParser()
        chunk = (
            b": this is a comment\n"
            b"data: line1\n"
            b"data: line2\n"
            b": another comment\n\n"
        )
        events = parser.feed(chunk)
        assert len(events) == 1
        assert events[0].event == "message"
        assert events[0].data == "line1\nline2"

    def test_close_flushes_pending_event(self) -> None:
        """Verify close() flushes an event even if no trailing blank line."""
        parser = SSEParser()
        parser.feed(b"event: custom\ndata: final_data")
        events = parser.close()
        assert len(events) == 1
        assert events[0].event == "custom"
        assert events[0].data == "final_data"


class TestAnthropicAccumulator:
    def test_full_anthropic_stream_reconstruction(self) -> None:
        """Reconstruct text response and extract usage + stop reason."""
        acc = AnthropicAccumulator()

        chunks = [
            b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1","type":"message","role":"assistant","usage":{"input_tokens":120,"cache_creation_input_tokens":10,"cache_read_input_tokens":50}}}\n\n',
            b'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n',
            b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}\n\n',
            b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":", "}}\n\n',
            b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"world!"}}\n\n',
            b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
            b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":25}}\n\n',
            b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]

        for chunk in chunks:
            acc.feed_chunk(chunk)

        assert acc.is_done() is True
        assert acc.get_stop_reason() == "end_turn"

        usage = acc.get_usage()
        assert usage.input_tokens == 120
        assert usage.cache_creation_input_tokens == 10
        assert usage.cache_read_input_tokens == 50
        assert usage.output_tokens == 25

        blocks = acc.get_content_blocks()
        assert len(blocks) == 1
        assert blocks[0].block_type == "text"
        assert blocks[0].text == "Hello, world!"

    def test_tool_call_multi_chunk_stitching(self) -> None:
        """Verify multi-chunk input_json_delta stitching into parsed dictionary."""
        acc = AnthropicAccumulator()

        acc.feed_chunk(
            b'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_abc","name":"query_db","input":{}}}\n\n'
        )

        # 5 sequential input_json_delta chunks
        json_deltas = [
            '{"query": ',
            '"SELECT * ',
            'FROM ',
            'users',
            '"}',
        ]
        for delta in json_deltas:
            raw = f'event: content_block_delta\ndata: {{"type":"content_block_delta","index":0,"delta":{{"type":"input_json_delta","partial_json":{json.dumps(delta)}}}}}\n\n'.encode(
                "utf-8"
            )
            acc.feed_chunk(raw)

        acc.feed_chunk(
            b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n'
        )
        acc.feed_chunk(
            b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":150}}\n\n'
        )
        acc.feed_chunk(b'event: message_stop\ndata: {"type":"message_stop"}\n\n')

        assert acc.is_done() is True
        assert acc.get_stop_reason() == "tool_use"
        assert acc.get_usage().output_tokens == 150

        blocks = acc.get_content_blocks()
        assert len(blocks) == 1
        tb = blocks[0]
        assert tb.block_type == "tool_use"
        assert tb.tool_id == "toolu_abc"
        assert tb.tool_name == "query_db"
        assert tb.parsed_input == {"query": "SELECT * FROM users"}

    def test_thinking_block_accumulation(self) -> None:
        """Verify thinking_delta chunks accumulate into a thinking content block."""
        acc = AnthropicAccumulator()

        acc.feed_chunk(
            b'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}\n\n'
        )
        acc.feed_chunk(
            b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"Analyzing the schema..."}}\n\n'
        )
        acc.feed_chunk(
            b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":" Done."}}\n\n'
        )
        acc.feed_chunk(
            b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n'
        )
        acc.feed_chunk(b"")  # stream closed

        assert acc.is_done() is True
        blocks = acc.get_content_blocks()
        assert len(blocks) == 1
        assert blocks[0].block_type == "thinking"
        assert blocks[0].text == "Analyzing the schema... Done."


class TestOpenAIAccumulator:
    def test_full_openai_stream_reconstruction(self) -> None:
        """Verify OpenAI text, finish_reason, and usage extraction."""
        acc = OpenAIAccumulator()

        chunks = [
            b'data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{"role":"assistant","content":"Greetings"}}]}\n\n',
            b'data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{"content":" human."}}]}\n\n',
            b'data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n',
            b'data: {"id":"chatcmpl-1","choices":[],"usage":{"prompt_tokens":45,"completion_tokens":12,"total_tokens":57,"prompt_tokens_details":{"cached_tokens":20}}}\n\n',
            b"data: [DONE]\n\n",
        ]

        for chunk in chunks:
            acc.feed_chunk(chunk)

        assert acc.is_done() is True
        assert acc.get_stop_reason() == "stop"

        usage = acc.get_usage()
        assert usage.input_tokens == 45
        assert usage.output_tokens == 12
        assert usage.cache_read_input_tokens == 20

        blocks = acc.get_content_blocks()
        assert len(blocks) == 1
        assert blocks[0].block_type == "text"
        assert blocks[0].text == "Greetings human."

    def test_openai_tool_calls_and_reasoning(self) -> None:
        """Verify multi-chunk tool calls and reasoning_content accumulation."""
        acc = OpenAIAccumulator()

        chunks = [
            b'data: {"choices":[{"index":0,"delta":{"reasoning_content":"Let\'s calculate."}}]}\n\n',
            b'data: {"choices":[{"index":0,"delta":{"reasoning_content":" Step 1 completed."}}]}\n\n',
            b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_123","type":"function","function":{"name":"calc","arguments":"{\\"expr\\": "}}]}}]}\n\n',
            b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"2 + 2\\"}"}}]}}]}\n\n',
            b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":80,"completion_tokens":30,"completion_tokens_details":{"reasoning_tokens":15}}}\n\n',
            b"data: [DONE]\n\n",
        ]

        for chunk in chunks:
            acc.feed_chunk(chunk)

        assert acc.is_done() is True
        assert acc.get_stop_reason() == "tool_calls"
        assert acc.get_usage().reasoning_tokens == 15

        blocks = acc.get_content_blocks()
        assert len(blocks) == 2

        thinking_block = blocks[0]
        assert thinking_block.block_type == "thinking"
        assert thinking_block.text == "Let's calculate. Step 1 completed."

        tool_block = blocks[1]
        assert tool_block.block_type == "tool_use"
        assert tool_block.tool_id == "call_123"
        assert tool_block.tool_name == "calc"
        assert tool_block.parsed_input == {"expr": "2 + 2"}


class TestGeminiAccumulator:
    def test_gemini_stream_reconstruction(self) -> None:
        """Verify Gemini stream reconstruction with text and usageMetadata."""
        acc = GeminiAccumulator()

        chunk1 = (
            b'data: {"candidates": [{"content": {"parts": [{"text": "Hello from "}], "role": "model"}, "index": 0}]}\n\n'
        )
        chunk2 = (
            b'data: {"candidates": [{"content": {"parts": [{"text": "Gemini!"}], "role": "model"}, "finishReason": "STOP", "index": 0}], '
            b'"usageMetadata": {"promptTokenCount": 50, "candidatesTokenCount": 10, "totalTokenCount": 60, "cachedContentTokenCount": 15}}\n\n'
        )

        acc.feed_chunk(chunk1)
        assert acc.is_done() is False

        acc.feed_chunk(chunk2)
        assert acc.is_done() is True
        assert acc.get_stop_reason() == "STOP"

        usage = acc.get_usage()
        assert usage.input_tokens == 50
        assert usage.output_tokens == 10
        assert usage.cache_read_input_tokens == 15

        blocks = acc.get_content_blocks()
        assert len(blocks) == 1
        assert blocks[0].block_type == "text"
        assert blocks[0].text == "Hello from Gemini!"

    def test_gemini_thinking_and_function_call(self) -> None:
        """Verify Gemini thinking parts and functionCall handling."""
        acc = GeminiAccumulator()

        chunk1 = (
            b'data: {"candidates": [{"content": {"parts": [{"thought": true, "text": "Planning query..."}], "role": "model"}}]}\n\n'
        )
        chunk2 = (
            b'data: {"candidates": [{"content": {"parts": [{"thought": true, "text": " Done planning."}], "role": "model"}}]}\n\n'
        )
        chunk3 = (
            b'data: {"candidates": [{"content": {"parts": [{"functionCall": {"name": "search_db", "args": {"limit": 10}}}], "role": "model"}, "finishReason": "STOP"}], '
            b'"usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20, "candidatesTokensDetails": [{"modality": "THINKING", "tokenCount": 12}]}}\n\n'
        )

        acc.feed_chunk(chunk1)
        acc.feed_chunk(chunk2)
        acc.feed_chunk(chunk3)

        assert acc.is_done() is True
        assert acc.get_stop_reason() == "STOP"
        assert acc.get_usage().reasoning_tokens == 12

        blocks = acc.get_content_blocks()
        assert len(blocks) == 2

        thinking = blocks[0]
        assert thinking.block_type == "thinking"
        assert thinking.text == "Planning query... Done planning."

        fn = blocks[1]
        assert fn.block_type == "tool_use"
        assert fn.tool_name == "search_db"
        assert fn.parsed_input == {"limit": 10}
