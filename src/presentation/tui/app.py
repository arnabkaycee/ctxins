"""Root Textual application for interactive terminal context inspection."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Optional

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.reactive import reactive

from src.presentation.broadcaster import PresentationBroadcaster
from src.presentation.events import UIEvent
from src.presentation.tui.state import TUIState
from src.presentation.tui.theme import TUI_THEME_CSS
from src.presentation.tui.widgets.context_breakdown import ContextBreakdownWidget
from src.presentation.tui.widgets.footer_bar import FooterBarWidget
from src.presentation.tui.widgets.header_bar import HeaderBarWidget
from src.presentation.tui.widgets.help_modal import HelpModalScreen
from src.presentation.tui.widgets.hook_modal import HookModalScreen, copy_to_clipboard
from src.presentation.tui.widgets.recommendations import RecommendationsWidget
from src.presentation.tui.widgets.session_modal import SessionModalScreen
from src.presentation.tui.widgets.sessions_panel import SessionChosen, SessionsPanelWidget
from src.presentation.tui.widgets.turn_timeline import (
    SessionSelected,
    TurnSelected,
    TurnTimelineWidget,
)

if TYPE_CHECKING:
    from src.core.store.session_store import SessionStore

logger = logging.getLogger(__name__)


class CtxinsTUIApp(App[None]):
    """Primary Textual application for interactive terminal context inspection."""

    CSS = TUI_THEME_CSS

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("tab", "focus_next", "Next Pane"),
        ("shift+tab", "focus_previous", "Prev Pane"),
        ("r", "toggle_rule_filter", "Filter Violations"),
        ("e", "export_jsonc", "Export .jsonc"),
        ("s", "switch_session", "Switch Session"),
        ("a", "show_session_modal", "Agent Picker"),
        ("h", "show_hook_modal", "Hook Guide"),
        ("c", "copy_env", "Copy Env"),
        ("u", "copy_unset_env", "Unset Env"),
        ("w", "open_web", "Open Web"),
        ("?", "show_help_modal", "Help"),
        ("f1", "show_help_modal", "Help"),
    ]

    selected_turn_index: reactive[int] = reactive(0)

    def __init__(
        self,
        state: Optional[TUIState] = None,
        broadcaster: Optional[PresentationBroadcaster] = None,
        store: Optional[SessionStore] = None,
        proxy_port: int = 8080,
        web_url: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.state = state if state is not None else TUIState()
        self.broadcaster = broadcaster if broadcaster is not None else PresentationBroadcaster()
        self.store = store
        self.proxy_port = proxy_port
        self.web_url = web_url
        if self.store is not None:
            self._sync_store_sessions()

    def _sync_store_sessions(self) -> None:
        """Sync known sessions and metadata from SessionStore into state."""
        if self.store is None:
            return
        for sid in self.store.list_sessions():
            meta = self.store.get_session_metadata(sid) or {}
            if sid not in self.state.available_sessions:
                self.state.available_sessions.append(sid)
            self.state.sessions_metadata[sid] = dict(meta)
            store_turns = self.store.get_session(sid) or []
            if store_turns and sid not in self.state.sessions_turns:
                self.state._load_turns_from_store(self.store, sid)
            if not self.state.session_id or self.state.session_id == "sess_default":
                self.state.session_id = sid
                self.state.agent_harness = meta.get(
                    "agentHarness", meta.get("harness", self.state.agent_harness)
                )
                self.state.model = meta.get("model", self.state.model)
                self.state.provider = meta.get("provider", self.state.provider)
                self.state.status = meta.get("status", "Idle")

        if self.state.session_id and self.store is not None:
            self.state._load_turns_from_store(self.store, self.state.session_id)
            self.selected_turn_index = self.state.selected_turn_index

    def compose(self) -> ComposeResult:
        yield HeaderBarWidget(self.state, proxy_port=self.proxy_port, web_url=self.web_url)
        with Horizontal(id="main-container"):
            yield SessionsPanelWidget(self.state, id="sessions-pane")
            yield TurnTimelineWidget(self.state, id="timeline-pane")
            yield ContextBreakdownWidget(self.state, id="breakdown-pane")
            yield RecommendationsWidget(self.state, id="recommendations-pane")
        yield FooterBarWidget(self.state)

    async def on_mount(self) -> None:
        """Start listening for real-time events on mount."""
        self._sync_store_sessions()
        self._refresh_all_widgets()
        self.run_worker(self._listen_events(), exclusive=False, name="tui_event_listener")

    async def _listen_events(self) -> None:
        """Consume live presentation events from PresentationBroadcaster."""
        queue = await self.broadcaster.subscribe()
        try:
            while True:
                event = await queue.get()
                self._handle_ui_event(event)
                queue.task_done()
        except asyncio.CancelledError:
            pass
        finally:
            await self.broadcaster.unsubscribe(queue)

    def _handle_ui_event(self, event: UIEvent) -> None:
        """Process incoming event, updating state and refreshing widgets."""
        if self.store is not None:
            for sid in self.store.list_sessions():
                if sid not in self.state.available_sessions:
                    self.state.available_sessions.append(sid)
                if sid not in self.state.sessions_metadata:
                    meta = self.store.get_session_metadata(sid) or {}
                    self.state.sessions_metadata[sid] = dict(meta)
        self.state.apply_event(event)
        self._refresh_all_widgets()

    def watch_selected_turn_index(self, old_val: int, new_val: int) -> None:
        """Propagate selected turn changes to state and inspectors."""
        self.state.selected_turn_index = new_val
        self._refresh_inspectors()

    def on_turn_selected(self, message: TurnSelected) -> None:
        """Handle turn selection emitted from timeline."""
        self.selected_turn_index = message.turn_index

    def action_toggle_rule_filter(self) -> None:
        """Toggle showing violations for all turns vs selected turn."""
        self.state.show_all_violations = not self.state.show_all_violations
        self._refresh_inspectors()
        label = "all session turns" if self.state.show_all_violations else f"turn #{self.selected_turn_index}"
        self.notify(f"Showing violations for: {label}")

    def action_export_jsonc(self) -> None:
        """Export session timeline adhering to canonical .jsonc schema."""
        out_path = self.state.export_to_jsonc()
        self.notify(f"Exported session report: {out_path.name}")

    def action_show_hook_modal(self) -> None:
        """Display interactive agent connection modal."""
        self.push_screen(HookModalScreen(proxy_port=self.proxy_port))

    def action_copy_env(self) -> None:
        """Copy proxy environment export snippet to system clipboard."""
        from src.cli import get_env_exports

        exports = get_env_exports(proxy_port=self.proxy_port)
        export_str = "export " + " ".join(f'{k}="{v}"' for k, v in exports.items())
        copy_to_clipboard(export_str)
        self.notify("Copied proxy environment exports to clipboard!")

    def action_copy_unset_env(self) -> None:
        """Copy proxy environment unset snippet to system clipboard."""
        from src.cli import get_unset_env_exports

        var_names = get_unset_env_exports()
        unset_str = f"unset {' '.join(var_names)}"
        copy_to_clipboard(unset_str)
        self.notify("Copied unset proxy environment command to clipboard!")

    def action_open_web(self) -> None:
        """Open web dashboard in default browser."""
        if self.web_url:
            import webbrowser

            webbrowser.open(self.web_url)
            self.notify(f"Opened {self.web_url} in browser")
        else:
            self.notify("Web Dashboard is disabled (--no-web active)", severity="warning")

    def action_show_help_modal(self) -> None:
        """Display interactive help and keyboard shortcuts modal."""
        self.push_screen(HelpModalScreen())

    def on_session_selected(self, message: SessionSelected) -> None:
        """Handle session selection emitted from timeline."""
        self.state.switch_session(message.session_id, store=self.store)
        self.selected_turn_index = self.state.selected_turn_index
        self._refresh_all_widgets()
        self.notify(f"Active Session: {message.session_id} ({self.state.agent_harness})")

    def on_session_chosen(self, message: SessionChosen) -> None:
        """Handle session selection from the dedicated sessions panel."""
        self.state.switch_session(message.session_id, store=self.store)
        self.selected_turn_index = self.state.selected_turn_index
        self._refresh_all_widgets()
        self.notify(f"Active Session: {message.session_id} ({self.state.agent_harness})")

    def action_show_session_modal(self) -> None:
        """Display interactive detected agent sessions modal."""
        def _on_modal_dismiss(chosen_sid: Optional[str]) -> None:
            if chosen_sid:
                self.state.switch_session(chosen_sid, store=self.store)
                self.selected_turn_index = self.state.selected_turn_index
                self._refresh_all_widgets()
                self.notify(f"Active Session: {chosen_sid} ({self.state.agent_harness})")

        self.push_screen(SessionModalScreen(self.state), callback=_on_modal_dismiss)

    def action_switch_session(self) -> None:
        """Cycle to next active or auto-detected agent session."""
        next_sid = self.state.switch_session(store=self.store)
        if next_sid:
            self.selected_turn_index = self.state.selected_turn_index
            harness = self.state.agent_harness
            self._refresh_all_widgets()
            self.notify(f"Active Session: {next_sid} ({harness})")
        else:
            self.notify("No other detected sessions available")

    def _refresh_inspectors(self) -> None:
        """Refresh context breakdown, recommendations, and footer widgets."""
        try:
            self.query_one(ContextBreakdownWidget).update_from_state()
        except Exception:
            pass
        try:
            self.query_one(RecommendationsWidget).update_from_state()
        except Exception:
            pass
        try:
            self.query_one(FooterBarWidget).update_from_state()
        except Exception:
            pass

    def _refresh_all_widgets(self) -> None:
        """Refresh all top-level widgets upon state mutations."""
        try:
            self.query_one(HeaderBarWidget).update_from_state()
        except Exception:
            pass
        try:
            self.query_one(SessionsPanelWidget).update_from_state()
        except Exception:
            pass
        try:
            self.query_one(TurnTimelineWidget).update_from_state()
        except Exception:
            pass
        self._refresh_inspectors()
        self.refresh()
