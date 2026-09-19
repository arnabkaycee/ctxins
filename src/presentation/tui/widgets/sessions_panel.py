"""Dedicated TUI panel displaying auto-detected agent sessions and processes."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.message import Message
from textual.widget import Widget
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from src.presentation.tui.state import TUIState
from src.presentation.tui.theme import COLOR_MUTED, COLOR_SUCCESS


class SessionChosen(Message):
    """Event emitted when a user selects a session from the sessions panel."""

    def __init__(self, session_id: str) -> None:
        super().__init__()
        self.session_id = session_id


class SessionsPanelWidget(Widget):
    """Interactive left panel listing all auto-detected agent sessions, processes, and types."""

    can_focus = True

    DEFAULT_CSS = """
    SessionsPanelWidget {
        layout: vertical;
        height: 100%;
        background: #0d1117;
    }
    #sessions-option-list {
        height: 1fr;
        background: #0d1117;
        border: none;
    }
    """

    BINDINGS = [
        ("j", "cursor_down", "Next Session"),
        ("k", "cursor_up", "Prev Session"),
    ]

    HARNESS_COLORS = {
        "agy": "bold cyan",
        "claude-code": "bold magenta",
        "claude": "bold magenta",
        "opencode": "bold blue",
        "aider": "bold green",
        "pi": "bold yellow",
        "custom": "bold white",
        "unknown": "dim",
    }

    HARNESS_TITLES = {
        "agy": "Antigravity",
        "claude-code": "Claude Code",
        "claude": "Claude Code",
        "opencode": "OpenCode",
        "aider": "Aider",
        "pi": "Pi Agent",
    }

    def __init__(self, state: TUIState, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.state = state

    def compose(self) -> ComposeResult:
        yield Static("[0] SESSIONS & AGENTS", classes="pane-title")
        yield OptionList(id="sessions-option-list")

    def on_mount(self) -> None:
        self.update_from_state()

    def update_from_state(self) -> None:
        """Refresh sessions list options from state."""
        try:
            ol = self.query_one("#sessions-option-list", OptionList)
        except Exception:
            return

        ol.clear_options()
        sessions = self.state.available_sessions
        if not sessions:
            ol.add_option(
                Option(Text.from_markup("[dim italic](Waiting for agent traffic on :8080...)[/]"), disabled=True)
            )
            ol.add_option(
                Option(Text.from_markup("[dim]Run: [bold #58a6ff]ctxins run -- <agent>[/][/]"), disabled=True)
            )
            ol.add_option(
                Option(Text.from_markup("[dim]Or:  [bold #58a6ff]eval $(ctxins env)[/][/]"), disabled=True)
            )
            return

        for idx, sid in enumerate(sessions):
            meta = self.state.sessions_metadata.get(sid, {})
            harness = meta.get("agentHarness", meta.get("harness", "unknown")).lower()
            agent_dict = meta.get("agent") or {}
            pid = agent_dict.get("pid") or meta.get("pid")
            pid_str = f"PID:{pid}" if pid else "PID:N/A"
            cmd = agent_dict.get("command") or meta.get("command", "")
            if len(cmd) > 28:
                cmd = cmd[:25] + "..."

            display_name = agent_dict.get("display_name") or self.HARNESS_TITLES.get(harness, harness.upper())
            color = self.HARNESS_COLORS.get(harness, "bold white")
            is_active = (sid == self.state.session_id)

            # Count turns for this session
            sess_turns = self.state.sessions_turns.get(sid, [])
            if not sess_turns and is_active:
                sess_turns = self.state.turns
            turns_cnt = len(sess_turns)
            turns_str = f"{turns_cnt} turn{'s' if turns_cnt != 1 else ''}"

            # Format 2-line or compact rich option
            text = Text()
            if is_active:
                text.append("▶ ", style=f"bold {COLOR_SUCCESS}")
                text.append(f"{display_name} ", style="bold white")
                text.append(f"[{harness}]", style=color)
                text.append(" [ACTIVE]\n", style=f"bold {COLOR_SUCCESS}")
                text.append(f"   {sid} │ {pid_str} │ {turns_str}\n", style=COLOR_MUTED)
                if cmd:
                    text.append(f"   cmd: {cmd}", style="italic dim")
            else:
                text.append("○ ", style=COLOR_MUTED)
                text.append(f"{display_name} ", style="white")
                text.append(f"[{harness}]\n", style=color)
                text.append(f"   {sid} │ {pid_str} │ {turns_str}", style=COLOR_MUTED)

            ol.add_option(Option(text, id=sid))

        # Highlight active session in option list
        if self.state.session_id in sessions:
            active_idx = sessions.index(self.state.session_id)
            if 0 <= active_idx < ol.option_count:
                ol.highlighted = active_idx

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option and not event.option.disabled and event.option.id:
            sid = str(event.option.id)
            self.post_message(SessionChosen(sid))

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option and not event.option.disabled and event.option.id:
            sid = str(event.option.id)
            if sid != self.state.session_id:
                self.post_message(SessionChosen(sid))

    def action_cursor_down(self) -> None:
        try:
            self.query_one("#sessions-option-list", OptionList).action_cursor_down()
        except Exception:
            pass

    def action_cursor_up(self) -> None:
        try:
            self.query_one("#sessions-option-list", OptionList).action_cursor_up()
        except Exception:
            pass
