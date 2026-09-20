"""Context breakdown widget with proportional ASCII bar and Turn Diff Ledger."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Static

from src.presentation.tui.state import TUIState
from src.presentation.tui.theme import CATEGORY_COLORS, COLOR_BORDER, DIFF_COLORS


class ContextBreakdownWidget(Widget):
    """Visualizes token composition and inspects AST context blocks or Turn Diff Ledger."""

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
        ("d", "toggle_diff", "Toggle Diff"),
    ]

    def __init__(self, state: TUIState, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.state = state
        self.view_mode: str = "composition"  # "composition" or "diff"

    def compose(self) -> ComposeResult:
        yield Static("[2] CONTEXT COMPOSITION", id="breakdown-title", classes="pane-title")
        with VerticalScroll(id="breakdown-scroll"):
            yield Static(id="breakdown-content")

    def on_mount(self) -> None:
        self.update_from_state()

    def action_toggle_diff(self) -> None:
        """Toggle between Context Composition and Turn Diff Ledger view."""
        self.view_mode = "diff" if self.view_mode == "composition" else "composition"
        self.state.selected_block_index = 0
        self.update_from_state()

    def update_from_state(self) -> None:
        """Update context composition visualization or turn diff ledger."""
        try:
            content_widget = self.query_one("#breakdown-content", Static)
            title_widget = self.query_one("#breakdown-title", Static)
        except Exception:
            return

        turn = self.state.get_selected_turn()
        if not turn:
            if self.view_mode == "diff":
                title_widget.update("[2] TURN DIFF LEDGER")
                card = Text()
                card.append("AWAITING FIRST TURN TELEMETRY\n\n", style="bold #58a6ff")
                card.append(
                    "Turn Diff Ledger itemizes block additions, persistence, mutations, and evictions:\n\n",
                    style="dim",
                )
                card.append(
                    "  • [+] Added      : Fresh blocks introduced into prompt\n",
                    style="bold #3fb950",
                )
                card.append(
                    "  • [=] Persisted  : Stable cached context across turns\n", style="dim #58a6ff"
                )
                card.append(
                    "  • [~] Mutated    : In-place block mutations (invalidation)\n",
                    style="bold #d29922",
                )
                card.append(
                    "  • [-] Evicted    : Trimmed or removed blocks\n\n", style="bold #f85149"
                )
                card.append("Press [d] to toggle back to Composition view.\n", style="dim italic")
                content_widget.update(card)
            else:
                title_widget.update("[2] CONTEXT COMPOSITION")
                card = Text()
                card.append("AWAITING FIRST TURN TELEMETRY\n\n", style="bold #58a6ff")
                card.append(
                    "This pane displays granular context & prompt cache metrics:\n\n", style="dim"
                )
                card.append("  • Skills & Modular capabilities\n", style="green")
                card.append("  • Tool Schemas & Function definitions\n", style="magenta")
                card.append("  • System Prompt & Developer instructions\n", style="cyan")
                card.append("  • Conversation History & User messages\n", style="green")
                card.append("  • Tool Results & Execution outputs\n", style="yellow")
                card.append("  • Thoughts & Reasoning tokens\n", style="magenta")
                card.append("  • Injected State & Session data\n", style="cyan")
                card.append("  • Assistant Output tokens\n", style="blue")
                card.append("  • Prompt Cache Read & Creation tokens\n\n", style="bold green")
                card.append("Press [d] to toggle Turn Diff Ledger mode.\n\n", style="dim italic")
                card.append("──────────────────────────────────────────\n", style="dim")
                card.append("Traffic Routing to Proxy:\n", style="bold #58a6ff")
                card.append(" export HTTP_PROXY=http://127.0.0.1:8080\n", style="white")
                card.append(" export HTTPS_PROXY=http://127.0.0.1:8080\n", style="white")
                card.append("(Press [c] to copy export commands)\n", style="dim italic")
                content_widget.update(card)
            return

        turn_idx = turn.get("turnIndex", self.state.selected_turn_index)

        if self.view_mode == "diff":
            self._render_diff_view(title_widget, content_widget, turn_idx)
        else:
            self._render_composition_view(title_widget, content_widget, turn, turn_idx)

    def _render_composition_view(
        self,
        title_widget: Static,
        content_widget: Static,
        turn: dict[str, Any],
        turn_idx: int,
    ) -> None:
        """Render standard contextual composition breakdown with proportional ASCII bar."""
        title_widget.update(f"[2] CONTEXT COMPOSITION (TURN #{turn_idx})")

        tb = self.state.get_context_breakdown_for_selected_turn()
        skills_tok = tb.get("skills", 0)
        tools_tok = tb.get("tools", 0)
        sys_tok = tb.get("system", 0)
        hist_tok = tb.get("history", 0)
        res_tok = tb.get("toolResults", 0)
        thought_tok = tb.get("thought", tb.get("thoughts", 0))
        injected_tok = tb.get("injected_state", 0)
        asst_tok = tb.get("assistant", 0)
        cache_tok = tb.get("cache", turn.get("cachedReadTokens", 0))

        # Effective context total
        base_tokens = (
            skills_tok
            + tools_tok
            + sys_tok
            + hist_tok
            + res_tok
            + thought_tok
            + injected_tok
            + asst_tok
        )
        denom = base_tokens if base_tokens > 0 else (turn.get("tokens", 0) or 1)

        p_skills = (skills_tok / denom) * 100.0
        p_tools = (tools_tok / denom) * 100.0
        p_sys = (sys_tok / denom) * 100.0
        p_hist = (hist_tok / denom) * 100.0
        p_res = (res_tok / denom) * 100.0
        p_thought = (thought_tok / denom) * 100.0
        p_injected = (injected_tok / denom) * 100.0
        p_cache = (cache_tok / denom) * 100.0 if denom > 0 else 0.0

        # Build proportional ASCII bar
        out = Text()
        out.append("PROPORTIONAL COMPOSITION [Press 'd' for Diff Ledger]:\n", style="bold #8b949e")
        out.append(f"[Skills: {p_skills:.1f}%] ", style=f"bold {CATEGORY_COLORS['skills']}")
        out.append(f"[Tool Defs: {p_tools:.1f}%] ", style=f"bold {CATEGORY_COLORS['tools']}")
        out.append(f"[System: {p_sys:.1f}%] ", style=f"bold {CATEGORY_COLORS['system']}")
        out.append(f"[Conversation: {p_hist:.1f}%] ", style=f"bold {CATEGORY_COLORS['history']}")
        out.append(f"[Tool Results: {p_res:.1f}%] ", style=f"bold {CATEGORY_COLORS['toolResults']}")
        out.append(f"[Thoughts: {p_thought:.1f}%] ", style=f"bold {CATEGORY_COLORS['thought']}")
        if injected_tok > 0:
            out.append(
                f"[Injected: {p_injected:.1f}%] ", style=f"bold {CATEGORY_COLORS['injected_state']}"
            )
        out.append(f"[Cache: {p_cache:.1f}%]\n\n", style=f"bold {CATEGORY_COLORS['cache']}")

        # Category lines with mini horizontal bars
        def make_bar(toks: int, max_toks: int, width: int = 12) -> str:
            if max_toks <= 0:
                return ""
            filled = int(round((toks / max_toks) * width))
            return "■" * filled

        max_cat = max(
            skills_tok,
            tools_tok,
            sys_tok,
            hist_tok,
            res_tok,
            thought_tok,
            injected_tok,
            asst_tok,
            cache_tok,
            1,
        )

        categories = [
            ("Skills", skills_tok, p_skills, CATEGORY_COLORS["skills"]),
            ("Tool Definitions", tools_tok, p_tools, CATEGORY_COLORS["tools"]),
            ("System Prompt", sys_tok, p_sys, CATEGORY_COLORS["system"]),
            ("Conversation", hist_tok, p_hist, CATEGORY_COLORS["history"]),
            ("Tool Results", res_tok, p_res, CATEGORY_COLORS["toolResults"]),
            ("Thoughts", thought_tok, p_thought, CATEGORY_COLORS["thought"]),
            ("Injected State", injected_tok, p_injected, CATEGORY_COLORS["injected_state"]),
            (
                "Assistant Output",
                asst_tok,
                (asst_tok / denom) * 100.0,
                CATEGORY_COLORS["assistant"],
            ),
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
            if self.state.selected_block_index >= total_blocks:
                self.state.selected_block_index = total_blocks - 1
            if self.state.selected_block_index < 0:
                self.state.selected_block_index = 0

            sel_idx = self.state.selected_block_index
            out.append(
                f"CONTEXT BLOCKS ({total_blocks} total) [n: next, p: prev, d: diff view]:\n",
                style="bold #8b949e",
            )

            # Show block items
            for i, blk in enumerate(blocks[:8]):
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
            s_hash = str(sel_block.get("content_hash", sel_block.get("contentHash", "n/a")))[:12]
            s_surv = sel_block.get("turns_survived", sel_block.get("turnsSurvived", 1))
            s_content = sel_block.get("content", "")

            out.append(f"\nSELECTED BLOCK: {s_type} (id: {s_id})\n", style="bold #58a6ff")
            out.append(
                f"Size: {s_toks:,d} tok | Survived: {s_surv} turns | Hash: {s_hash}\n", style="dim"
            )
            if s_content:
                preview = s_content.strip()[:180].replace("\n", " ")
                out.append(f"Preview: {preview}...\n", style="italic #c9d1d9")

        content_widget.update(out)

    def _render_diff_view(
        self,
        title_widget: Static,
        content_widget: Static,
        turn_idx: int,
    ) -> None:
        """Render Turn Diff Ledger itemizing block additions, mutations, and evictions."""
        title_widget.update(f"[2] TURN DIFF LEDGER (TURN #{turn_idx})")

        delta = self.state.get_delta_for_selected_turn()
        transitions = delta.get("transitions", [])
        added_cnt = delta.get("added_count", len(delta.get("added_block_ids", [])))
        persisted_cnt = delta.get("persisted_count", len(delta.get("persisted_block_ids", [])))
        mutated_cnt = delta.get("mutated_count", len(delta.get("mutated_block_ids", [])))
        evicted_cnt = delta.get("evicted_count", len(delta.get("removed_block_ids", [])))
        cb_id = delta.get("cache_breakpoint_block_id")

        out = Text()
        out.append("DIFF SUMMARY [Press 'd' for Composition]:\n", style="bold #8b949e")
        out.append(f"[+{added_cnt} Added] ", style=f"bold {DIFF_COLORS['added']}")
        out.append(f"[={persisted_cnt} Persisted] ", style=f"bold {DIFF_COLORS['persisted']}")
        out.append(f"[~{mutated_cnt} Mutated] ", style=f"bold {DIFF_COLORS['mutated']}")
        out.append(f"[-{evicted_cnt} Evicted] ", style=f"bold {DIFF_COLORS['evicted']}")
        if cb_id:
            out.append(f"[Cache Breakpoint: {cb_id}]\n", style="bold #f85149")
        else:
            out.append("[Cache Breakpoint: None]\n", style="dim #3fb950")

        growth = delta.get("token_growth", 0)
        sign = "+" if growth > 0 else ""
        out.append(f"Net Token Delta: {sign}{growth:,d} tokens\n", style="dim white")
        out.append(f"\n{'─' * 42}\n", style=COLOR_BORDER)

        if not transitions:
            out.append("TURN DIFF LEDGER:\n", style="bold #8b949e")
            out.append("No context block transitions detected for this turn.\n", style="dim")
        else:
            total_items = len(transitions)
            if self.state.selected_block_index >= total_items:
                self.state.selected_block_index = total_items - 1
            if self.state.selected_block_index < 0:
                self.state.selected_block_index = 0

            sel_idx = self.state.selected_block_index
            out.append(
                f"BLOCK TRANSITIONS ({total_items} total) [n: next, p: prev, d: composition]:\n",
                style="bold #8b949e",
            )

            # Display window around selection
            display_limit = 10
            start_i = max(0, min(sel_idx - display_limit // 2, total_items - display_limit))
            end_i = min(total_items, start_i + display_limit)

            for i in range(start_i, end_i):
                tr = transitions[i]
                marker = "▶ " if i == sel_idx else "  "
                bid = tr.get("block_id", f"blk_{i}")
                btype = tr.get("block_type", "unknown")
                btoks = tr.get("token_count", 0)
                status = tr.get("status", "added")
                id_key = tr.get("identity_key", "")
                bcol = CATEGORY_COLORS.get(btype, "white")
                is_sel = i == sel_idx

                out.append(f"{marker}", style="bold #58a6ff" if is_sel else "dim")

                # Format status badge
                if status == "evicted":
                    out.append("[-] Evicted   ", style="bold strike #f85149")
                elif status == "mutated":
                    out.append("[~] Mutated   ", style="bold #d29922")
                elif status == "persisted":
                    out.append("[=] Persisted ", style="dim #58a6ff")
                else:
                    out.append("[+] Added     ", style="bold #3fb950")

                out.append(f"[{bid}] ", style="bold white" if is_sel else "dim white")
                out.append(f"{btype:<12} ", style=bcol)
                out.append(f"{btoks:>6,d} tok ", style="white")
                if id_key:
                    out.append(f"({id_key})", style="dim italic")
                out.append("\n")

            if total_items > display_limit:
                out.append(
                    f"  ... showing {start_i + 1}-{end_i} of {total_items} blocks (navigate with n/p) ...\n",
                    style="dim",
                )

            # Selected block detail card
            sel_tr = transitions[sel_idx]
            s_id = sel_tr.get("block_id", f"blk_{sel_idx}")
            s_type = sel_tr.get("block_type", "unknown")
            s_status = sel_tr.get("status", "added")
            s_status_tag = sel_tr.get("status_tag", "")
            s_toks = sel_tr.get("token_count", 0)
            s_key = sel_tr.get("identity_key", "") or "n/a"
            s_blk = sel_tr.get("block", {})
            s_hash = str(s_blk.get("content_hash", s_blk.get("contentHash", "n/a")))[:12]
            s_content = sel_tr.get("content", "") or s_blk.get("content", "")

            out.append(
                f"\nSELECTED BLOCK: {s_type} (id: {s_id}) [{s_status.upper()}]\n",
                style="bold #58a6ff",
            )
            out.append(
                f"Status: {s_status_tag} | Size: {s_toks:,d} tok | Key: {s_key} | Hash: {s_hash}\n",
                style="dim",
            )
            if s_content:
                preview = s_content.strip()[:180].replace("\n", " ")
                out.append(f"Preview: {preview}...\n", style="italic #c9d1d9")

        content_widget.update(out)

    def action_next_block(self) -> None:
        if self.view_mode == "diff":
            items = self.state.get_block_transitions_for_selected_turn()
        else:
            items = self.state.get_blocks_for_selected_turn()
        if items:
            self.state.selected_block_index = (self.state.selected_block_index + 1) % len(items)
            self.update_from_state()

    def action_prev_block(self) -> None:
        if self.view_mode == "diff":
            items = self.state.get_block_transitions_for_selected_turn()
        else:
            items = self.state.get_blocks_for_selected_turn()
        if items:
            self.state.selected_block_index = (self.state.selected_block_index - 1) % len(items)
            self.update_from_state()
