"""Integration tests for Unix Domain Socket IPC pipeline between UDSClient and UDSFrameServer."""

import asyncio
import os
import shutil
import tempfile
import time
import uuid
from typing import Any, List

import pytest

from src.core.server.uds_server import UDSFrameServer
from src.interceptor.egress.ring_buffer import BoundedRingBuffer
from src.interceptor.egress.uds_client import UDSClient
from src.schema.wire import WireEnvelope, WireEventType


@pytest.fixture
def socket_dir():
    """Create short temp directory for AF_UNIX socket (macOS 104-char limit)."""
    d = tempfile.mkdtemp(prefix="ctx_", dir="/tmp")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def socket_path(socket_dir: str):
    """Generate a unique socket path within the temporary directory."""
    return os.path.join(socket_dir, f"{uuid.uuid4().hex[:8]}.sock")


def _make_envelope(
    turn_idx: int,
    event_type: WireEventType = WireEventType.TURN_COMPLETED,
    payload_size: int = 50,
) -> WireEnvelope:
    """Helper to construct WireEnvelope test instances."""
    data = "x" * payload_size
    return WireEnvelope(
        event_type=event_type,
        correlation_id=f"corr_{turn_idx}",
        session_id="sess_integration",
        timestamp=time.time(),
        payload={
            "index": turn_idx,
            "provider": "anthropic",
            "model": "claude-3-5-sonnet",
            "data": data,
        },
    )


@pytest.mark.asyncio
async def test_uds_client_server_basic_transmission(socket_path: str):
    """Verify standard end-to-end transmission of WireEnvelopes over UDS."""
    received: List[WireEnvelope] = []
    receive_event = asyncio.Event()

    async def on_turn(item: Any) -> None:
        if isinstance(item, WireEnvelope):
            received.append(item)
            if len(received) >= 5:
                receive_event.set()

    server = UDSFrameServer(socket_path=socket_path, on_turn_callback=on_turn)
    await server.start()

    ring_buffer = BoundedRingBuffer(capacity=100)
    client = UDSClient(
        socket_path=socket_path,
        buffer=ring_buffer,
        connect_retry_interval=0.05,
        reconnect_backoff=0.05,
        poll_interval=0.005,
    )
    client.start()

    try:
        # Send 5 envelopes of different types
        envelopes = [
            _make_envelope(0, WireEventType.REQUEST_INITIATED),
            _make_envelope(1, WireEventType.TURN_COMPLETED),
            _make_envelope(2, WireEventType.TURN_ERROR),
            _make_envelope(3, WireEventType.SYSTEM_TELEMETRY),
            _make_envelope(4, WireEventType.TURN_COMPLETED),
        ]

        for env in envelopes:
            ring_buffer.push(env.to_bytes())

        # Wait for all envelopes to be delivered
        await asyncio.wait_for(receive_event.wait(), timeout=3.0)

        assert len(received) == 5
        for i in range(5):
            assert received[i].correlation_id == f"corr_{i}"
            assert received[i].event_type == envelopes[i].event_type
            assert received[i].payload["data"] == "x" * 50
    finally:
        client.stop()
        await server.stop()


@pytest.mark.asyncio
async def test_uds_client_reconnection_when_server_starts_later(socket_path: str):
    """Verify UDSClient buffers frames when server is down and drains once server starts."""
    ring_buffer = BoundedRingBuffer(capacity=100)
    client = UDSClient(
        socket_path=socket_path,
        buffer=ring_buffer,
        connect_retry_interval=0.05,
        reconnect_backoff=0.05,
        poll_interval=0.005,
    )
    client.start()

    # Client starts with no server running; push 3 envelopes
    for i in range(3):
        ring_buffer.push(_make_envelope(i).to_bytes())

    assert len(ring_buffer) == 3
    assert not client.is_connected

    # Now start server
    received: List[WireEnvelope] = []
    receive_event = asyncio.Event()

    async def on_turn(item: Any) -> None:
        if isinstance(item, WireEnvelope):
            received.append(item)
            if len(received) >= 3:
                receive_event.set()

    server = UDSFrameServer(socket_path=socket_path, on_turn_callback=on_turn)
    await server.start()

    try:
        await asyncio.wait_for(receive_event.wait(), timeout=3.0)
        assert len(received) == 3
        for i in range(3):
            assert received[i].correlation_id == f"corr_{i}"
    finally:
        client.stop()
        await server.stop()


@pytest.mark.asyncio
async def test_uds_server_restart_and_client_auto_reconnection(socket_path: str):
    """Verify client detects server disconnect, backs off, and reconnects on server restart."""
    received: List[WireEnvelope] = []
    first_batch_event = asyncio.Event()
    second_batch_event = asyncio.Event()

    async def on_turn(item: Any) -> None:
        if isinstance(item, WireEnvelope):
            received.append(item)
            if len(received) == 2:
                first_batch_event.set()
            elif len(received) >= 5:
                second_batch_event.set()

    server = UDSFrameServer(socket_path=socket_path, on_turn_callback=on_turn)
    await server.start()

    ring_buffer = BoundedRingBuffer(capacity=100)
    client = UDSClient(
        socket_path=socket_path,
        buffer=ring_buffer,
        connect_retry_interval=0.05,
        reconnect_backoff=0.05,
        poll_interval=0.005,
    )
    client.start()

    try:
        # Send initial batch of 2 frames
        ring_buffer.push(_make_envelope(0).to_bytes())
        ring_buffer.push(_make_envelope(1).to_bytes())
        await asyncio.wait_for(first_batch_event.wait(), timeout=2.0)
        assert len(received) == 2

        # Abruptly stop server (simulating crash / restart)
        await server.stop()

        # Enqueue more frames while server is offline
        ring_buffer.push(_make_envelope(2).to_bytes())
        ring_buffer.push(_make_envelope(3).to_bytes())
        ring_buffer.push(_make_envelope(4).to_bytes())

        # Restart server on the same socket path
        server = UDSFrameServer(socket_path=socket_path, on_turn_callback=on_turn)
        await server.start()

        # Client should reconnect and deliver remaining frames
        await asyncio.wait_for(second_batch_event.wait(), timeout=3.0)
        assert len(received) == 5
        for i in range(5):
            assert received[i].correlation_id == f"corr_{i}"
    finally:
        client.stop()
        await server.stop()


@pytest.mark.asyncio
async def test_high_throughput_burst(socket_path: str):
    """Test throughput and framing under high-load burst of 200 envelopes."""
    total_frames = 200
    received: List[WireEnvelope] = []
    all_received_event = asyncio.Event()

    async def on_turn(item: Any) -> None:
        if isinstance(item, WireEnvelope):
            received.append(item)
            if len(received) >= total_frames:
                all_received_event.set()

    server = UDSFrameServer(socket_path=socket_path, on_turn_callback=on_turn)
    await server.start()

    ring_buffer = BoundedRingBuffer(capacity=total_frames + 50)
    client = UDSClient(
        socket_path=socket_path,
        buffer=ring_buffer,
        connect_retry_interval=0.05,
        reconnect_backoff=0.05,
        poll_interval=0.001,
    )
    client.start()

    try:
        # Rapid burst enqueue
        for i in range(total_frames):
            ring_buffer.push(_make_envelope(i, payload_size=128).to_bytes())

        await asyncio.wait_for(all_received_event.wait(), timeout=5.0)

        assert len(received) == total_frames
        # Verify sequential ordering and data integrity
        for i in range(total_frames):
            assert received[i].correlation_id == f"corr_{i}"
            assert received[i].payload["index"] == i
    finally:
        client.stop()
        await server.stop()


@pytest.mark.asyncio
async def test_fail_open_buffer_behavior_under_load(socket_path: str):
    """Verify BoundedRingBuffer enforces bounds and drops oldest frames when server is unreachable."""
    capacity = 10
    ring_buffer = BoundedRingBuffer(capacity=capacity)
    total_pushed = 25

    # Push 25 items into capacity 10 buffer with no server
    for i in range(total_pushed):
        ring_buffer.push(_make_envelope(i).to_bytes())

    # Assert bounded capacity enforcement and telemetry
    assert len(ring_buffer) == capacity
    assert ring_buffer.dropped_count == total_pushed - capacity

    # When server starts, only the latest 10 surviving items are drained
    received: List[WireEnvelope] = []
    drain_event = asyncio.Event()

    async def on_turn(item: Any) -> None:
        if isinstance(item, WireEnvelope):
            received.append(item)
            if len(received) >= capacity:
                drain_event.set()

    server = UDSFrameServer(socket_path=socket_path, on_turn_callback=on_turn)
    await server.start()

    client = UDSClient(
        socket_path=socket_path,
        buffer=ring_buffer,
        connect_retry_interval=0.05,
        reconnect_backoff=0.05,
        poll_interval=0.005,
    )
    client.start()

    try:
        await asyncio.wait_for(drain_event.wait(), timeout=3.0)
        assert len(received) == capacity
        # Oldest 15 (0..14) were dropped; remaining are 15..24
        expected_indices = list(range(15, 25))
        for i, idx in enumerate(expected_indices):
            assert received[i].correlation_id == f"corr_{idx}"
    finally:
        client.stop()
        await server.stop()


@pytest.mark.asyncio
async def test_large_payload_handling(socket_path: str):
    """Verify 4-byte framing correctly chunks and reassembles large 100KB payloads."""
    received: List[WireEnvelope] = []
    received_event = asyncio.Event()

    async def on_turn(item: Any) -> None:
        if isinstance(item, WireEnvelope):
            received.append(item)
            received_event.set()

    server = UDSFrameServer(socket_path=socket_path, on_turn_callback=on_turn)
    await server.start()

    ring_buffer = BoundedRingBuffer(capacity=20)
    client = UDSClient(
        socket_path=socket_path,
        buffer=ring_buffer,
        connect_retry_interval=0.05,
        reconnect_backoff=0.05,
        poll_interval=0.005,
    )
    client.start()

    try:
        large_size = 100 * 1024  # 100 KB payload
        large_env = _make_envelope(999, payload_size=large_size)
        ring_buffer.push(large_env.to_bytes())

        await asyncio.wait_for(received_event.wait(), timeout=4.0)

        assert len(received) == 1
        assert received[0].correlation_id == "corr_999"
        assert len(received[0].payload["data"]) == large_size
    finally:
        client.stop()
        await server.stop()
