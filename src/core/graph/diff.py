"""Turn difference analysis engine computing deltas between sequential turns."""

from __future__ import annotations

from typing import Optional

from src.schema.ast import CanonicalTurn, ContextBlock, TurnDelta


def _block_match_key(block: ContextBlock) -> tuple[str, str]:
    """Produce unique identity key for a context block.

    If block.identity_key is present, use (block.block_type.value, block.identity_key).
    Otherwise use (block.block_type.value, block.content_hash) or (block.block_type.value, block.block_id).
    """
    if block.identity_key:
        return (block.block_type.value, block.identity_key)
    if block.content_hash:
        return (block.block_type.value, block.content_hash)
    return (block.block_type.value, block.block_id)


class TurnDiffEngine:
    """Computes context block lineage transitions and token growth between turns."""

    @classmethod
    def compute_delta(
        cls,
        turn_prev: Optional[CanonicalTurn],
        turn_curr: CanonicalTurn,
    ) -> TurnDelta:
        """Compute TurnDelta between previous turn and current turn.

        Args:
            turn_prev: Preceding turn (or None if turn_curr is the initial turn).
            turn_curr: Current turn being evaluated.

        Returns:
            TurnDelta with added, removed, persisted, and mutated block IDs,
            cache breakpoint block ID, and token growth.
        """
        turn_index = turn_curr.turn_index

        if turn_prev is None:
            added_ids: list[str] = []
            seen_added: set[str] = set()
            for b in turn_curr.all_blocks:
                if b.block_id not in seen_added:
                    seen_added.add(b.block_id)
                    added_ids.append(b.block_id)

            growth = (
                turn_curr.input_tokens
                if turn_curr.input_tokens > 0
                else sum(b.token_count for b in turn_curr.all_blocks)
            )

            return TurnDelta(
                turn_index=turn_index,
                added_block_ids=added_ids,
                removed_block_ids=[],
                persisted_block_ids=[],
                mutated_block_ids=[],
                cache_breakpoint_block_id=None,
                token_growth=growth,
            )

        # Build lookup set of keys from previous and current turns
        prev_blocks = turn_prev.all_blocks
        curr_blocks = turn_curr.all_blocks

        prev_by_key: dict[tuple[str, str], ContextBlock] = {}
        for b in prev_blocks:
            key = _block_match_key(b)
            if key not in prev_by_key:
                prev_by_key[key] = b

        curr_by_key: dict[tuple[str, str], ContextBlock] = {}
        for b in curr_blocks:
            key = _block_match_key(b)
            if key not in curr_by_key:
                curr_by_key[key] = b

        added_ids = []
        persisted_ids: list[str] = []
        mutated_ids: list[str] = []
        seen_curr: set[str] = set()

        for b in curr_blocks:
            if b.block_id in seen_curr:
                continue
            seen_curr.add(b.block_id)

            key = _block_match_key(b)
            if key in prev_by_key:
                prev_b = prev_by_key[key]
                if b.identity_key and b.content_hash != prev_b.content_hash:
                    mutated_ids.append(b.block_id)
                else:
                    persisted_ids.append(b.block_id)
            else:
                added_ids.append(b.block_id)

        removed_ids: list[str] = []
        seen_prev: set[str] = set()
        for b in prev_blocks:
            if b.block_id in seen_prev:
                continue
            seen_prev.add(b.block_id)

            key = _block_match_key(b)
            if key not in curr_by_key:
                removed_ids.append(b.block_id)

        # Inspect turn_curr.all_blocks in prefix order to find cache breakpoint
        persisted_set = set(persisted_ids)
        cache_breakpoint_block_id: Optional[str] = None
        for b in curr_blocks:
            if b.block_id not in persisted_set:
                cache_breakpoint_block_id = b.block_id
                break

        # Calculate token growth
        if turn_curr.input_tokens > 0 or turn_prev.input_tokens > 0:
            token_growth = turn_curr.input_tokens - turn_prev.input_tokens
        else:
            curr_tokens = sum(b.token_count for b in curr_blocks)
            prev_tokens = sum(b.token_count for b in prev_blocks)
            token_growth = curr_tokens - prev_tokens

        return TurnDelta(
            turn_index=turn_index,
            added_block_ids=added_ids,
            removed_block_ids=removed_ids,
            persisted_block_ids=persisted_ids,
            mutated_block_ids=mutated_ids,
            cache_breakpoint_block_id=cache_breakpoint_block_id,
            token_growth=token_growth,
        )

    @classmethod
    def diff(
        cls,
        turn_prev: Optional[CanonicalTurn],
        turn_curr: CanonicalTurn,
    ) -> TurnDelta:
        """Alias for compute_delta."""
        return cls.compute_delta(turn_prev, turn_curr)
