"""Non-blocking Unix Domain Socket client for shipping telemetry frames."""

import logging
import select
import socket
import threading
from typing import Optional

from src.interceptor.egress.framing import encode_frame
from src.interceptor.egress.ring_buffer import BoundedRingBuffer

logger = logging.getLogger(__name__)


class UDSClient:
    """Non-blocking length-prefixed Unix Domain Socket writer.

    Reads payloads from a BoundedRingBuffer, frames them with a 4-byte
    big-endian length prefix, and transmits them over a Unix Domain Socket.
    Runs a background daemon thread that handles non-blocking I/O, socket
    disconnections, backoff, and automatic reconnection.
    """

    def __init__(
        self,
        socket_path: str,
        buffer: BoundedRingBuffer,
        connect_retry_interval: float = 0.5,
        reconnect_backoff: float = 0.1,
        poll_interval: float = 0.005,
    ):
        """Initialize UDSClient.

        Args:
            socket_path: Path to the target Unix Domain Socket.
            buffer: BoundedRingBuffer from which to pop items.
            connect_retry_interval: Seconds to wait between connection attempts
                when socket server is not available.
            reconnect_backoff: Seconds to wait after a connection drops before
                attempting reconnection.
            poll_interval: Seconds to wait when the ring buffer is empty before
                checking again.
        """
        self.socket_path = socket_path
        self.buffer = buffer
        self.connect_retry_interval = connect_retry_interval
        self.reconnect_backoff = reconnect_backoff
        self.poll_interval = poll_interval

        self.sock: Optional[socket.socket] = None
        self.running: bool = False
        self._stop_event = threading.Event()
        self._worker: Optional[threading.Thread] = None

    @property
    def is_connected(self) -> bool:
        """Check if socket is currently connected."""
        return self.sock is not None

    @property
    def is_running(self) -> bool:
        """Check if the client egress worker thread is active."""
        return self.running and self._worker is not None and self._worker.is_alive()

    def start(self) -> None:
        """Start the background daemon egress worker thread."""
        if self._worker is not None and self._worker.is_alive():
            return

        self.running = True
        self._stop_event.clear()
        self._worker = threading.Thread(
            target=self._egress_loop,
            daemon=True,
            name="UDSClientWorker",
        )
        self._worker.start()

    def stop(self, timeout: Optional[float] = 2.0) -> None:
        """Stop the background egress worker thread and close the socket.

        Args:
            timeout: Maximum seconds to wait for worker thread to terminate.
        """
        self.running = False
        self._stop_event.set()

        if self._worker is not None and self._worker is not threading.current_thread():
            self._worker.join(timeout=timeout)
            self._worker = None

        self._disconnect()

    def __enter__(self) -> "UDSClient":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()

    def _connect(self) -> bool:
        """Attempt to establish a non-blocking connection to the Unix domain socket.

        Returns:
            True if connection was established, False otherwise.
        """
        if self.sock is not None:
            return True

        sock = None
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.setblocking(False)
            try:
                sock.connect(self.socket_path)
            except BlockingIOError:
                # Connection in progress; wait for writable
                _, writable, _ = select.select([], [sock], [], 0.1)
                if not writable:
                    sock.close()
                    return False
                err = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if err != 0:
                    sock.close()
                    return False

            self.sock = sock
            return True
        except (OSError, socket.error):
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
            self.sock = None
            return False

    def _disconnect(self) -> None:
        """Close and reset the socket."""
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def _send_frame(self, frame: bytes) -> None:
        """Send framed data over the non-blocking socket.

        Args:
            frame: Complete length-prefixed frame bytes.

        Raises:
            OSError: On write failure or broken connection.
        """
        if self.sock is None:
            raise OSError("Socket not connected")

        total_sent = 0
        frame_len = len(frame)
        while total_sent < frame_len:
            if not self.running or self._stop_event.is_set():
                break

            try:
                sent = self.sock.send(frame[total_sent:])
                if sent == 0:
                    raise OSError("Socket connection broken (send returned 0)")
                total_sent += sent
            except BlockingIOError:
                # Buffer temporarily full; wait briefly for writability
                _, writable, _ = select.select([], [self.sock], [], 0.05)
                if not writable and (not self.running or self._stop_event.is_set()):
                    break

    def _is_socket_alive(self) -> bool:
        """Check if connected socket is still alive and open on peer side."""
        if self.sock is None:
            return False
        try:
            peek = self.sock.recv(1, socket.MSG_PEEK)
            if peek == b"":
                return False
            return True
        except BlockingIOError:
            return True
        except OSError:
            return False

    def _egress_loop(self) -> None:
        """Main loop for daemon worker thread."""
        while self.running and not self._stop_event.is_set():
            if self.sock is None or not self._is_socket_alive():
                self._disconnect()
                if not self._connect():
                    self._stop_event.wait(self.connect_retry_interval)
                    continue

            payload = self.buffer.pop()
            if payload is None:
                self._stop_event.wait(self.poll_interval)
                continue

            frame = encode_frame(payload)
            try:
                self._send_frame(frame)
            except (OSError, socket.error):
                # Socket disconnected or broken pipe; disconnect, put frame back, and back off
                self._disconnect()
                self.buffer.unpop(payload)
                self._stop_event.wait(self.reconnect_backoff)
