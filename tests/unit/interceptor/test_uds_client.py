"""Unit tests for UDSClient egress writer."""

import os
import socket
import struct
import tempfile
import threading
import time
from typing import Optional

import pytest

from src.interceptor.egress.ring_buffer import BoundedRingBuffer
from src.interceptor.egress.uds_client import UDSClient


class MockUDSServer:
    """Lightweight test UDS server receiving 4-byte framed messages."""

    def __init__(self, socket_path: str):
        self.socket_path = socket_path
        self.received: list[bytes] = []
        self.lock = threading.Lock()
        self.running = False
        self.server_sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        self.server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server_sock.bind(self.socket_path)
        self.server_sock.listen(5)
        self.server_sock.settimeout(0.1)
        self.running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while self.running:
            if not self.server_sock:
                break
            try:
                conn, _ = self.server_sock.accept()
            except (socket.timeout, OSError):
                continue

            conn.settimeout(0.2)
            try:
                while self.running:
                    header = self._recv_exact(conn, 4)
                    if not header:
                        break
                    (length,) = struct.unpack(">I", header)
                    payload = self._recv_exact(conn, length)
                    if payload is None:
                        break
                    with self.lock:
                        self.received.append(payload)
            except (socket.timeout, OSError):
                pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    def _recv_exact(self, conn: socket.socket, n: int) -> Optional[bytes]:
        data = bytearray()
        while len(data) < n:
            try:
                chunk = conn.recv(n - len(data))
                if not chunk:
                    return None
                data.extend(chunk)
            except socket.timeout:
                if not self.running:
                    return None
                continue
            except OSError:
                return None
        return bytes(data)

    def stop(self) -> None:
        self.running = False
        if self.server_sock:
            try:
                self.server_sock.close()
            except Exception:
                pass
            self.server_sock = None
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except Exception:
                pass

    def get_received(self) -> list[bytes]:
        with self.lock:
            return list(self.received)


@pytest.fixture
def temp_uds_path():
    """Provide a short temporary socket path suitable for macOS AF_UNIX limits."""
    with tempfile.TemporaryDirectory(dir="/tmp") as tmpdir:
        sock_path = os.path.join(tmpdir, "test.sock")
        yield sock_path
        if os.path.exists(sock_path):
            try:
                os.unlink(sock_path)
            except Exception:
                pass


class TestUDSClient:
    """Tests for UDSClient writer."""

    def test_lifecycle_start_stop(self, temp_uds_path):
        """Test starting, stopping, and idempotent start/stop calls."""
        buf = BoundedRingBuffer(capacity=100)
        client = UDSClient(temp_uds_path, buf, connect_retry_interval=0.05)

        assert not client.is_running
        assert not client.is_connected

        client.start()
        assert client.is_running
        # Idempotent start
        client.start()
        assert client.is_running

        client.stop()
        assert not client.is_running
        assert not client.is_connected
        # Idempotent stop
        client.stop()

    def test_context_manager(self, temp_uds_path):
        """Test UDSClient as context manager."""
        buf = BoundedRingBuffer(capacity=100)
        with UDSClient(temp_uds_path, buf, connect_retry_interval=0.05) as client:
            assert client.is_running
        assert not client.is_running

    def test_write_frames_to_server(self, temp_uds_path):
        """Test client successfully writes length-prefixed frames to server."""
        server = MockUDSServer(temp_uds_path)
        server.start()

        buf = BoundedRingBuffer(capacity=100)
        client = UDSClient(
            temp_uds_path,
            buf,
            connect_retry_interval=0.02,
            poll_interval=0.005,
        )
        client.start()

        try:
            expected_messages = [f"message-{i}".encode("utf-8") for i in range(20)]
            for msg in expected_messages:
                buf.push(msg)

            # Wait for server to receive all messages
            timeout = 3.0
            deadline = time.time() + timeout
            while time.time() < deadline:
                if len(server.get_received()) == len(expected_messages):
                    break
                time.sleep(0.02)

            received = server.get_received()
            assert received == expected_messages
        finally:
            client.stop()
            server.stop()

    def test_dropping_frames_gracefully_when_server_down(self, temp_uds_path):
        """Verify client drops frames via ring buffer when server is unreachable.

        Client should not crash or block, and buffer should track dropped frames.
        """
        buf = BoundedRingBuffer(capacity=10)
        client = UDSClient(
            temp_uds_path,
            buf,
            connect_retry_interval=0.05,
            poll_interval=0.005,
        )
        client.start()

        try:
            assert not client.is_connected

            # Push 25 items to buffer with capacity 10
            for i in range(25):
                buf.push(f"item-{i}".encode("utf-8"))

            time.sleep(0.1)
            assert client.is_running
            assert not client.is_connected
            assert len(buf) == 10
            assert buf.dropped_count == 15
        finally:
            client.stop()

    def test_reconnect_after_server_restart(self, temp_uds_path):
        """Test that client reconnects after server restarts and resumes transmitting."""
        server = MockUDSServer(temp_uds_path)
        server.start()

        buf = BoundedRingBuffer(capacity=100)
        client = UDSClient(
            temp_uds_path,
            buf,
            connect_retry_interval=0.02,
            reconnect_backoff=0.02,
            poll_interval=0.005,
        )
        client.start()

        try:
            # Send batch 1
            batch1 = [b"batch1-1", b"batch1-2", b"batch1-3"]
            for m in batch1:
                buf.push(m)

            deadline = time.time() + 2.0
            while time.time() < deadline:
                if len(server.get_received()) == len(batch1):
                    break
                time.sleep(0.02)

            assert server.get_received() == batch1

            # Stop server (simulate server crash)
            server.stop()
            time.sleep(0.1)

            # Enqueue batch 2 while server is offline
            batch2 = [b"batch2-1", b"batch2-2"]
            for m in batch2:
                buf.push(m)

            # Restart server on the same socket path
            server = MockUDSServer(temp_uds_path)
            server.start()

            # Client should automatically reconnect and flush batch2
            deadline = time.time() + 3.0
            while time.time() < deadline:
                if len(server.get_received()) == len(batch2):
                    break
                time.sleep(0.02)

            assert server.get_received() == batch2
        finally:
            client.stop()
            server.stop()
