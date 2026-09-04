"""Unit tests for length-prefixed stream framing protocol."""

import pytest

from src.core.server.framer import (
    FrameDecodeError,
    FrameDecoder,
    decode_payload,
    encode_frame,
)
from src.schema.wire import WireEnvelope, WireEventType


def test_encode_frame_format():
    """Verify 4-byte big-endian framing prefix and payload concatenation."""
    payload = b"test payload"
    framed = encode_frame(payload)
    assert len(framed) == 4 + len(payload)
    # First 4 bytes must be big-endian representation of len(payload)
    length = int.from_bytes(framed[:4], "big")
    assert length == len(payload)
    assert framed[4:] == payload


def test_encode_empty_frame():
    """Verify zero-length payload encoding."""
    framed = encode_frame(b"")
    assert framed == b"\x00\x00\x00\x00"


def test_decode_single_complete_frame():
    """Verify decoding a complete frame in a single feed."""
    decoder = FrameDecoder()
    payload = b"Hello World"
    framed = encode_frame(payload)

    frames = decoder.feed(framed)
    assert frames == [payload]
    assert decoder.is_empty()
    assert decoder.buffer_size == 0


def test_decode_split_length_header():
    """Verify decoder accumulates partial length prefix across multiple byte chunks."""
    decoder = FrameDecoder()
    payload = b"Split Header Test"
    framed = encode_frame(payload)

    # Feed 1 byte at a time for the 4-byte header
    assert decoder.feed(framed[0:1]) == []
    assert decoder.buffer_size == 1
    assert decoder.feed(framed[1:2]) == []
    assert decoder.buffer_size == 2
    assert decoder.feed(framed[2:3]) == []
    assert decoder.buffer_size == 3
    # 4th header byte completes length prefix, but payload is still missing
    assert decoder.feed(framed[3:4]) == []
    assert decoder.buffer_size == 4

    # Now feed the payload in one chunk
    frames = decoder.feed(framed[4:])
    assert frames == [payload]
    assert decoder.is_empty()


def test_decode_split_payload_chunks():
    """Verify decoder handles arbitrary payload fragmentation."""
    decoder = FrameDecoder()
    payload = b"Arbitrary chunk fragmentation across network packets"
    framed = encode_frame(payload)

    # Header + first 5 bytes of payload
    assert decoder.feed(framed[:9]) == []
    assert decoder.buffer_size == 9

    # Next 10 bytes
    assert decoder.feed(framed[9:19]) == []
    assert decoder.buffer_size == 19

    # Remainder of payload
    frames = decoder.feed(framed[19:])
    assert frames == [payload]
    assert decoder.is_empty()


def test_decode_byte_by_byte():
    """Verify decoder functions properly when receiving 1 byte per feed."""
    decoder = FrameDecoder()
    payload = b"Stream Byte by Byte"
    framed = encode_frame(payload)

    frames = []
    for b in framed:
        frames.extend(decoder.feed(bytes([b])))

    assert frames == [payload]
    assert decoder.is_empty()


def test_decode_multiple_back_to_back_frames():
    """Verify multiple frames received in a single chunk are all extracted."""
    decoder = FrameDecoder()
    f1 = encode_frame(b"frame-1")
    f2 = encode_frame(b"frame-2")
    f3 = encode_frame(b"frame-3-longer-content")

    combined = f1 + f2 + f3
    frames = decoder.feed(combined)

    assert frames == [b"frame-1", b"frame-2", b"frame-3-longer-content"]
    assert decoder.is_empty()


def test_decode_partial_and_concatenated_frames():
    """Verify handling of complete frames followed by partial trailing frame."""
    decoder = FrameDecoder()
    f1 = encode_frame(b"first")
    f2 = encode_frame(b"second-part-of-chunk")

    # Feed f1 and half of f2
    split_point = len(f1) + 6
    frames = decoder.feed((f1 + f2)[:split_point])
    assert frames == [b"first"]
    assert not decoder.is_empty()

    # Feed the rest of f2
    frames = decoder.feed((f1 + f2)[split_point:])
    assert frames == [b"second-part-of-chunk"]
    assert decoder.is_empty()


def test_decode_empty_payload_frames():
    """Verify decoding multiple zero-length payload frames."""
    decoder = FrameDecoder()
    f1 = encode_frame(b"")
    f2 = encode_frame(b"content")
    f3 = encode_frame(b"")

    frames = decoder.feed(f1 + f2 + f3)
    assert frames == [b"", b"content", b""]
    assert decoder.is_empty()


def test_oversized_frame_raises_error():
    """Verify FrameDecodeError is raised when length prefix exceeds max_frame_size."""
    decoder = FrameDecoder(max_frame_size=100)
    oversized_frame = encode_frame(b"x" * 101)

    with pytest.raises(FrameDecodeError, match="exceeds max allowable size"):
        decoder.feed(oversized_frame)


def test_decoder_clear():
    """Verify clear() resets pending buffer state."""
    decoder = FrameDecoder()
    decoder.feed(b"\x00\x00\x00\x10partial")
    assert decoder.buffer_size > 0
    assert not decoder.is_empty()

    decoder.clear()
    assert decoder.buffer_size == 0
    assert decoder.is_empty()


def test_decode_payload_wire_envelope():
    """Verify decode_payload unmarshals valid WireEnvelope JSON."""
    envelope = WireEnvelope(
        event_type=WireEventType.TURN_COMPLETED,
        correlation_id="corr-123",
        session_id="sess-456",
        timestamp=1700000000.0,
        payload={"result": "success", "tokens": 42},
    )
    raw_bytes = envelope.to_bytes()
    decoded = decode_payload(raw_bytes)

    assert isinstance(decoded, WireEnvelope)
    assert decoded.event_type == WireEventType.TURN_COMPLETED
    assert decoded.correlation_id == "corr-123"
    assert decoded.session_id == "sess-456"
    assert decoded.timestamp == 1700000000.0
    assert decoded.payload == {"result": "success", "tokens": 42}


def test_decode_payload_generic_json_dict():
    """Verify decode_payload falls back to raw dict for non-WireEnvelope JSON."""
    raw_dict = {"custom_event": "telemetry", "metric": 99.5}
    import json

    raw_bytes = json.dumps(raw_dict).encode("utf-8")
    decoded = decode_payload(raw_bytes)

    assert isinstance(decoded, dict)
    assert decoded == raw_dict


def test_decode_payload_invalid_data():
    """Verify decode_payload raises FrameDecodeError on invalid UTF-8 and invalid JSON."""
    # Invalid UTF-8
    with pytest.raises(FrameDecodeError, match="not valid UTF-8"):
        decode_payload(b"\xff\xfe\xfd")

    # Invalid JSON
    with pytest.raises(FrameDecodeError, match="not valid JSON"):
        decode_payload(b"this is not json {")
