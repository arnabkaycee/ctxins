"""Footer bar widget displaying navigation keybindings and mode status."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.widget import Widget

from src.presentation.tui.state import TUIState
from src.presentation.tui.theme import COLOR_ACCENT, COLOR_MUTED, COLOR_SUCCESS


class FooterBarWidget(Widget):
    """Footer bar displaying interactive keybindings and active view status."""

    DEFAULT_CSS = """
    FooterBarWidget {
        height: auto;
        min-height: 3;
        dock: bottom;
        background: #161b22;
        border-top: solid #30363d;
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
        filter_mode = "All" if self.state.show_all_violations else "Turn"

        text = Text()
        # Row 1: Timeline and Content Navigation
        text.append("[Tab] ", style=f"bold {COLOR_ACCENT}")
        text.append("Switch Pane │ ", style=COLOR_MUTED)
        text.append("[↑/↓] ", style=f"bold {COLOR_ACCENT}")
        text.append("Turn │ ", style=COLOR_MUTED)
        text.append("[n/p] ", style=f"bold {COLOR_ACCENT}")
        text.append("Block │ ", style=COLOR_MUTED)
        text.append("[r] ", style=f"bold {COLOR_ACCENT}")
        text.append(
            f"Filter({filter_mode}) │ ",
            style="bold #d29922" if self.state.show_all_violations else COLOR_MUTED,
        )
        text.append("[e] ", style=f"bold {COLOR_ACCENT}")
        text.append("Export\n", style=COLOR_MUTED)

        # Row 2: Integration Actions, Help, and Exit
        text.append("[s] ", style=f"bold {COLOR_ACCENT}")
        text.append("Switch │ ", style=COLOR_MUTED)
        text.append("[a] ", style=f"bold {COLOR_ACCENT}")
        text.append("Agents │ ", style=COLOR_MUTED)
        text.append("[h] ", style=f"bold {COLOR_ACCENT}")
        text.append("Hook Guide │ ", style=COLOR_MUTED)
        text.append("[c] ", style=f"bold {COLOR_SUCCESS}")
        text.append("Copy Env │ ", style=COLOR_MUTED)
        text.append("[u] ", style="bold #e3b341")
        text.append("Unset Env │ ", style=COLOR_MUTED)
        text.append("[w] ", style=f"bold {COLOR_ACCENT}")
        text.append("Open Web │ ", style=COLOR_MUTED)
        text.append("[?] ", style="bold #e3b341")
        text.append("Help │ ", style=COLOR_MUTED)
        text.append("[q] ", style="bold #f85149")
        text.append("Quit", style=COLOR_MUTED)

        return text
