"""Header ribbon widget for session metadata and high-level KPIs."""

from __future__ import annotations

from typing import Any

from rich.table import Table
from rich.text import Text
from textual.widget import Widget

from src.presentation.tui.state import TUIState
from src.presentation.tui.theme import (
    COLOR_ACCENT,
    COLOR_CRITICAL,
    COLOR_MUTED,
    COLOR_SUCCESS,
    get_pollution_color,
    render_pollution_bar,
)


class HeaderBarWidget(Widget):
    """3-row compact summary header ribbon."""

    DEFAULT_CSS = """
    HeaderBarWidget {
        height: 4;
        dock: top;
        background: #0d1117;
        border-bottom: solid #30363d;
        padding: 0 1;
    }
    """

    def __init__(self, state: TUIState, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.state = state

    def update_from_state(self) -> None:
        """Trigger re-render with latest state values."""
        self.refresh()

    def render(self) -> Table:
        """Render 3-row KPI ribbon."""
        table = Table.grid(expand=True)
        table.add_column(ratio=1)

        summary = self.state.get_summary()
        sess_id = summary["sessionId"] or "sess_default"
        harness = summary["agentHarness"]
        model = summary["model"] or "default-model"
        provider = summary["provider"] or "default-provider"
        status = summary["status"]

        # Row 1: Session, Model, Status
        row1 = Text()
        row1.append("ctxins v0.1.0", style=f"bold {COLOR_ACCENT}")
        row1.append(" │ Session: ", style=COLOR_MUTED)
        row1.append(f"{sess_id} ({harness})", style="bold white")
        row1.append(" │ Model: ", style=COLOR_MUTED)
        row1.append(f"{provider}/{model}", style="white")
        row1.append(" │ Status: ", style=COLOR_MUTED)

        if status.lower() == "streaming":
            curr_turn = self.state.selected_turn_index
            row1.append(f"● STREAMING (Turn #{curr_turn})", style="bold cyan")
        elif status.lower() == "ended":
            row1.append("● Ended", style=f"bold {COLOR_MUTED}")
        else:
            row1.append("● Idle", style=f"bold {COLOR_SUCCESS}")

        # Row 2: Aggregate Metrics
        tokens = summary["totalTokens"]
        tok_str = f"{tokens / 1000:.1f}k" if tokens >= 1000 else str(tokens)
        cache_hit = summary["cacheHitRatio"] * 100.0
        cached_tok = summary["cachedReadTokens"]
        cached_str = f"{cached_tok / 1000:.1f}k" if cached_tok >= 1000 else str(cached_tok)
        spend = summary["totalCostUSD"]
        wasted = summary["wastedCostUSD"]

        row2 = Text()
        row2.append("TOTAL TOKENS: ", style=f"bold {COLOR_ACCENT}")
        row2.append(f"{tok_str} ", style="bold white")
        row2.append("│ CACHE HIT: ", style=COLOR_MUTED)
        row2.append(f"{cache_hit:.1f}% ({cached_str}) ", style=f"bold {COLOR_SUCCESS}")
        row2.append("│ SPEND: ", style=COLOR_MUTED)
        row2.append(f"${spend:.4f} ", style="bold white")
        row2.append("│ WASTED: ", style=COLOR_MUTED)
        wasted_color = COLOR_CRITICAL if wasted > 0 else COLOR_MUTED
        row2.append(f"${wasted:.4f}", style=f"bold {wasted_color}")

        # Row 3: Visual Pollution Meter
        score = summary["pollutionScore"]
        meter_str = render_pollution_bar(score, width=15)
        p_color = get_pollution_color(score)

        row3 = Text()
        row3.append("POLLUTION SCORE: ", style="bold #8b949e")
        row3.append(meter_str, style=f"bold {p_color}")

        table.add_row(row1)
        table.add_row(row2)
        table.add_row(row3)
        return table
