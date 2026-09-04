"""Web presentation package exporting create_app factory and WebSocketHub."""

from src.presentation.web.server import create_app
from src.presentation.web.ws import WebSocketHub

__all__ = ["create_app", "WebSocketHub"]
