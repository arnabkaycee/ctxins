"""Rule CTX-007: Cache-Busting Prefix Mutation Heuristic Rule."""

from __future__ import annotations

from typing import List, Optional, Set

from src.core.analyzer.cost.pricing_table import get_pricing
from src.core.analyzer.heuristics.base import BaseHeuristic
from src.core.graph.diff import TurnDiffEngine
from src.core.graph.turn_tree import ContextGraph
from src.schema.ast import BlockType, CanonicalTurn, RuleViolation, TurnDelta, ViolationSeverity

STATIC_BLOCK_TYPES: Set[BlockType] = {
    BlockType.SYSTEM,
    BlockType.TOOL_DEF,
    BlockType.SKILL,
}


class CacheBustingPrefixRule(BaseHeuristic):
    """Detects mutations or insertions in static blocks or early context prefix that bust prompt caching."""

    rule_id = "CTX-007"
    name = "Prefix Cache Mutation"
    description = (
        "Flags static block mutations (system instructions, tool definitions, skills) or early "
        "prefix insertions that invalidate downstream prompt cache reuse."
    )

    def __init__(
        self,
        prefix_ratio_threshold: float = 0.30,
        severity: Optional[ViolationSeverity] = None,
        suggested_fix: str = (
            "Keep system instructions and skills immutable across turns; move dynamic state to tail"
        ),
    ) -> None:
        self.prefix_ratio_threshold = prefix_ratio_threshold
        self._custom_severity = severity
        self.suggested_fix = suggested_fix

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

        prev_turn = resolved_prev[-1]

        # Retrieve delta from graph or compute via diff engine
        delta: Optional[TurnDelta] = None
        if graph is not None:
            delta = graph.get_delta(turn.turn_index)
        if delta is None:
            delta = TurnDiffEngine.compute_delta(prev_turn, turn)
        if delta is None:
            return violations

        curr_blocks = turn.all_blocks
        if not curr_blocks:
            return violations

        total_blocks = len(curr_blocks)
        block_by_id = {b.block_id: b for b in curr_blocks}

        # Identify mutated static blocks
        mutated_static_blocks = [
            block_by_id[bid]
            for bid in delta.mutated_block_ids
            if bid in block_by_id and block_by_id[bid].block_type in STATIC_BLOCK_TYPES
        ]

        # Check breakpoint block position and type
        bp_block = (
            block_by_id.get(delta.cache_breakpoint_block_id)
            if delta.cache_breakpoint_block_id
            else None
        )
        bp_index = curr_blocks.index(bp_block) if bp_block is not None else 0

        is_early_by_count = (bp_index / max(1, total_blocks)) <= self.prefix_ratio_threshold
        tokens_before = sum(b.token_count for b in curr_blocks[:bp_index])
        total_tokens = sum(b.token_count for b in curr_blocks) or turn.input_tokens or 1
        is_early_by_tokens = (tokens_before / total_tokens) <= self.prefix_ratio_threshold
        is_in_early_prefix = is_early_by_count or is_early_by_tokens

        prev_static = [b for b in prev_turn.all_blocks if b.block_type in STATIC_BLOCK_TYPES]
        curr_static = [b for b in curr_blocks if b.block_type in STATIC_BLOCK_TYPES]
        static_changed = [b.content_hash for b in prev_static] != [
            b.content_hash for b in curr_static
        ]

        # Determine if cache busting occurred
        triggered = False
        if mutated_static_blocks:
            triggered = True
        elif bp_block is not None and bp_block.block_type in STATIC_BLOCK_TYPES:
            triggered = True
        elif is_in_early_prefix and (
            (bp_block is not None and bp_block.block_type in STATIC_BLOCK_TYPES)
            or static_changed
            or (
                bp_block is not None
                and any(b.block_type in STATIC_BLOCK_TYPES for b in curr_blocks[bp_index:])
            )
        ):
            triggered = True

        if not triggered:
            return violations

        # Determine culprit block IDs
        culprit_ids: List[str] = []
        if bp_block is not None:
            culprit_ids.append(bp_block.block_id)
        for b in mutated_static_blocks:
            if b.block_id not in culprit_ids:
                culprit_ids.append(b.block_id)
        if not culprit_ids and curr_blocks:
            culprit_ids.append(curr_blocks[0].block_id)

        # Determine severity
        if self._custom_severity is not None:
            severity = self._custom_severity
        elif bp_block is not None and bp_block.block_type in (BlockType.SYSTEM, BlockType.SKILL):
            severity = ViolationSeverity.CRITICAL
        elif any(
            b.block_type in (BlockType.SYSTEM, BlockType.SKILL) for b in mutated_static_blocks
        ):
            severity = ViolationSeverity.CRITICAL
        else:
            severity = ViolationSeverity.WARN

        # Financial modeling for prompt cache loss
        pricing = get_pricing(turn.model, turn.provider)
        loss_per_1k = max(0.0, pricing.input_cost_per_1k - pricing.cache_read_cost_per_1k)
        if loss_per_1k <= 0.0:
            loss_per_1k = pricing.input_cost_per_1k * 0.9

        downstream_tokens = sum(
            b.token_count for b in curr_blocks[bp_index:] if b.block_type != BlockType.ASSISTANT_MSG
        )

        total_input_block_tokens = (
            sum(b.token_count for b in curr_blocks if b.block_type != BlockType.ASSISTANT_MSG) or 1
        )

        if turn.input_tokens > 0:
            scale = min(1.0, max(0.0, downstream_tokens / total_input_block_tokens))
            wasted_tokens = max(1, int(turn.input_tokens * scale))
        else:
            wasted_tokens = max(1, downstream_tokens)

        waste_usd = round((wasted_tokens / 1000.0) * loss_per_1k, 6)
        if waste_usd <= 0.0 and wasted_tokens > 0:
            waste_usd = round((wasted_tokens / 1000.0) * pricing.input_cost_per_1k * 0.9, 6)

        block_type_label = bp_block.block_type.value if bp_block is not None else "static_block"
        violations.append(
            RuleViolation(
                rule_id=self.rule_id,
                severity=severity,
                title=self.name,
                message=(
                    f"Prefix cache invalidated near start of context ({block_type_label} mutated/inserted), "
                    f"busting prompt cache reuse for {wasted_tokens} downstream tokens."
                ),
                estimated_waste_usd=waste_usd,
                suggested_fix=self.suggested_fix,
                block_ids=culprit_ids,
            )
        )

        return violations
