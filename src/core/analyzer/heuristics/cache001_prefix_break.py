"""Rule CACHE-001: Prompt Cache Dynamic Prefix Invalidation Detection."""

from __future__ import annotations

from typing import List, Optional

from src.core.analyzer.cost.pricing_table import get_pricing
from src.core.analyzer.heuristics.base import BaseHeuristic
from src.core.graph.turn_tree import ContextGraph
from src.schema.ast import CanonicalTurn, RuleViolation, ViolationSeverity


class PrefixBreakHeuristic(BaseHeuristic):
    """Detects system prompt or prefix mutations that invalidate prompt caching."""

    rule_id = "CACHE-001"
    name = "Prompt Cache Prefix Invalidation"
    description = (
        "Detects system prompt mutations across turns that invalidate prompt cache "
        "reuse for downstream context."
    )

    def __init__(self, max_token_drift: int = 100) -> None:
        self.max_token_drift = max_token_drift

    def analyze(
        self,
        turn: CanonicalTurn,
        graph: Optional[ContextGraph] = None,
        previous_turns: Optional[List[CanonicalTurn]] = None,
    ) -> List[RuleViolation]:
        violations: List[RuleViolation] = []
        resolved_prev = self.resolve_previous_turns(turn, graph, previous_turns)

        if not resolved_prev:
            return violations

        last_turn = resolved_prev[-1]

        # Compare system blocks between immediate previous turn and current turn
        if not last_turn.system_blocks or not turn.system_blocks:
            return violations

        last_sys = last_turn.system_blocks[0]
        curr_sys = turn.system_blocks[0]

        # Check if content hash changed
        if last_sys.content_hash != curr_sys.content_hash:
            token_diff = abs(last_sys.token_count - curr_sys.token_count)
            # Prefix mutation typically involves minor drift (timestamp, UUID, or modified preamble)
            # or any system modification in an ongoing session
            if token_diff <= self.max_token_drift or last_sys.content in curr_sys.content or curr_sys.content in last_sys.content:
                pricing = get_pricing(turn.model, turn.provider)
                effective_input = turn.input_tokens or sum(b.token_count for b in turn.all_blocks)
                # 90% discount missed on input tokens due to cache invalidation
                waste_usd = round((effective_input / 1000.0) * pricing.input_cost_per_1k * 0.9, 6)

                violations.append(
                    RuleViolation(
                        rule_id=self.rule_id,
                        severity=ViolationSeverity.CRITICAL,
                        title=self.name,
                        message=(
                            "System prompt prefix was modified, breaking prompt cache "
                            "reuse for all downstream tokens."
                        ),
                        estimated_waste_usd=waste_usd,
                        suggested_fix=(
                            "Move dynamic elements (timestamps, random IDs) to the end of "
                            "the prompt or into user messages to preserve cache prefixes."
                        ),
                        block_ids=[curr_sys.block_id],
                    )
                )

        return violations
