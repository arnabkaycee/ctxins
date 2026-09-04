"""Stream framing protocol for length-prefixed IPC frames."""

from __future__ import annotations

import json
import struct
from typing import Any

from src.schema.wire import WireEnvelope

# Default maximum allowable frame size (64 MiB) to avoid memory exhaustion
DEFAULT_MAX_FRAME_SIZE: int = 64 * 1024 * 1024


class FrameDecodeError(Exception):
    """Raised when frame decoding fails due to corrupted protocol or oversized frame."""


def encode_frame(payload: bytes) -> bytes:
    """Encode a byte payload into a 4-byte big-endian length-prefixed frame.

    Args:
        payload: Raw payload bytes to frame.

    Returns:
        Bytes with a 4-byte big-endian uint32 prefix followed by the payload.

    Raises:
        ValueError: If payload length exceeds 32-bit unsigned integer limit.
    """
    length = len(payload)
    if length > 0xFFFFFFFF:
        raise ValueError(f"Payload length {length} exceeds maximum frame length 4,294,967,295")
    return struct.pack(">I", length) + payload


def decode_payload(payload: bytes) -> WireEnvelope | dict[str, Any]:
    """Decode raw frame bytes into either a WireEnvelope or a generic JSON dictionary.

    Args:
        payload: Unframed payload bytes to unmarshal.

    Returns:
        WireEnvelope if payload matches the envelope schema, or dict if general JSON.

    Raises:
        FrameDecodeError: If payload cannot be decoded as valid JSON.
    """
    try:
        decoded_text = payload.decode("utf-8")
    except UnicodeDecodeError as e:
        raise FrameDecodeError(f"Payload is not valid UTF-8: {e}") from e

    try:
        raw_obj = json.loads(decoded_text)
    except json.JSONDecodeError as e:
        raise FrameDecodeError(f"Payload is not valid JSON: {e}") from e

    if isinstance(raw_obj, dict):
        try:
            return WireEnvelope.from_dict(raw_obj)
        except (KeyError, ValueError, TypeError):
            return raw_obj

    # Return raw object if JSON parsed to something other than dict
    return raw_obj  # type: ignore[return-value]


class FrameDecoder:
    """Stream buffer accumulator for 4-byte big-endian length-prefixed frames.

    Maintains internal byte state and handles:
    - Split length headers (1-3 bytes received)
    - Partial payload chunks
    - Multiple back-to-back concatenated frames
    - Zero-length payload frames
    """

    def __init__(self, max_frame_size: int = DEFAULT_MAX_FRAME_SIZE) -> None:
        """Initialize frame decoder.

        Args:
            max_frame_size: Maximum allowable payload size in bytes. Defaults to 64 MiB.
        """
        self.max_frame_size = max_frame_size
        self._buffer = bytearray()

    @property
    def buffer_size(self) -> int:
        """Current number of buffered bytes waiting for completion."""
        return len(self._buffer)

    def is_empty(self) -> bool:
        """Return True if the internal buffer has no pending bytes."""
        return len(self._buffer) == 0

    def clear(self) -> None:
        """Reset internal buffer state."""
        self._buffer.clear()

    def feed(self, chunk: bytes | bytearray | memoryview) -> list[bytes]:
        """Feed an incoming byte chunk and return all complete frames extracted.

        Args:
            chunk: Incoming bytes from transport stream.

        Returns:
            List of extracted complete frame payload bytes (excluding the 4-byte length prefix).

        Raises:
            FrameDecodeError: If frame length header exceeds max_frame_size.
        """
        if chunk:
            self._buffer.extend(chunk)

        frames: list[bytes] = []
        buf = self._buffer

        while True:
            buf_len = len(buf)
            if buf_len < 4:
                # Need at least 4 bytes for length header
                break

            payload_len = struct.unpack_from(">I", buf, 0)[0]
            if payload_len > self.max_frame_size:
                raise FrameDecodeError(
                    f"Frame length {payload_len} exceeds max allowable size {self.max_frame_size}"
                )

            total_len = 4 + payload_len
            if buf_len < total_len:
                # Need more payload bytes
                break

            # Extract payload slice and advance buffer
            frames.append(bytes(buf[4:total_len]))
            del buf[:total_len]

        return frames
