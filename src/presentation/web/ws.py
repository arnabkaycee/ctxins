"""WebSocket connection hub managing real-time presentation subscribers."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional, Set

from fastapi import WebSocket, WebSocketDisconnect

from src.core.analyzer.scorer import PollutionScorer
from src.core.store.session_store import SessionStore
from src.presentation.broadcaster import PresentationBroadcaster
from src.presentation.events import UIEvent

logger = logging.getLogger(__name__)


class WebSocketHub:
    """Manages active WebSocket client connections and event fan-out."""

    def __init__(
        self,
        broadcaster: Optional[PresentationBroadcaster] = None,
        store: Optional[SessionStore] = None,
    ) -> None:
        """Initialize WebSocketHub.

        Args:
            broadcaster: Optional PresentationBroadcaster instance for event consumption.
            store: Optional SessionStore for hydrating initial connection snapshots.
        """
        self.broadcaster = broadcaster
        self.store = store
        self._clients: Set[WebSocket] = set()
        self._client_sessions: Dict[WebSocket, Optional[str]] = {}

    @property
    def connections(self) -> Set[WebSocket]:
        """Return set of currently active WebSocket client connections."""
        return set(self._clients)

    @property
    def client_count(self) -> int:
        """Return number of currently active WebSocket client connections."""
        return len(self._clients)

    def register(self, websocket: WebSocket, session_id: Optional[str] = None) -> None:
        """Register a connected WebSocket client with an optional session filter.

        Args:
            websocket: Connected WebSocket client.
            session_id: Optional session identifier to scope incoming events.
        """
        self._clients.add(websocket)
        self._client_sessions[websocket] = session_id

    def unregister(self, websocket: WebSocket) -> None:
        """Unregister a disconnected WebSocket client.

        Args:
            websocket: WebSocket client to remove.
        """
        self._clients.discard(websocket)
        self._client_sessions.pop(websocket, None)

    async def broadcast(self, event: UIEvent) -> None:
        """Broadcast UIEvent to all active clients matching session_id or all clients.

        Args:
            event: UIEvent instance to serialize and send.
        """
        if self.broadcaster is not None:
            self.broadcaster.publish_nowait(event)
            # Yield to event loop to allow any active worker tasks to process the queue
            await asyncio.sleep(0)
            return

        # Direct fan-out if no broadcaster is attached
        dead_clients: Set[WebSocket] = set()
        for ws, client_sid in list(self._client_sessions.items()):
            if client_sid is None or not event.session_id or client_sid == event.session_id:
                try:
                    await ws.send_json(event.to_dict())
                except Exception:
                    dead_clients.add(ws)

        for ws in dead_clients:
            self.unregister(ws)

    async def handle_client(
        self,
        websocket: WebSocket,
        store: Optional[SessionStore] = None,
    ) -> None:
        """Handle incoming WebSocket client connection lifecycle.

        Accepts connection, parses session_id query parameter, transmits initial
        SNAPSHOT event, and continuously forwards presentation events.

        Args:
            websocket: Incoming WebSocket connection instance.
            store: Optional SessionStore override. Defaults to self.store.
        """
        await websocket.accept()
        session_id = websocket.query_params.get("session_id")
        self.register(websocket, session_id)

        resolved_store = store or self.store
        if resolved_store is not None:
            target_sid = session_id
            if not target_sid:
                sessions = resolved_store.list_sessions()
                if sessions:
                    target_sid = sessions[-1]

            turns = resolved_store.get_session(target_sid) if target_sid else []
            summary = (
                PollutionScorer.calculate_summary(turns)
                if turns
                else {
                    "totalTurns": 0,
                    "totalInputTokens": 0,
                    "totalOutputTokens": 0,
                    "cachedInputTokens": 0,
                    "cacheHitRatio": 0.0,
                    "totalDurationMs": 0.0,
                    "estimatedCostUSD": 0.0,
                    "pollutionScore": 0.0,
                    "potentialSavingsUSD": 0.0,
                    "activeViolationsCount": 0,
                    "violationsBySeverity": {"INFO": 0, "WARN": 0, "CRITICAL": 0},
                }
            )
            violations = [
                v.to_dict()
                for v in (resolved_store.get_violations(target_sid) if target_sid else [])
            ]
            turns_data = [t.to_dict() for t in (turns or [])]

            snapshot_payload: Dict[str, Any] = {
                "summary": summary,
                "turns": turns_data,
                "violations": violations,
            }
            snapshot_event: Dict[str, Any] = {
                "type": "SNAPSHOT",
                "sessionId": target_sid or "",
                "timestamp": time.time(),
                "payload": snapshot_payload,
                "summary": summary,
                "turns": turns_data,
                "violations": violations,
            }
            try:
                await websocket.send_json(snapshot_event)
            except Exception:
                self.unregister(websocket)
                return

        queue: Optional[asyncio.Queue[UIEvent]] = None
        if self.broadcaster is not None:
            queue = await self.broadcaster.subscribe()

        async def _forward_events() -> None:
            if queue is None:
                return
            while True:
                event = await queue.get()
                try:
                    client_sid = self._client_sessions.get(websocket)
                    if (
                        client_sid is None
                        or not event.session_id
                        or client_sid == event.session_id
                    ):
                        await websocket.send_json(event.to_dict())
                finally:
                    queue.task_done()

        async def _listen_incoming() -> None:
            while True:
                await websocket.receive_text()

        try:
            if queue is not None:
                forward_task = asyncio.create_task(_forward_events())
                listen_task = asyncio.create_task(_listen_incoming())
                done, pending = await asyncio.wait(
                    [forward_task, listen_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
            else:
                await _listen_incoming()
        except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
            pass
        finally:
            if queue is not None and self.broadcaster is not None:
                await self.broadcaster.unsubscribe(queue)
            self.unregister(websocket)
