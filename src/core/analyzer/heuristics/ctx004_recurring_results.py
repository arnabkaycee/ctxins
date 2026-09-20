"""Rule CTX-004: Recurring Execution Results Exceeded Detection & Optimization."""

from __future__ import annotations

import difflib
from typing import List, Optional, Set

from src.core.analyzer.cost.pricing_table import get_pricing
from src.core.analyzer.heuristics.base import BaseHeuristic
from src.core.graph.turn_tree import ContextGraph
from src.schema.ast import CanonicalTurn, ContextBlock, RuleViolation, ViolationSeverity


class RecurringResultsHeuristic(BaseHeuristic):
    """Tracks if the amount of recurring tool execution results exceeds a given threshold.

    Suggests concrete optimization mechanisms:
    1. Shrinking context (compacting or pruning repetitive tool outputs).
    2. Starting a new session (clearing context when recurring outputs dominate window).
    3. Output caching or incremental diff transmission.
    """

    rule_id = "CTX-004"
    name = "Recurring Execution Results Exceeded"
    description = (
        "Tracks if the amount of recurring tool execution results exceeds a given threshold "
        "and suggests optimizations (shrink context, start a new session, or cache outputs)."
    )

    def __init__(
        self,
        token_threshold: int = 2000,
        occurrence_threshold: int = 2,
        ratio_threshold: float = 0.20,
        similarity_threshold: float = 0.90,
        min_block_tokens: int = 150,
    ) -> None:
        """Initialize RecurringResultsHeuristic.

        Args:
            token_threshold: Total token volume threshold for recurring results to trigger alert.
            occurrence_threshold: Minimum number of occurrences or turns survived to be deemed recurring.
            ratio_threshold: Minimum fraction of turn's input tokens consumed by recurring results.
            similarity_threshold: Normalized similarity ratio to consider two result payloads identical.
            min_block_tokens: Minimum tokens for an individual block to be evaluated for recurrence.
        """
        self.token_threshold = token_threshold
        self.occurrence_threshold = occurrence_threshold
        self.ratio_threshold = ratio_threshold
        self.similarity_threshold = similarity_threshold
        self.min_block_tokens = min_block_tokens

    def _is_matching_result(self, b1: ContextBlock, b2: ContextBlock) -> bool:
        """Check if two tool result blocks are identical or near-identical in content."""
        if b1.content_hash and b2.content_hash and b1.content_hash == b2.content_hash:
            return True
        if b1.identity_key and b2.identity_key and b1.identity_key == b2.identity_key:
            return True

        c1 = str(b1.content)
        c2 = str(b2.content)
        if c1 == c2:
            return True

        # If lengths differ significantly, quick reject
        l1, l2 = len(c1), len(c2)
        if max(l1, l2) > 0 and abs(l1 - l2) / max(l1, l2) > (1.0 - self.similarity_threshold):
            return False

        ratio = difflib.SequenceMatcher(None, c1[:2000], c2[:2000]).ratio()
        return ratio >= self.similarity_threshold

    def analyze(
        self,
        turn: CanonicalTurn,
        graph: Optional[ContextGraph] = None,
        previous_turns: Optional[List[CanonicalTurn]] = None,
    ) -> List[RuleViolation]:
        """Analyze turn for recurring tool execution results exceeding threshold."""
        violations: List[RuleViolation] = []
        if not turn.tool_results:
            return violations

        resolved_prev = self.resolve_previous_turns(turn, graph, previous_turns)

        recurring_blocks: List[ContextBlock] = []
        recurring_block_ids: List[str] = []
        recurring_tokens = 0
        max_occurrences = 1

        # Track results already accounted for in this turn to avoid double counting
        evaluated_block_ids: Set[str] = set()

        for tool_res in turn.tool_results:
            if tool_res.token_count < self.min_block_tokens:
                continue

            if tool_res.block_id in evaluated_block_ids:
                continue

            occurrences = 1
            # 1. Check previous turns
            for prev in resolved_prev:
                has_match = any(
                    self._is_matching_result(tool_res, prev_res) for prev_res in prev.tool_results
                )
                if has_match:
                    occurrences += 1

            # 2. Check turns_survived metadata
            if tool_res.turns_survived > 0:
                occurrences = max(occurrences, tool_res.turns_survived + 1)

            # 3. Check duplicate injections within the current turn itself
            duplicates_in_current = [
                other
                for other in turn.tool_results
                if other is not tool_res
                and other.block_id not in evaluated_block_ids
                and self._is_matching_result(tool_res, other)
            ]
            occurrences += len(duplicates_in_current)

            if occurrences >= self.occurrence_threshold:
                evaluated_block_ids.add(tool_res.block_id)
                recurring_blocks.append(tool_res)
                recurring_block_ids.append(tool_res.block_id)
                recurring_tokens += tool_res.token_count

                # Include any duplicate blocks present in the current turn
                for other in duplicates_in_current:
                    evaluated_block_ids.add(other.block_id)
                    recurring_blocks.append(other)
                    recurring_block_ids.append(other.block_id)
                    recurring_tokens += other.token_count

                if occurrences > max_occurrences:
                    max_occurrences = occurrences

        if not recurring_blocks:
            return violations

        input_toks = turn.input_tokens if turn.input_tokens > 0 else (turn.total_tokens or 1)
        recurring_ratio = recurring_tokens / input_toks

        # Trigger condition: Exceeds token threshold OR ratio threshold
        exceeds_threshold = (
            recurring_tokens >= self.token_threshold or recurring_ratio >= self.ratio_threshold
        )

        if not exceeds_threshold:
            return violations

        pricing = get_pricing(turn.model, turn.provider)
        waste_cost = round((recurring_tokens / 1000.0) * pricing.input_cost_per_1k, 6)

        is_critical = recurring_tokens >= 5000 or recurring_ratio >= 0.50
        severity = ViolationSeverity.CRITICAL if is_critical else ViolationSeverity.WARN

        pct_str = f"{round(recurring_ratio * 100, 1)}%"
        message = (
            f"Recurring tool results ({recurring_tokens:,} tokens across {max_occurrences} occurrences, "
            f"{pct_str} of context) exceeded threshold ({self.token_threshold:,} tokens)."
        )

        suggested_fix = (
            f"Recurring results exceed threshold ({recurring_tokens:,} tokens). "
            "Optimization: 1) Shrink context by compacting or pruning repetitive tool outputs (/compact); "
            "2) Start a fresh session (/clear) preserving only active progress and modified files; "
            "3) Cache repetitive queries or transmit incremental diffs instead of full payload dumps."
        )

        violations.append(
            RuleViolation(
                rule_id=self.rule_id,
                severity=severity,
                title=self.name,
                message=message,
                estimated_waste_usd=waste_cost,
                suggested_fix=suggested_fix,
                block_ids=recurring_block_ids,
                turn_index=turn.turn_index,
            )
        )

        return violations
