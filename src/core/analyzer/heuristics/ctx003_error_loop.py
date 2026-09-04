"""Rule CTX-003: Agent Error Loop Detection."""

from __future__ import annotations

import difflib
from typing import List, Optional

from src.core.analyzer.cost.pricing_table import get_pricing
from src.core.analyzer.heuristics.base import BaseHeuristic
from src.core.graph.turn_tree import ContextGraph
from src.schema.ast import CanonicalTurn, ContextBlock, RuleViolation, ViolationSeverity


class ErrorLoopHeuristic(BaseHeuristic):
    """Detects consecutive repeated tool execution errors across turns."""

    rule_id = "CTX-003"
    name = "Agent Error Loop Detected"
    description = (
        "Detects tool execution errors occurring across consecutive turns without "
        "resolution or with repeated error messages."
    )

    def __init__(
        self,
        consecutive_errors: int = 3,
        similarity_threshold: float = 0.5,
    ) -> None:
        self.consecutive_errors = consecutive_errors
        self.similarity_threshold = similarity_threshold

    def _is_error_result(self, block: ContextBlock) -> bool:
        """Check if a tool result represents an error."""
        meta = block.metadata
        if meta.get("is_error") is True or meta.get("is_error") == "true":
            return True
        # Check content keywords
        content_lower = block.content.lower()
        if "error" in meta.get("status", "").lower():
            return True
        if '"is_error": true' in content_lower or "'is_error': true" in content_lower:
            return True
        return False

    def analyze(
        self,
        turn: CanonicalTurn,
        graph: Optional[ContextGraph] = None,
        previous_turns: Optional[List[CanonicalTurn]] = None,
    ) -> List[RuleViolation]:
        violations: List[RuleViolation] = []
        resolved_prev = self.resolve_previous_turns(turn, graph, previous_turns)

        if len(resolved_prev) < self.consecutive_errors - 1:
            return violations

        all_recent = resolved_prev[-(self.consecutive_errors - 1) :] + [turn]

        # Extract error blocks from each of the recent turns
        recent_error_blocks: List[List[ContextBlock]] = []
        for t in all_recent:
            errors_in_turn = [r for r in t.tool_results if self._is_error_result(r)]
            if not errors_in_turn:
                return violations
            recent_error_blocks.append(errors_in_turn)

        # All consecutive turns have tool errors
        error_count = len(recent_error_blocks)
        if error_count < self.consecutive_errors:
            return violations

        # Check error similarity if messages are present
        error_texts = [
            " ".join(b.content for b in err_list) for err_list in recent_error_blocks
        ]
        is_repetitive = True
        if len(error_texts) >= 2:
            # Check pairwise similarity between consecutive errors
            similarities = [
                difflib.SequenceMatcher(None, error_texts[i], error_texts[i + 1]).ratio()
                for i in range(len(error_texts) - 1)
            ]
            # If errors are substantial, check similarity; if very short/empty, flag on error status
            if any(len(txt) > 20 for txt in error_texts):
                is_repetitive = any(s >= self.similarity_threshold for s in similarities) or all(
                    s >= 0.3 for s in similarities
                )

        if is_repetitive:
            pricing = get_pricing(turn.model, turn.provider)
            # Collect error block IDs from current turn
            curr_error_block_ids = [b.block_id for b in recent_error_blocks[-1]]
            total_error_tokens = sum(
                b.token_count for err_list in recent_error_blocks for b in err_list
            )
            waste_usd = max(
                0.015,
                round((total_error_tokens / 1000.0) * pricing.input_cost_per_1k, 6),
            )

            violations.append(
                RuleViolation(
                    rule_id=self.rule_id,
                    severity=ViolationSeverity.CRITICAL,
                    title=self.name,
                    message=(
                        f"Tool execution errors occurred across {self.consecutive_errors} "
                        f"consecutive turns without resolution."
                    ),
                    estimated_waste_usd=waste_usd,
                    suggested_fix="Inject steering prompt or terminate agent execution loop.",
                    block_ids=curr_error_block_ids,
                )
            )

        return violations
