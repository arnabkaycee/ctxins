"""Asynchronous Unix Domain Socket server for framed IPC telemetry."""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import stat
from typing import Any, Awaitable, Callable

from src.core.server.framer import (
    DEFAULT_MAX_FRAME_SIZE,
    FrameDecodeError,
    FrameDecoder,
    decode_payload,
)
from src.schema.wire import WireEnvelope

logger = logging.getLogger(__name__)

TurnCallback = Callable[[WireEnvelope | dict[str, Any]], Awaitable[None] | None]


class UDSFrameServer:
    """Asynchronous Unix Domain Socket server reading 4-byte length-prefixed frames.

    Binds to a specified socket path with secure permissions (0600) and dispatches
    incoming telemetry frames to an asynchronous or synchronous turn callback.
    """

    def __init__(
        self,
        socket_path: str,
        on_turn_callback: TurnCallback,
        max_frame_size: int = DEFAULT_MAX_FRAME_SIZE,
    ) -> None:
        """Initialize UDSFrameServer.

        Args:
            socket_path: Filesystem path to bind the Unix domain socket.
            on_turn_callback: Callable receiving parsed WireEnvelope or JSON dict.
            max_frame_size: Maximum allowable payload size per frame in bytes.
        """
        self.socket_path = os.path.abspath(socket_path)
        self.on_turn_callback = on_turn_callback
        self.max_frame_size = max_frame_size

        self._server: asyncio.Server | None = None
        self._active_writers: set[asyncio.StreamWriter] = set()
        self._running: bool = False

    @property
    def is_running(self) -> bool:
        """Return True if the server is active and accepting connections."""
        return self._running and self._server is not None and self._server.is_serving()

    async def start(self) -> None:
        """Start the UDS server.

        Removes stale socket if present, creates parent directory, sets 0600
        permissions, and begins accepting connections.
        """
        if self.is_running:
            logger.warning("UDSFrameServer is already running on %s", self.socket_path)
            return

        # Ensure parent directory exists
        sock_dir = os.path.dirname(self.socket_path)
        if sock_dir:
            os.makedirs(sock_dir, exist_ok=True)

        # Remove stale socket file if it exists
        self._cleanup_socket_file()

        # Securely bind socket with 0600 permissions
        old_umask = os.umask(0o077)
        try:
            self._server = await asyncio.start_unix_server(
                self._handle_client,
                path=self.socket_path,
            )
        finally:
            os.umask(old_umask)

        # Explicitly enforce 0600 permissions
        try:
            os.chmod(self.socket_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError as e:
            logger.warning("Failed to chmod socket file %s: %s", self.socket_path, e)

        self._running = True
        logger.info("UDSFrameServer listening on %s (0600)", self.socket_path)

    async def stop(self) -> None:
        """Stop the UDS server and clean up socket files and connections."""
        if not self._running and self._server is None:
            return

        self._running = False

        # Close all active client connections
        for writer in list(self._active_writers):
            try:
                writer.close()
            except Exception:
                pass

        if self._active_writers:
            close_tasks = []
            for writer in list(self._active_writers):
                try:
                    close_tasks.append(writer.wait_closed())
                except Exception:
                    pass
            if close_tasks:
                await asyncio.gather(*close_tasks, return_exceptions=True)
            self._active_writers.clear()

        # Stop accepting new connections and close server
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        # Clean up socket file from filesystem
        self._cleanup_socket_file()
        logger.info("UDSFrameServer stopped on %s", self.socket_path)

    def _cleanup_socket_file(self) -> None:
        """Safely remove socket file if present on filesystem."""
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except OSError as e:
                logger.debug("Failed to remove socket file %s: %s", self.socket_path, e)

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle incoming client socket connection."""
        self._active_writers.add(writer)
        decoder = FrameDecoder(max_frame_size=self.max_frame_size)

        try:
            while self._running:
                chunk = await reader.read(4096)
                if not chunk:
                    # Client disconnected
                    break

                try:
                    frames = decoder.feed(chunk)
                except FrameDecodeError as e:
                    logger.warning("Frame decoding error from client: %s", e)
                    break

                for frame in frames:
                    await self._dispatch_frame(frame)

        except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
            pass
        except Exception as e:
            logger.error("Unexpected error handling UDS client: %s", e, exc_info=True)
        finally:
            self._active_writers.discard(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _dispatch_frame(self, frame_bytes: bytes) -> None:
        """Unmarshal and dispatch frame to turn callback."""
        try:
            payload = decode_payload(frame_bytes)
        except FrameDecodeError as e:
            logger.warning("Failed to decode frame payload (%d bytes): %s", len(frame_bytes), e)
            return

        try:
            result = self.on_turn_callback(payload)
            if inspect.isawaitable(result):
                await result
        except Exception as e:
            logger.error("Error executing on_turn_callback: %s", e, exc_info=True)

    async def __aenter__(self) -> UDSFrameServer:
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.stop()
