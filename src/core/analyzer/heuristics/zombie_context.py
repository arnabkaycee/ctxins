"""Rule CTX-006: Zombie Context Detection for Stale Tool Results."""

from __future__ import annotations

from typing import List, Optional

from src.core.analyzer.cost.pricing_table import get_pricing
from src.core.analyzer.heuristics.base import BaseHeuristic
from src.core.graph.turn_tree import ContextGraph
from src.schema.ast import BlockType, CanonicalTurn, RuleViolation, ViolationSeverity


class ZombieContextRule(BaseHeuristic):
    """Detects unreferenced tool results surviving >= 3 turns with high token overhead."""

    rule_id = "CTX-006"
    name = "Zombie Tool Result Detected"
    description = (
        "Flags tool result blocks lingering in context for >= 3 turns with > 300 tokens "
        "that are no longer referenced in recent turns."
    )

    def __init__(
        self,
        min_turns: int = 3,
        min_tokens: int = 300,
        recent_turns: int = 2,
        severity: ViolationSeverity = ViolationSeverity.WARN,
        suggested_fix: str = "Prune or summarize stale tool result from prompt",
    ) -> None:
        self.min_turns = min_turns
        self.min_tokens = min_tokens
        self.recent_turns = recent_turns
        self.severity = severity
        self.suggested_fix = suggested_fix

    def analyze(
        self,
        turn: CanonicalTurn,
        graph: Optional[ContextGraph] = None,
        previous_turns: Optional[List[CanonicalTurn]] = None,
    ) -> List[RuleViolation]:
        violations: List[RuleViolation] = []
        if not turn.tool_results:
            return violations

        resolved_prev = self.resolve_previous_turns(turn, graph, previous_turns)

        # Collect recent text from conversation history and assistant messages across recent turns
        recent_text_parts: List[str] = []
        for prev in resolved_prev[-self.recent_turns :]:
            recent_text_parts.extend(b.content for b in prev.assistant_blocks)
            recent_text_parts.extend(
                b.content
                for b in prev.conversation_history
                if b.block_type in (BlockType.USER_MSG, BlockType.ASSISTANT_MSG)
            )

        if turn.assistant_blocks:
            recent_text_parts.extend(b.content for b in turn.assistant_blocks)
        if turn.conversation_history:
            recent_text_parts.extend(
                b.content
                for b in turn.conversation_history
                if b.block_type in (BlockType.USER_MSG, BlockType.ASSISTANT_MSG)
            )

        recent_text = " ".join(recent_text_parts)
        pricing = get_pricing(turn.model, turn.provider)

        for tool_res in turn.tool_results:
            # Token count must be strictly greater than min_tokens (> 300)
            if tool_res.token_count <= self.min_tokens:
                continue

            # Determine turns survived: check block attribute or count consecutive presence in resolved_prev
            consecutive_prev_count = 0
            for prev_turn in reversed(resolved_prev):
                matched = any(
                    t.block_id == tool_res.block_id
                    or t.content_hash == tool_res.content_hash
                    or (bool(t.identity_key) and t.identity_key == tool_res.identity_key)
                    for t in prev_turn.tool_results
                )
                if matched:
                    consecutive_prev_count += 1
                else:
                    break

            turns_survived = max(tool_res.turns_survived, consecutive_prev_count)
            if turns_survived < self.min_turns:
                continue

            # Check if referenced in recent context
            tool_id = (
                tool_res.metadata.get("tool_use_id")
                or tool_res.metadata.get("tool_call_id")
                or tool_res.metadata.get("id")
                or ""
            )

            is_referenced = False
            if tool_id and tool_id in recent_text:
                is_referenced = True
            elif tool_res.block_id and tool_res.block_id in recent_text:
                is_referenced = True
            elif tool_res.identity_key and tool_res.identity_key in recent_text:
                is_referenced = True

            if is_referenced:
                continue

            # Calculate estimated waste USD based on token_count * turns_survived * model input price
            waste_usd = round(
                (tool_res.token_count / 1000.0) * turns_survived * pricing.input_cost_per_1k,
                6,
            )

            violations.append(
                RuleViolation(
                    rule_id=self.rule_id,
                    severity=self.severity,
                    title=self.name,
                    message=(
                        f"Tool result '{tool_res.block_id}' ({tool_res.token_count} tokens) has "
                        f"survived {turns_survived} turns without reference in recent context."
                    ),
                    estimated_waste_usd=waste_usd,
                    suggested_fix=self.suggested_fix,
                    block_ids=[tool_res.block_id],
                )
            )

        return violations
