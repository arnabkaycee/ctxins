"""Serialization utilities for turns, category token breakdowns, and lifecycle status."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from src.core.graph.diff import TurnDiffEngine
from src.schema.ast import BlockType, CanonicalTurn, TurnDelta


def calculate_category_breakdown(turn: CanonicalTurn) -> Dict[str, int]:
    """Calculate fine-grained token counts across context categories for a turn.

    Categories:
    - system: System instructions and root prompts
    - tools: Tool schema definitions
    - skills: Skill blocks and tool capability descriptions
    - history: User/assistant conversational turns and injected context
    - tool_results: Execution results and outputs from tools
    - thoughts: Model reasoning, chain-of-thought, or thinking tokens
    """
    system_tokens = 0
    tools_tokens = 0
    skills_tokens = 0
    history_tokens = 0
    tool_results_tokens = 0
    thoughts_tokens = 0

    seen_ids: Set[str] = set()
    for block in turn.all_blocks:
        if block.block_id in seen_ids:
            continue
        seen_ids.add(block.block_id)
        tokens = block.token_count
        b_type = block.block_type
        meta = block.metadata or {}

        # 1. Skills
        if (
            b_type == BlockType.SKILL
            or meta.get("type") == "skill"
            or meta.get("category") == "skill"
        ):
            skills_tokens += tokens
        # 2. Thoughts / Reasoning
        elif (
            b_type == BlockType.THOUGHT
            or meta.get("type") in ("thinking", "thought", "reasoning")
            or meta.get("thought") is True
        ):
            thoughts_tokens += tokens
        # 3. System
        elif b_type == BlockType.SYSTEM or block in turn.system_blocks:
            system_tokens += tokens
        # 4. Tool definitions
        elif b_type == BlockType.TOOL_DEF or block in turn.tool_defs:
            tools_tokens += tokens
        # 5. Tool results
        elif b_type == BlockType.TOOL_RESULT or block in turn.tool_results:
            tool_results_tokens += tokens
        # 6. Conversation history / Assistant
        else:
            history_tokens += tokens

    return {
        "system": system_tokens,
        "tools": tools_tokens,
        "skills": skills_tokens,
        "history": history_tokens,
        "conversation": history_tokens,
        "tool_results": tool_results_tokens,
        "results": tool_results_tokens,
        "thoughts": thoughts_tokens,
    }


def calculate_cache_retention(
    turn: CanonicalTurn,
    prev_turn: Optional[CanonicalTurn],
    delta: TurnDelta,
) -> float:
    """Calculate cache retention percentage for a turn."""
    if turn.input_tokens > 0 and turn.cached_read_tokens > 0:
        return round((turn.cached_read_tokens / turn.input_tokens) * 100.0, 2)
    if prev_turn is not None and turn.all_blocks:
        persisted_tokens = sum(
            b.token_count for b in turn.all_blocks if b.block_id in delta.persisted_block_ids
        )
        total_tokens = sum(b.token_count for b in turn.all_blocks)
        if total_tokens > 0:
            return round((persisted_tokens / total_tokens) * 100.0, 2)
    return 0.0


def serialize_turn_with_delta(
    turn: CanonicalTurn,
    prev_turn: Optional[CanonicalTurn],
) -> Dict[str, Any]:
    """Serialize a CanonicalTurn including TurnDelta and category breakdowns."""
    delta = TurnDiffEngine.compute_delta(prev_turn, turn)
    category_breakdown = calculate_category_breakdown(turn)
    cache_retention_pct = calculate_cache_retention(turn, prev_turn, delta)

    turn_dict = turn.to_dict()
    turn_dict["turn_delta"] = delta.to_dict()
    turn_dict["delta"] = delta.to_dict()
    turn_dict["added_block_ids"] = delta.added_block_ids
    turn_dict["persisted_block_ids"] = delta.persisted_block_ids
    turn_dict["mutated_block_ids"] = delta.mutated_block_ids
    turn_dict["removed_block_ids"] = delta.removed_block_ids
    turn_dict["cache_breakpoint_block_id"] = delta.cache_breakpoint_block_id
    turn_dict["token_growth"] = delta.token_growth
    turn_dict["category_breakdown"] = category_breakdown
    turn_dict["token_breakdown"] = category_breakdown
    turn_dict["cache_retention_percentage"] = cache_retention_pct
    turn_dict["cacheRetentionPercentage"] = cache_retention_pct
    return turn_dict


def annotate_blocks_lifecycle(
    turn: CanonicalTurn,
    prev_turn: Optional[CanonicalTurn],
) -> List[Dict[str, Any]]:
    """Return all blocks for a turn annotated with their lifecycle status.

    Lifecycle status:
    - 'added': Newly introduced context block in this turn
    - 'persisted': Block retained identically from previous turn
    - 'mutated': Block with same identity key but mutated content/hash
    - 'evicted': Block present in previous turn but removed in this turn
    """
    delta = TurnDiffEngine.compute_delta(prev_turn, turn)

    added_set = set(delta.added_block_ids)
    mutated_set = set(delta.mutated_block_ids)
    persisted_set = set(delta.persisted_block_ids)

    annotated_blocks: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()

    for b in turn.all_blocks:
        if b.block_id in seen_ids:
            continue
        seen_ids.add(b.block_id)
        d = b.to_dict()
        if b.block_id in added_set:
            status = "added"
        elif b.block_id in mutated_set:
            status = "mutated"
        elif b.block_id in persisted_set:
            status = "persisted"
        else:
            status = "added" if prev_turn is None else "persisted"

        d["status"] = status
        d["lifecycle_status"] = status
        annotated_blocks.append(d)

    if prev_turn is not None:
        removed_set = set(delta.removed_block_ids)
        for b in prev_turn.all_blocks:
            if b.block_id in removed_set and b.block_id not in seen_ids:
                seen_ids.add(b.block_id)
                d = b.to_dict()
                d["status"] = "evicted"
                d["lifecycle_status"] = "evicted"
                annotated_blocks.append(d)

    return annotated_blocks
