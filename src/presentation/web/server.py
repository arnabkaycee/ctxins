"""FastAPI server application factory mounting REST APIs, WebSockets, and static assets."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

from src.core.store.session_store import SessionStore
from src.presentation.broadcaster import PresentationBroadcaster
from src.presentation.web.api import create_api_router
from src.presentation.web.ws import WebSocketHub


class NoCacheStaticFiles(StaticFiles):
    """StaticFiles mount ensuring browsers always revalidate local dashboard assets."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


def create_app(
    store: Optional[SessionStore] = None,
    broadcaster: Optional[PresentationBroadcaster] = None,
    granularity: str = "step",
) -> FastAPI:
    """Create and configure the FastAPI web dashboard application.

    Args:
        store: Optional SessionStore instance. Defaults to a new in-memory SessionStore.
        broadcaster: Optional PresentationBroadcaster instance. Defaults to a new PresentationBroadcaster.
        granularity: Turn counting granularity ('step' or 'human').

    Returns:
        Configured FastAPI application instance.
    """
    eff_granularity = (granularity or getattr(store, "granularity", "step") or "step").lower()
    resolved_store = store if store is not None else SessionStore(granularity=eff_granularity)
    resolved_broadcaster = broadcaster if broadcaster is not None else PresentationBroadcaster()

    app = FastAPI(
        title="ctxins Dashboard",
        version="0.1.0",
        description="Context Inspector Real-Time Web Dashboard",
    )

    ws_hub = WebSocketHub(
        broadcaster=resolved_broadcaster, store=resolved_store, granularity=eff_granularity
    )

    # Attach shared instances to app.state
    app.state.store = resolved_store
    app.state.broadcaster = resolved_broadcaster
    app.state.ws_hub = ws_hub
    app.state.granularity = eff_granularity

    # Register REST API router under /api/v1 and /api
    api_router = create_api_router(store=resolved_store, ws_hub=ws_hub, granularity=eff_granularity)
    app.include_router(api_router, prefix="/api/v1")
    app.include_router(api_router, prefix="/api")

    # Mount WebSocket endpoint
    @app.websocket("/ws/live")
    async def live_websocket(websocket: WebSocket) -> None:
        await ws_hub.handle_client(websocket, store=resolved_store)

    # Mount static dashboard assets
    static_dir = Path(__file__).parent / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/", NoCacheStaticFiles(directory=str(static_dir), html=True), name="static")

    return app
