"""Chronological turn timeline widget with live streaming indicators."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.message import Message
from textual.widget import Widget
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from src.presentation.tui.state import TUIState


class TurnSelected(Message):
    """Event emitted when a turn is selected or highlighted."""

    def __init__(self, turn_index: int) -> None:
        super().__init__()
        self.turn_index = turn_index


class SessionSelected(Message):
    """Event emitted when a detected agent session is selected from timeline."""

    def __init__(self, session_id: str) -> None:
        super().__init__()
        self.session_id = session_id


class TurnTimelineWidget(Widget):
    """Displays scrollable list of turns with live streaming and violation badges."""

    can_focus = True

    DEFAULT_CSS = """
    TurnTimelineWidget {
        layout: vertical;
        height: 100%;
        background: #0d1117;
    }
    #turns-option-list {
        height: 1fr;
        background: #0d1117;
        border: none;
    }
    """

    BINDINGS = [
        ("j", "cursor_down", "Next Turn"),
        ("k", "cursor_up", "Prev Turn"),
    ]

    def __init__(self, state: TUIState, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.state = state

    def compose(self) -> ComposeResult:
        yield Static("[1] TURNS & TIMELINE", classes="pane-title")
        yield OptionList(id="turns-option-list")

    def on_mount(self) -> None:
        self.update_from_state()

    def update_from_state(self) -> None:
        """Refresh timeline list options from state."""
        try:
            ol = self.query_one("#turns-option-list", OptionList)
        except Exception:
            return

        ol.clear_options()
        if not self.state.turns:
            if self.state.available_sessions:
                ol.add_option(
                    Option(
                        Text.from_markup(f"[bold cyan]── DETECTED AGENTS ({len(self.state.available_sessions)}) ──[/]"),
                        disabled=True,
                    )
                )
                for sid in self.state.available_sessions:
                    meta = self.state.sessions_metadata.get(sid, {})
                    harness = meta.get("agentHarness", meta.get("harness", "agent"))
                    agent_dict = meta.get("agent") or {}
                    pid = agent_dict.get("pid") or meta.get("pid")
                    pid_str = f"PID:{pid} " if pid else ""
                    is_active = (sid == self.state.session_id)
                    if is_active:
                        markup = f"[bold green]▶ ACTIVE:[/] [bold white]{sid}[/] [cyan]\\[{harness}\\][/] [dim]{pid_str}[/]"
                    else:
                        markup = f"  [dim]○ SWITCH:[/] [white]{sid}[/] [dim]\\[{harness}\\] {pid_str}[/]"
                    ol.add_option(Option(Text.from_markup(markup), id=f"sess:{sid}"))
                ol.add_option(
                    Option(
                        Text.from_markup("[dim italic](Press Enter to select agent │ Proxy listening on :8080)[/]"),
                        disabled=True,
                    )
                )
            else:
                ol.add_option(Option(Text.from_markup("[dim](No turns yet; scanning for agents...)[/]"), disabled=True))
            return

        for turn in self.state.turns:
            idx = int(turn.get("turnIndex", 0))
            tokens = int(turn.get("tokens", 0))
            tok_str = f"{tokens / 1000:.1f}k" if tokens >= 1000 else str(tokens)
            status = str(turn.get("status", "idle"))
            violations = list(turn.get("violations", []))
            dur_sec = float(turn.get("durationMs", 0.0)) / 1000.0
            cost = float(turn.get("cost", 0.0))

            if status == "streaming":
                markup = f"[bold cyan]●[/] Turn #{idx} [dim]\\[streaming {dur_sec:.1f}s\\][/] {tok_str} tok"
            elif len(violations) > 0:
                markup = f"[bold yellow]⚠[/] Turn #{idx} ({tok_str} tok, {len(violations)} viols)"
            else:
                markup = f"[bold green]✓[/] Turn #{idx} ({tok_str} tok, ${cost:.3f})"

            ol.add_option(Option(Text.from_markup(markup), id=str(idx)))

        # Preserve or set highlighted turn
        target_idx = self.state.selected_turn_index
        if 0 <= target_idx < ol.option_count:
            ol.highlighted = target_idx

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option and not event.option.disabled and event.option_index is not None:
            if event.option.id and event.option.id.startswith("sess:"):
                return
            self.state.selected_turn_index = event.option_index
            self.post_message(TurnSelected(event.option_index))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option and not event.option.disabled:
            if event.option.id and event.option.id.startswith("sess:"):
                sid = event.option.id[len("sess:"):]
                self.state.switch_session(sid)
                self.post_message(SessionSelected(sid))
                self.update_from_state()
                return
            if event.option_index is not None:
                self.state.selected_turn_index = event.option_index
                self.post_message(TurnSelected(event.option_index))

    def action_cursor_down(self) -> None:
        try:
            self.query_one("#turns-option-list", OptionList).action_cursor_down()
        except Exception:
            pass

    def action_cursor_up(self) -> None:
        try:
            self.query_one("#turns-option-list", OptionList).action_cursor_up()
        except Exception:
            pass
