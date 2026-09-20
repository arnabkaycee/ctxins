"""Interactive modal dialog displaying auto-detected agent sessions and allowing switching."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, OptionList
from textual.widgets.option_list import Option

from src.presentation.tui.state import TUIState


class SessionModalScreen(ModalScreen[str]):
    """Modal dialog displaying all running agent processes and allowing interactive selection."""

    DEFAULT_CSS = """
    SessionModalScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.7);
    }

    #session-modal-container {
        width: 86;
        height: 28;
        border: solid #58a6ff;
        background: #161b22;
        padding: 1 2;
    }

    #session-modal-title {
        text-align: center;
        text-style: bold;
        color: #58a6ff;
        margin-bottom: 1;
    }

    #session-option-list {
        height: 18;
        background: #0d1117;
        border: solid #30363d;
        margin: 1 0;
    }

    #session-btn-box {
        align: center middle;
        height: 3;
    }

    #btn-close-session {
        background: #21262d;
        color: white;
    }
    """

    BINDINGS = [
        ("escape", "dismiss_modal", "Close"),
        ("q", "dismiss_modal", "Close"),
    ]

    def __init__(self, state: TUIState, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.state = state

    def compose(self) -> ComposeResult:
        with Container(id="session-modal-container"):
            yield Label("[AUTO-DETECTED AGENT SESSIONS]", id="session-modal-title")
            with Vertical():
                yield OptionList(id="session-option-list")
                with Container(id="session-btn-box"):
                    yield Button("Close (Esc)", id="btn-close-session")

    def on_mount(self) -> None:
        ol = self.query_one("#session-option-list", OptionList)
        ol.clear_options()

        if not self.state.available_sessions:
            ol.add_option(
                Option(
                    Text.from_markup("[dim]No agent processes currently detected.[/dim]"),
                    disabled=True,
                )
            )
            return

        for sid in self.state.available_sessions:
            meta = self.state.sessions_metadata.get(sid, {})
            harness = meta.get("agentHarness", meta.get("harness", "unknown"))
            agent_dict = meta.get("agent") or {}
            pid = agent_dict.get("pid") or meta.get("pid", "N/A")
            cmd = agent_dict.get("command") or meta.get("command", "")
            if len(cmd) > 35:
                cmd = cmd[:32] + "..."
            status = meta.get("status", "detected")
            is_active = sid == self.state.session_id

            badge = "[bold green]● ACTIVE[/]" if is_active else "[dim]○ SWITCH[/]"
            markup = (
                f"{badge} [bold white]{sid}[/] "
                f"[bold cyan]\\[{harness}\\][/] "
                f"[yellow]PID:{pid}[/] "
                f"[dim]{cmd}[/] "
                f"[dim italic]({status})[/]"
            )
            ol.add_option(Option(Text.from_markup(markup), id=sid))

        if self.state.session_id in self.state.available_sessions:
            ol.highlighted = self.state.available_sessions.index(self.state.session_id)
        elif ol.option_count > 0:
            ol.highlighted = 0
        ol.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option and not event.option.disabled and event.option.id:
            selected_sid = str(event.option.id)
            self.state.switch_session(selected_sid)
            self.dismiss(selected_sid)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-close-session":
            self.dismiss(None)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)
