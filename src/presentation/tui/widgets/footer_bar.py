"""Footer bar widget displaying navigation keybindings and mode status."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.widget import Widget

from src.presentation.tui.state import TUIState
from src.presentation.tui.theme import COLOR_ACCENT, COLOR_MUTED


class FooterBarWidget(Widget):
    """Footer bar displaying interactive keybindings and active view status."""

    DEFAULT_CSS = """
    FooterBarWidget {
        height: 1;
        dock: bottom;
        background: #161b22;
        color: #8b949e;
        padding: 0 1;
    }
    """

    def __init__(self, state: TUIState, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.state = state

    def update_from_state(self) -> None:
        """Trigger re-render with updated state."""
        self.refresh()

    def render(self) -> Text:
        filter_mode = "ALL VIOLATIONS" if self.state.show_all_violations else "TURN VIOLATIONS"

        text = Text()
        text.append("[Tab] ", style=f"bold {COLOR_ACCENT}")
        text.append("Switch Pane  │  ", style=COLOR_MUTED)

        text.append("[↑/↓/j/k] ", style=f"bold {COLOR_ACCENT}")
        text.append("Select Turn  │  ", style=COLOR_MUTED)

        text.append("[n/p] ", style=f"bold {COLOR_ACCENT}")
        text.append("Select Block  │  ", style=COLOR_MUTED)

        text.append("[r] ", style=f"bold {COLOR_ACCENT}")
        text.append(f"Filter ({filter_mode})  │  ", style="bold #d29922" if self.state.show_all_violations else COLOR_MUTED)

        text.append("[e] ", style=f"bold {COLOR_ACCENT}")
        text.append("Export .jsonc  │  ", style=COLOR_MUTED)

        text.append("[q] ", style="bold #f85149")
        text.append("Quit", style=COLOR_MUTED)

        return text
