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
        prev_sys = last_turn.system_blocks
        curr_sys = turn.system_blocks

        # If neither turn had system blocks, nothing to break
        if not prev_sys and not curr_sys:
            return violations

        # Check if system prompt prefix changed across turns
        # 1. System prompt added on this turn when previous turn had none
        # 2. System prompt removed on this turn when previous turn had one
        # 3. Any system block content, sequence, or count changed
        prev_hashes = [b.content_hash for b in prev_sys]
        curr_hashes = [b.content_hash for b in curr_sys]

        if prev_hashes != curr_hashes:
            # Locate first mutated, added, or changed block in current turn
            culprit_block = None
            for idx, b in enumerate(curr_sys):
                if idx >= len(prev_sys) or b.content_hash != prev_sys[idx].content_hash:
                    culprit_block = b
                    break

            # Fallback to first curr_sys block or first prev_sys block if curr_sys was evicted
            if culprit_block is None:
                culprit_block = curr_sys[0] if curr_sys else prev_sys[0]

            block_ids = [culprit_block.block_id] if culprit_block else []

            pricing = get_pricing(turn.model, turn.provider)
            effective_input = turn.input_tokens or sum(b.token_count for b in turn.all_blocks)
            loss_per_1k = max(0.0, pricing.input_cost_per_1k - pricing.cache_read_cost_per_1k)
            if loss_per_1k <= 0.0:
                loss_per_1k = pricing.input_cost_per_1k * 0.9

            waste_usd = round((effective_input / 1000.0) * loss_per_1k, 6)

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
                    block_ids=block_ids,
                )
            )

        return violations
