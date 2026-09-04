"""Length-prefixed framing for IPC transmission over Unix Domain Sockets.

Frames consist of:
- 4-byte big-endian unsigned 32-bit integer (uint32) indicating payload length
- Raw payload bytes
"""

import struct

HEADER_SIZE = 4
MAX_PAYLOAD_SIZE = 0xFFFFFFFF  # Maximum value for 32-bit unsigned int (4 GiB - 1)


def encode_frame(payload: bytes) -> bytes:
    """Encode a byte payload into a 4-byte length-prefixed frame.

    Args:
        payload: Raw bytes to encode.

    Returns:
        Frame consisting of 4-byte big-endian length prefix followed by payload.

    Raises:
        TypeError: If payload is not bytes.
        ValueError: If payload length exceeds maximum 32-bit unsigned int.
    """
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError(f"Payload must be bytes or bytearray, got {type(payload).__name__}")

    length = len(payload)
    if length > MAX_PAYLOAD_SIZE:
        raise ValueError(
            f"Payload size ({length} bytes) exceeds maximum allowable frame size "
            f"({MAX_PAYLOAD_SIZE} bytes)"
        )

    return struct.pack(">I", length) + bytes(payload)


def decode_frame_header(header: bytes) -> int:
    """Decode a 4-byte frame header into expected payload length.

    Args:
        header: 4-byte header buffer.

    Returns:
        Payload length as an integer.

    Raises:
        ValueError: If header length is not exactly 4 bytes.
    """
    if len(header) != HEADER_SIZE:
        raise ValueError(f"Header must be exactly {HEADER_SIZE} bytes, got {len(header)} bytes")

    (length,) = struct.unpack(">I", header)
    return length


# Alias for compatibility with beads specification
frame_encode = encode_frame
