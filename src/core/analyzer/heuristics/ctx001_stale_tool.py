"""Rule CTX-001: Stale Tool Output Bloat Detection."""

from __future__ import annotations

from typing import List, Optional

from src.core.analyzer.cost.pricing_table import get_pricing
from src.core.analyzer.heuristics.base import BaseHeuristic
from src.core.graph.turn_tree import ContextGraph
from src.schema.ast import CanonicalTurn, RuleViolation, ViolationSeverity


class StaleToolHeuristic(BaseHeuristic):
    """Detects unreferenced tool output blocks lingering >= 3 turns without reference."""

    rule_id = "CTX-001"
    name = "Stale Tool Output Bloat"
    description = (
        "Detects tool outputs consuming significant tokens lingering in context "
        "for >= 3 turns without being referenced in assistant messages."
    )

    def __init__(self, min_tokens: int = 3000, min_turns: int = 3) -> None:
        self.min_tokens = min_tokens
        self.min_turns = min_turns

    def analyze(
        self,
        turn: CanonicalTurn,
        graph: Optional[ContextGraph] = None,
        previous_turns: Optional[List[CanonicalTurn]] = None,
    ) -> List[RuleViolation]:
        violations: List[RuleViolation] = []
        resolved_prev = self.resolve_previous_turns(turn, graph, previous_turns)

        if len(resolved_prev) < self.min_turns and all(
            b.turns_survived < self.min_turns for b in turn.tool_results
        ):
            return violations

        pricing = get_pricing(turn.model, turn.provider)

        # Concatenate text from recent assistant blocks to search for references
        recent_assistant_text = " ".join(
            blk.content for prev in resolved_prev[-2:] for blk in prev.assistant_blocks
        )
        if turn.assistant_blocks:
            recent_assistant_text += " " + " ".join(
                blk.content for blk in turn.assistant_blocks
            )

        for tool_res in turn.tool_results:
            if tool_res.token_count < self.min_tokens:
                continue

            # Check if block survived >= min_turns turns
            is_stale = False
            if tool_res.turns_survived >= self.min_turns:
                is_stale = True
            elif len(resolved_prev) >= self.min_turns:
                older_turn = resolved_prev[-self.min_turns]
                is_stale = any(
                    t.content_hash == tool_res.content_hash or t.block_id == tool_res.block_id
                    for t in older_turn.tool_results
                )

            if not is_stale:
                continue

            tool_id = tool_res.metadata.get("tool_use_id", "")
            # If tool_use_id is not found, check block_id
            referenced = False
            if tool_id and tool_id in recent_assistant_text:
                referenced = True
            elif tool_res.block_id and tool_res.block_id in recent_assistant_text:
                referenced = True

            if not referenced:
                waste_cost = round((tool_res.token_count / 1000.0) * pricing.input_cost_per_1k, 6)
                violations.append(
                    RuleViolation(
                        rule_id=self.rule_id,
                        severity=ViolationSeverity.WARN,
                        title=self.name,
                        message=(
                            f"Tool result ({tool_res.token_count} tokens) has remained in "
                            f"context for >= {self.min_turns} turns without reference."
                        ),
                        estimated_waste_usd=waste_cost,
                        suggested_fix="Prune older tool payloads or truncate large responses.",
                        block_ids=[tool_res.block_id],
                    )
                )

        return violations
