"""Recommendations and heuristic violation alerts widget."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Static

from src.presentation.tui.state import TUIState
from src.presentation.tui.theme import (
    COLOR_BORDER,
    COLOR_CRITICAL,
    COLOR_INFO,
    COLOR_SUCCESS,
    COLOR_WARN,
)


class RecommendationsWidget(Widget):
    """Displays real-time rule violation cards, estimated waste, and suggested remediation."""

    can_focus = True

    DEFAULT_CSS = """
    RecommendationsWidget {
        layout: vertical;
        height: 100%;
        background: #0d1117;
    }
    #recommendations-scroll {
        height: 1fr;
        padding: 0 1;
    }
    """

    def __init__(self, state: TUIState, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.state = state

    def compose(self) -> ComposeResult:
        yield Static("[3] RECOMMENDATIONS", id="recommendations-title", classes="pane-title")
        with VerticalScroll(id="recommendations-scroll"):
            yield Static(id="recommendations-content")

    def on_mount(self) -> None:
        self.update_from_state()

    def update_from_state(self) -> None:
        """Update violations list based on active filter and selected turn."""
        try:
            content_widget = self.query_one("#recommendations-content", Static)
            title_widget = self.query_one("#recommendations-title", Static)
        except Exception:
            return

        is_all = self.state.show_all_violations
        mode_label = "All Session [r]" if is_all else f"Turn #{self.state.selected_turn_index} [r]"
        title_widget.update(f"[3] RECOMMENDATIONS ({mode_label})")

        violations = self.state.get_violations_for_selected_turn()
        if not violations:
            msg = Text()
            msg.append("\n✓ No rule violations detected.\n", style=f"bold {COLOR_SUCCESS}")
            msg.append("Context composition is clean and optimal.", style="dim")
            content_widget.update(msg)
            return

        out = Text()
        total_waste = sum(float(v.get("estimatedWasteUSD", 0.0)) for v in violations)
        out.append(
            f"Active Violations: {len(violations)} | Potential Savings: ${total_waste:.4f}\n\n",
            style="bold #8b949e",
        )

        for i, v in enumerate(violations):
            rule_id = v.get("ruleId", "CTX-000")
            severity = str(v.get("severity", "WARN")).upper()
            title = v.get("title", rule_id)
            waste = float(v.get("estimatedWasteUSD", 0.0))
            fix = v.get("suggestedFix", "No suggestion provided.")
            block_ids = v.get("blockIds", [])
            turn_idx = v.get("turnIndex", 0)

            # Badge styling
            if severity == "CRITICAL":
                badge_style = f"bold {COLOR_CRITICAL}"
                badge_text = "[CRITICAL]"
            elif severity == "INFO":
                badge_style = f"bold {COLOR_INFO}"
                badge_text = "[INFO]"
            else:
                badge_style = f"bold {COLOR_WARN}"
                badge_text = "[WARN]"

            out.append(f"{badge_text} ", style=badge_style)
            out.append(f"{rule_id}: {title} (Turn #{turn_idx})\n", style="bold white")

            if waste > 0:
                out.append(f"  Waste Impact: ${waste:.4f}\n", style=f"bold {COLOR_CRITICAL}")

            if block_ids:
                b_str = ", ".join(block_ids)
                out.append(f"  Referenced Blocks: {b_str}\n", style="dim cyan")

            out.append(f"  Suggested Fix: {fix}\n", style=f"bold {COLOR_SUCCESS}")

            if i < len(violations) - 1:
                out.append(f"  {'─' * 36}\n", style=COLOR_BORDER)

        content_widget.update(out)
