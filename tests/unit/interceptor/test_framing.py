"""Unit tests for UDS framing module."""

import struct

import pytest

from src.interceptor.egress.framing import (
    HEADER_SIZE,
    MAX_PAYLOAD_SIZE,
    decode_frame_header,
    encode_frame,
    frame_encode,
)


class TestFraming:
    """Tests for 4-byte big-endian length-prefixed framing."""

    def test_header_size_constant(self):
        """Verify header size is 4 bytes."""
        assert HEADER_SIZE == 4

    def test_encode_and_decode_empty_payload(self):
        """Test framing of zero-length payload."""
        payload = b""
        frame = encode_frame(payload)
        assert len(frame) == 4
        assert frame[:4] == b"\x00\x00\x00\x00"

        length = decode_frame_header(frame[:4])
        assert length == 0

    def test_encode_and_decode_small_payload(self):
        """Test framing of small payload (10 bytes)."""
        payload = b"0123456789"
        assert len(payload) == 10
        frame = encode_frame(payload)

        assert len(frame) == 4 + 10
        expected_header = struct.pack(">I", 10)
        assert frame[:4] == expected_header
        assert frame[4:] == payload

        length = decode_frame_header(frame[:4])
        assert length == 10

    def test_encode_and_decode_medium_payload(self):
        """Test framing of medium payload (1 KB)."""
        payload = b"M" * 1024
        frame = encode_frame(payload)

        assert len(frame) == 4 + 1024
        assert frame[:4] == struct.pack(">I", 1024)
        assert frame[4:] == payload

        length = decode_frame_header(frame[:4])
        assert length == 1024

    def test_encode_and_decode_large_payload(self):
        """Test framing of large payload (500 KB)."""
        payload = b"L" * 500_000
        frame = encode_frame(payload)

        assert len(frame) == 4 + 500_000
        assert frame[:4] == struct.pack(">I", 500_000)
        assert frame[4:] == payload

        length = decode_frame_header(frame[:4])
        assert length == 500_000

    def test_frame_encode_alias(self):
        """Test that frame_encode is an alias of encode_frame."""
        payload = b"test_alias"
        assert frame_encode(payload) == encode_frame(payload)

    def test_bytearray_support(self):
        """Test that bytearray is accepted and encoded properly."""
        payload = bytearray(b"bytearray_payload")
        frame = encode_frame(payload)
        assert decode_frame_header(frame[:4]) == len(payload)
        assert frame[4:] == bytes(payload)

    def test_decode_header_invalid_length(self):
        """Test that decode_frame_header raises ValueError when header is not 4 bytes."""
        with pytest.raises(ValueError, match="Header must be exactly 4 bytes"):
            decode_frame_header(b"\x00\x00")

        with pytest.raises(ValueError, match="Header must be exactly 4 bytes"):
            decode_frame_header(b"\x00\x00\x00\x00\x01")

        with pytest.raises(ValueError, match="Header must be exactly 4 bytes"):
            decode_frame_header(b"")

    def test_encode_invalid_type(self):
        """Test that encode_frame raises TypeError when payload is not bytes/bytearray."""
        with pytest.raises(TypeError, match="Payload must be bytes"):
            encode_frame("string payload")  # type: ignore

        with pytest.raises(TypeError, match="Payload must be bytes"):
            encode_frame({"key": "value"})  # type: ignore

    def test_encode_exceeds_max_size(self):
        """Test that encode_frame raises ValueError when payload exceeds uint32 max."""
        class MockHugePayload(bytes):
            def __len__(self):
                return MAX_PAYLOAD_SIZE + 1

        huge = MockHugePayload()
        with pytest.raises(ValueError, match="exceeds maximum allowable frame size"):
            encode_frame(huge)
