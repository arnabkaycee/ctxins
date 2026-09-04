"""FastAPI server application factory mounting REST APIs, WebSockets, and static assets."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles

from src.core.store.session_store import SessionStore
from src.presentation.broadcaster import PresentationBroadcaster
from src.presentation.web.api import create_api_router
from src.presentation.web.ws import WebSocketHub


def create_app(
    store: Optional[SessionStore] = None,
    broadcaster: Optional[PresentationBroadcaster] = None,
) -> FastAPI:
    """Create and configure the FastAPI web dashboard application.

    Args:
        store: Optional SessionStore instance. Defaults to a new in-memory SessionStore.
        broadcaster: Optional PresentationBroadcaster instance. Defaults to a new PresentationBroadcaster.

    Returns:
        Configured FastAPI application instance.
    """
    resolved_store = store if store is not None else SessionStore()
    resolved_broadcaster = broadcaster if broadcaster is not None else PresentationBroadcaster()

    app = FastAPI(
        title="ctxins Dashboard",
        version="0.1.0",
        description="Context Inspector Real-Time Web Dashboard",
    )

    ws_hub = WebSocketHub(broadcaster=resolved_broadcaster, store=resolved_store)

    # Attach shared instances to app.state
    app.state.store = resolved_store
    app.state.broadcaster = resolved_broadcaster
    app.state.ws_hub = ws_hub

    # Register REST API router under /api/v1
    api_router = create_api_router(store=resolved_store, ws_hub=ws_hub)
    app.include_router(api_router, prefix="/api/v1")

    # Mount WebSocket endpoint
    @app.websocket("/ws/live")
    async def live_websocket(websocket: WebSocket) -> None:
        await ws_hub.handle_client(websocket, store=resolved_store)

    # Mount static dashboard assets
    static_dir = Path(__file__).parent / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app
