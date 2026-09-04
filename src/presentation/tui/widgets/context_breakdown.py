"""Context breakdown widget with proportional ASCII bar and block inspector."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Static

from src.presentation.tui.state import TUIState
from src.presentation.tui.theme import CATEGORY_COLORS, COLOR_BORDER


class ContextBreakdownWidget(Widget):
    """Visualizes token composition and inspects AST context blocks for the selected turn."""

    can_focus = True

    DEFAULT_CSS = """
    ContextBreakdownWidget {
        layout: vertical;
        height: 100%;
        background: #0d1117;
    }
    #breakdown-scroll {
        height: 1fr;
        padding: 0 1;
    }
    """

    BINDINGS = [
        ("n", "next_block", "Next Block"),
        ("p", "prev_block", "Prev Block"),
    ]

    def __init__(self, state: TUIState, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.state = state

    def compose(self) -> ComposeResult:
        yield Static("[2] CONTEXT COMPOSITION", id="breakdown-title", classes="pane-title")
        with VerticalScroll(id="breakdown-scroll"):
            yield Static(id="breakdown-content")

    def on_mount(self) -> None:
        self.update_from_state()

    def update_from_state(self) -> None:
        """Update context composition visualization and block inspector."""
        try:
            content_widget = self.query_one("#breakdown-content", Static)
            title_widget = self.query_one("#breakdown-title", Static)
        except Exception:
            return

        turn = self.state.get_selected_turn()
        if not turn:
            title_widget.update("[2] CONTEXT COMPOSITION")
            content_widget.update(Text.from_markup("[dim]No turn selected or no data available.[/dim]"))
            return

        turn_idx = turn.get("turnIndex", self.state.selected_turn_index)
        title_widget.update(f"[2] CONTEXT COMPOSITION (TURN #{turn_idx})")

        tb = self.state.get_context_breakdown_for_selected_turn()
        sys_tok = tb.get("system", 0)
        tools_tok = tb.get("tools", 0)
        hist_tok = tb.get("history", 0)
        res_tok = tb.get("toolResults", 0)
        asst_tok = tb.get("assistant", 0)
        cache_tok = tb.get("cache", turn.get("cachedReadTokens", 0))

        # Effective context total
        base_tokens = sys_tok + tools_tok + hist_tok + res_tok + asst_tok
        denom = base_tokens if base_tokens > 0 else (turn.get("tokens", 0) or 1)

        p_sys = (sys_tok / denom) * 100.0
        p_tools = (tools_tok / denom) * 100.0
        p_hist = (hist_tok / denom) * 100.0
        p_res = (res_tok / denom) * 100.0
        p_cache = (cache_tok / denom) * 100.0 if denom > 0 else 0.0

        # Build proportional ASCII bar
        out = Text()
        out.append("PROPORTIONAL COMPOSITION:\n", style="bold #8b949e")
        out.append(f"[System: {p_sys:.1f}%] ", style=f"bold {CATEGORY_COLORS['system']}")
        out.append(f"[Tools: {p_tools:.1f}%] ", style=f"bold {CATEGORY_COLORS['tools']}")
        out.append(f"[History: {p_hist:.1f}%] ", style=f"bold {CATEGORY_COLORS['history']}")
        out.append(f"[Results: {p_res:.1f}%] ", style=f"bold {CATEGORY_COLORS['toolResults']}")
        out.append(f"[Cache: {p_cache:.1f}%]\n\n", style=f"bold {CATEGORY_COLORS['cache']}")

        # Category lines with mini horizontal bars
        def make_bar(toks: int, max_toks: int, width: int = 12) -> str:
            if max_toks <= 0:
                return ""
            filled = int(round((toks / max_toks) * width))
            return "■" * filled

        max_cat = max(sys_tok, tools_tok, hist_tok, res_tok, asst_tok, cache_tok, 1)

        categories = [
            ("System Prompt", sys_tok, p_sys, CATEGORY_COLORS["system"]),
            ("Tool Schemas", tools_tok, p_tools, CATEGORY_COLORS["tools"]),
            ("Conversation", hist_tok, p_hist, CATEGORY_COLORS["history"]),
            ("Tool Results", res_tok, p_res, CATEGORY_COLORS["toolResults"]),
            ("Output/Assistant", asst_tok, (asst_tok / denom) * 100.0, CATEGORY_COLORS["assistant"]),
            ("Cache Read", cache_tok, p_cache, CATEGORY_COLORS["cache"]),
        ]

        for name, toks, pct, col in categories:
            bar = make_bar(toks, max_cat)
            out.append(f"{name:<16}: ", style="dim white")
            out.append(f"{toks:>7,d} tok ", style="bold white")
            out.append(f"[{pct:>5.1f}%] ", style=f"bold {col}")
            out.append(f"{bar}\n", style=col)

        out.append(f"\n{'─' * 42}\n", style=COLOR_BORDER)

        # Blocks inspection
        blocks = self.state.get_blocks_for_selected_turn()
        if not blocks:
            out.append("BLOCK INSPECTOR:\n", style="bold #8b949e")
            out.append("No granular AST context blocks attached for this turn.\n", style="dim")
        else:
            total_blocks = len(blocks)
            # Clamp selected block index
            if self.state.selected_block_index >= total_blocks:
                self.state.selected_block_index = total_blocks - 1
            if self.state.selected_block_index < 0:
                self.state.selected_block_index = 0

            sel_idx = self.state.selected_block_index
            out.append(f"CONTEXT BLOCKS ({total_blocks} total) [n: next, p: prev]:\n", style="bold #8b949e")

            # Show block items
            for i, blk in enumerate(blocks[:8]):  # Show up to 8 blocks summary
                marker = "▶ " if i == sel_idx else "  "
                bid = blk.get("block_id", blk.get("blockId", f"blk_{i}"))
                btype = blk.get("block_type", blk.get("blockType", "unknown"))
                btoks = blk.get("token_count", blk.get("tokenCount", 0))
                surv = blk.get("turns_survived", blk.get("turnsSurvived", 1))
                bcol = CATEGORY_COLORS.get(btype, "white")

                item_style = "bold white" if i == sel_idx else "dim white"
                out.append(f"{marker}", style="bold #58a6ff" if i == sel_idx else "dim")
                out.append(f"[{bid}] ", style=item_style)
                out.append(f"{btype:<12} ", style=bcol)
                out.append(f"{btoks:>6,d} tok  ", style="white")
                out.append(f"(survived: {surv})\n", style="dim")

            if total_blocks > 8:
                out.append(f"  ... {total_blocks - 8} more blocks ...\n", style="dim")

            # Selected block detail card
            sel_block = blocks[sel_idx]
            s_id = sel_block.get("block_id", sel_block.get("blockId", f"blk_{sel_idx}"))
            s_type = sel_block.get("block_type", sel_block.get("blockType", "unknown"))
            s_toks = sel_block.get("token_count", sel_block.get("tokenCount", 0))
            s_hash = sel_block.get("content_hash", sel_block.get("contentHash", "n/a"))[:12]
            s_surv = sel_block.get("turns_survived", sel_block.get("turnsSurvived", 1))
            s_content = sel_block.get("content", "")

            out.append(f"\nSELECTED BLOCK: {s_type} (id: {s_id})\n", style="bold #58a6ff")
            out.append(f"Size: {s_toks:,d} tok | Survived: {s_surv} turns | Hash: {s_hash}\n", style="dim")
            if s_content:
                preview = s_content.strip()[:180].replace("\n", " ")
                out.append(f"Preview: {preview}...\n", style="italic #c9d1d9")

        content_widget.update(out)

    def action_next_block(self) -> None:
        blocks = self.state.get_blocks_for_selected_turn()
        if blocks:
            self.state.selected_block_index = (self.state.selected_block_index + 1) % len(blocks)
            self.update_from_state()

    def action_prev_block(self) -> None:
        blocks = self.state.get_blocks_for_selected_turn()
        if blocks:
            self.state.selected_block_index = (self.state.selected_block_index - 1) % len(blocks)
            self.update_from_state()
