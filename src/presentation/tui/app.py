"""Root Textual application for interactive terminal context inspection."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

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
from src.presentation.tui.widgets.recommendations import RecommendationsWidget
from src.presentation.tui.widgets.turn_timeline import TurnSelected, TurnTimelineWidget

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
    ]

    selected_turn_index: reactive[int] = reactive(0)

    def __init__(
        self,
        state: Optional[TUIState] = None,
        broadcaster: Optional[PresentationBroadcaster] = None,
    ) -> None:
        super().__init__()
        self.state = state if state is not None else TUIState()
        self.broadcaster = broadcaster if broadcaster is not None else PresentationBroadcaster()

    def compose(self) -> ComposeResult:
        yield HeaderBarWidget(self.state)
        with Horizontal(id="main-container"):
            yield TurnTimelineWidget(self.state, id="timeline-pane")
            yield ContextBreakdownWidget(self.state, id="breakdown-pane")
            yield RecommendationsWidget(self.state, id="recommendations-pane")
        yield FooterBarWidget(self.state)

    async def on_mount(self) -> None:
        """Start listening for real-time events on mount."""
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
            self.query_one(TurnTimelineWidget).update_from_state()
        except Exception:
            pass
        self._refresh_inspectors()
        self.refresh()
