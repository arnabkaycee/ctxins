"""Unit tests for asynchronous Unix Domain Socket server."""

import asyncio
import os
import stat
import tempfile
import uuid
from typing import Any

import pytest

from src.core.server.framer import encode_frame
from src.core.server.uds_server import UDSFrameServer
from src.schema.wire import WireEnvelope, WireEventType


@pytest.fixture
def socket_path():
    """Generate a short unique temporary socket path under /tmp to respect macOS 104-char AF_UNIX limit."""
    sock_dir = tempfile.mkdtemp(prefix="ctxins_", dir="/tmp")
    sock_file = os.path.join(sock_dir, f"{uuid.uuid4().hex[:8]}.sock")
    yield sock_file
    # Cleanup after test
    if os.path.exists(sock_file):
        try:
            os.unlink(sock_file)
        except OSError:
            pass
    if os.path.exists(sock_dir):
        try:
            os.rmdir(sock_dir)
        except OSError:
            pass


async def test_server_startup_shutdown_and_permissions(socket_path: str):
    """Verify server creates socket with 0600 permissions and cleans it up on stop."""
    received = []

    async def callback(data: Any) -> None:
        received.append(data)

    server = UDSFrameServer(socket_path=socket_path, on_turn_callback=callback)
    assert not server.is_running

    await server.start()
    assert server.is_running
    assert os.path.exists(socket_path)

    # Verify POSIX permissions are 0600 (read/write by owner only)
    file_stat = os.stat(socket_path)
    mode = stat.S_IMODE(file_stat.st_mode)
    assert mode == 0o600, f"Expected 0600 mode, got {oct(mode)}"

    await server.stop()
    assert not server.is_running
    assert not os.path.exists(socket_path)


async def test_stale_socket_cleanup_on_start(socket_path: str):
    """Verify server unlinks preexisting stale socket file before binding."""
    # Create stale socket dummy file
    with open(socket_path, "w") as f:
        f.write("stale socket placeholder")
    assert os.path.exists(socket_path)

    received = []

    async def callback(data: Any) -> None:
        received.append(data)

    server = UDSFrameServer(socket_path=socket_path, on_turn_callback=callback)
    await server.start()
    assert server.is_running

    # Client can connect to newly rebound socket
    _reader, writer = await asyncio.open_unix_connection(socket_path)
    writer.close()
    await writer.wait_closed()

    await server.stop()
    assert not os.path.exists(socket_path)


async def test_receive_wire_envelope(socket_path: str):
    """Verify client can transmit framed WireEnvelope and server dispatches it."""
    received_envelopes: list[WireEnvelope] = []
    received_event = asyncio.Event()

    async def on_turn(envelope: WireEnvelope | dict[str, Any]) -> None:
        if isinstance(envelope, WireEnvelope):
            received_envelopes.append(envelope)
            received_event.set()

    async with UDSFrameServer(socket_path=socket_path, on_turn_callback=on_turn):
        env = WireEnvelope(
            event_type=WireEventType.TURN_COMPLETED,
            correlation_id="corr-999",
            session_id="sess-888",
            timestamp=1700000500.0,
            payload={"turn_index": 2, "tokens": 150},
        )

        _reader, writer = await asyncio.open_unix_connection(socket_path)
        frame_bytes = encode_frame(env.to_bytes())
        writer.write(frame_bytes)
        await writer.drain()

        # Wait for callback dispatch
        await asyncio.wait_for(received_event.wait(), timeout=2.0)

        writer.close()
        await writer.wait_closed()

    assert len(received_envelopes) == 1
    received = received_envelopes[0]
    assert received.event_type == WireEventType.TURN_COMPLETED
    assert received.correlation_id == "corr-999"
    assert received.session_id == "sess-888"
    assert received.timestamp == 1700000500.0
    assert received.payload == {"turn_index": 2, "tokens": 150}


async def test_receive_generic_json_dict(socket_path: str):
    """Verify server unmarshals generic JSON payloads when not matching WireEnvelope."""
    import json

    received_payloads: list[dict[str, Any]] = []
    received_event = asyncio.Event()

    async def on_turn(payload: WireEnvelope | dict[str, Any]) -> None:
        if isinstance(payload, dict):
            received_payloads.append(payload)
            received_event.set()

    payload_dict = {"custom_metric": "latency", "duration_ms": 12.5}

    async with UDSFrameServer(socket_path=socket_path, on_turn_callback=on_turn):
        _reader, writer = await asyncio.open_unix_connection(socket_path)
        raw_bytes = json.dumps(payload_dict).encode("utf-8")
        writer.write(encode_frame(raw_bytes))
        await writer.drain()

        await asyncio.wait_for(received_event.wait(), timeout=2.0)

        writer.close()
        await writer.wait_closed()

    assert len(received_payloads) == 1
    assert received_payloads[0] == payload_dict


async def test_client_disconnect_handling(socket_path: str):
    """Verify server cleanly handles abrupt client disconnects without failure."""
    received = []
    event = asyncio.Event()

    async def on_turn(data: Any) -> None:
        received.append(data)
        event.set()

    async with UDSFrameServer(socket_path=socket_path, on_turn_callback=on_turn):
        # Client 1 connects, sends partial frame header (2 bytes), and disconnects abruptly
        _r1, w1 = await asyncio.open_unix_connection(socket_path)
        w1.write(b"\x00\x00")
        await w1.drain()
        w1.close()
        await w1.wait_closed()

        # Allow server loop to handle client 1 disconnect
        await asyncio.sleep(0.05)

        # Client 2 connects and sends a valid complete frame
        env = WireEnvelope(
            event_type=WireEventType.REQUEST_INITIATED,
            correlation_id="corr-client-2",
            session_id="sess-client-2",
            timestamp=1700000100.0,
            payload={},
        )
        _r2, w2 = await asyncio.open_unix_connection(socket_path)
        w2.write(encode_frame(env.to_bytes()))
        await w2.drain()

        await asyncio.wait_for(event.wait(), timeout=2.0)
        w2.close()
        await w2.wait_closed()

    assert len(received) == 1
    assert received[0].correlation_id == "corr-client-2"


async def test_synchronous_callback_support(socket_path: str):
    """Verify synchronous callback functions are properly invoked."""
    received = []

    def sync_callback(payload: Any) -> None:
        received.append(payload)

    env = WireEnvelope(
        event_type=WireEventType.TURN_COMPLETED,
        correlation_id="sync-corr",
        session_id="sync-sess",
        timestamp=1700000200.0,
        payload={},
    )

    async with UDSFrameServer(socket_path=socket_path, on_turn_callback=sync_callback):
        _r, w = await asyncio.open_unix_connection(socket_path)
        w.write(encode_frame(env.to_bytes()))
        await w.drain()
        w.close()
        await w.wait_closed()

        # Short pause for server to process
        await asyncio.sleep(0.05)

    assert len(received) == 1
    assert received[0].correlation_id == "sync-corr"


async def test_multiple_concurrent_clients(socket_path: str):
    """Verify server accepts and handles concurrent client streams simultaneously."""
    received_ids = set()
    all_done = asyncio.Event()

    async def on_turn(envelope: Any) -> None:
        if isinstance(envelope, WireEnvelope):
            received_ids.add(envelope.correlation_id)
            if len(received_ids) == 6:
                all_done.set()

    async with UDSFrameServer(socket_path=socket_path, on_turn_callback=on_turn):

        async def run_client(prefix: str, count: int) -> None:
            _r, w = await asyncio.open_unix_connection(socket_path)
            for i in range(count):
                env = WireEnvelope(
                    event_type=WireEventType.TURN_COMPLETED,
                    correlation_id=f"{prefix}-{i}",
                    session_id="sess-concurrent",
                    timestamp=1700000000.0 + i,
                    payload={"idx": i},
                )
                w.write(encode_frame(env.to_bytes()))
                await w.drain()
                await asyncio.sleep(0.01)
            w.close()
            await w.wait_closed()

        # Run two clients concurrently sending 3 frames each
        await asyncio.gather(
            run_client("client-a", 3),
            run_client("client-b", 3),
        )

        await asyncio.wait_for(all_done.wait(), timeout=3.0)

    expected = {f"client-a-{i}" for i in range(3)} | {f"client-b-{i}" for i in range(3)}
    assert received_ids == expected
