"""Interactive modal dialog displaying agent hook recipes and environment configurations."""

from __future__ import annotations

import base64
import shutil
import subprocess
import sys
from typing import Any

from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


def copy_to_clipboard(text: str) -> bool:
    """Copy text to clipboard using pbcopy, pyperclip, or OSC 52 ANSI sequence."""
    if shutil.which("pbcopy"):
        try:
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
            return True
        except Exception:
            pass

    try:
        import pyperclip  # type: ignore

        pyperclip.copy(text)
        return True
    except Exception:
        pass

    try:
        encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
        sys.stdout.write(f"\x1b]52;c;{encoded}\x07")
        sys.stdout.flush()
        return True
    except Exception:
        pass

    return False


class HookModalScreen(ModalScreen[None]):
    """Modal screen displaying instructions to hook any agent to ctxins."""

    DEFAULT_CSS = """
    HookModalScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.7);
    }

    #modal-container {
        width: 82;
        height: auto;
        border: solid #58a6ff;
        background: #161b22;
        padding: 1 2;
    }

    .modal-title {
        color: #58a6ff;
        text-style: bold;
        margin-bottom: 1;
    }

    .recipe-box {
        background: #0d1117;
        border: solid #30363d;
        padding: 1;
        margin-bottom: 1;
    }

    .button-bar {
        margin-top: 1;
        height: 3;
        align: right middle;
    }
    """

    BINDINGS = [
        ("escape", "dismiss", "Close"),
        ("c", "copy_env", "Copy Env"),
    ]

    def __init__(self, proxy_port: int = 8080, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.proxy_port = proxy_port

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-container"):
            yield Label("⚡ Connect Any Agent to ctxins", classes="modal-title")

            recipe1 = (
                f"[bold cyan]1. Standard CLI Agent[/bold cyan] (Claude Code, Antigravity agy, Aider):\n"
                f"   Run in your agent terminal:\n"
                f"   [bold yellow]$ eval $(ctxins env --proxy-port {self.proxy_port}) && agy[/bold yellow]"
            )
            yield Static(recipe1, classes="recipe-box")

            recipe2 = (
                f"[bold cyan]2. Local Agent / Local LLM on Custom Port[/bold cyan] (e.g. :8000, :1234):\n"
                f"   Point your agent base URL to ctxins gateway:\n"
                f'   [bold yellow]OPENAI_BASE_URL="http://127.0.0.1:{self.proxy_port}/v1"[/bold yellow]\n'
                f'   [bold yellow]CTXINS_TARGET="http://localhost:<TARGET_PORT>"[/bold yellow]'
            )
            yield Static(recipe2, classes="recipe-box")

            recipe3 = (
                f"[bold cyan]3. Python / Node SDK Script[/bold cyan]:\n"
                f'   [bold yellow]$ HTTP_PROXY="http://127.0.0.1:{self.proxy_port}" python agent.py[/bold yellow]'
            )
            yield Static(recipe3, classes="recipe-box")

            with Container(classes="button-bar"):
                yield Button("Copy Env [c]", id="btn-copy", variant="primary")
                yield Button("Close [Esc]", id="btn-close", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-copy":
            self.action_copy_env()
        elif event.button.id == "btn-close":
            self.dismiss()

    def action_copy_env(self) -> None:
        from src.cli import get_env_exports

        exports = get_env_exports(proxy_port=self.proxy_port)
        export_str = " ".join(f'{k}="{v}"' for k, v in exports.items())
        copy_to_clipboard(export_str)
        self.notify("Copied proxy environment to clipboard!")
