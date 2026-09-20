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

        groups = self.state.get_grouped_violations(selected_only=not is_all)
        if not groups:
            msg = Text()
            if not self.state.turns and not self.state.cumulative_violations:
                msg.append("HEURISTIC RECOMMENDATIONS & WASTE ANALYSIS\n\n", style="bold #58a6ff")
                msg.append("ctxins continuously evaluates context hygiene rules:\n\n", style="dim")
                msg.append("  • CTX-001: Stale Tool Output persistence\n", style="yellow")
                msg.append("  • CTX-002: Tool Schema bloat & unused definitions\n", style="yellow")
                msg.append(
                    "  • CACHE-001: Prompt cache prefix shifts & invalidations\n\n", style="yellow"
                )
                msg.append(
                    "Actionable recommendations, waste calculations ($), and\nremediation suggestions will appear here once turns execute.\n",
                    style="dim",
                )
            else:
                msg.append("\n✓ No rule violations detected.\n", style=f"bold {COLOR_SUCCESS}")
                msg.append("Context composition is clean and optimal.", style="dim")
            content_widget.update(msg)
            return

        out = Text()
        total_waste = sum(g["totalWasteUSD"] for g in groups)
        total_violations_count = sum(g["totalOccurrences"] for g in groups)
        out.append(
            f"Unique Warnings: {len(groups)} | Total Violations: {total_violations_count} | Potential Savings: ${total_waste:.4f}\n\n",
            style="bold #8b949e",
        )

        for i, g in enumerate(groups):
            rule_id = g["ruleId"]
            severity = g["severity"]
            title = g["title"]
            fix = g["suggestedFix"]
            count = g["totalOccurrences"]
            turn_count = g["turnCount"]
            current_turn = g["currentTurnIndex"]
            cur_viol = g["currentTurnViolation"]
            earlier_viols = g["earlierViolations"]
            earlier_turns = g["earlierTurnIndices"]
            total_waste = g["totalWasteUSD"]
            block_ids = g["blockIds"]

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
            occ_text = f"{count} violation{'s' if count != 1 else ''}"
            if turn_count > 1:
                occ_text += f" across {turn_count} turns"
            out.append(f"{rule_id}: {title} ({occ_text})\n", style="bold white")

            # Current turn breakdown
            if cur_viol:
                cur_waste = float(cur_viol.get("estimatedWasteUSD", 0.0) or 0.0)
                cur_msg = cur_viol.get("message", "")
                waste_info = f" (${cur_waste:.4f} waste)" if cur_waste > 0 else ""
                out.append(f"  • Current Turn (#{current_turn}): ", style="bold cyan")
                out.append(f"Active{waste_info}\n", style="bold #e3b341")
                if cur_msg:
                    out.append(f"    {cur_msg}\n", style="dim")
            else:
                out.append(f"  • Current Turn (#{current_turn}): ", style="bold cyan")
                out.append("Clean / Not triggered\n", style="dim green")

            # Earlier turns breakdown
            if earlier_viols:
                earlier_waste = g["earlierWasteUSD"]
                earlier_waste_info = f" (${earlier_waste:.4f} waste)" if earlier_waste > 0 else ""
                turns_str = ", ".join(f"#{t}" for t in earlier_turns)
                out.append(f"  • Earlier Turns ({turns_str}): ", style="bold cyan")
                out.append(
                    f"{len(earlier_viols)} violation{'s' if len(earlier_viols) != 1 else ''}{earlier_waste_info}\n",
                    style="yellow",
                )
            else:
                out.append("  • Earlier Turns: ", style="bold cyan")
                out.append("None (first occurrence)\n", style="dim")

            if total_waste > 0:
                out.append(
                    f"  Total Waste Impact: ${total_waste:.4f}\n", style=f"bold {COLOR_CRITICAL}"
                )

            if block_ids:
                b_str = ", ".join(block_ids[:5])
                if len(block_ids) > 5:
                    b_str += f" (+{len(block_ids) - 5} more)"
                out.append(f"  Referenced Blocks: {b_str}\n", style="dim cyan")

            out.append(f"  Suggested Fix: {fix}\n", style=f"bold {COLOR_SUCCESS}")

            if i < len(groups) - 1:
                out.append(f"  {'─' * 36}\n", style=COLOR_BORDER)

        content_widget.update(out)
