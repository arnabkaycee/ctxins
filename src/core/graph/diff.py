"""Turn difference analysis engine computing deltas between sequential turns."""

from __future__ import annotations

from typing import Optional

from src.schema.ast import CanonicalTurn, ContextBlock, TurnDelta


def _block_match_key(block: ContextBlock) -> tuple[str, str]:
    """Produce unique identity key for a context block based on type and content hash.

    Falls back to block_id if content_hash is empty.
    """
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
            TurnDelta with added, removed, and persisted block IDs, and token growth.
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
                token_growth=growth,
            )

        # Build lookup set of keys from previous turn
        prev_blocks = turn_prev.all_blocks
        curr_blocks = turn_curr.all_blocks

        prev_key_set = {_block_match_key(b) for b in prev_blocks}
        curr_key_set = {_block_match_key(b) for b in curr_blocks}

        added_ids = []
        persisted_ids = []
        seen_curr: set[str] = set()

        for b in curr_blocks:
            if b.block_id in seen_curr:
                continue
            seen_curr.add(b.block_id)

            key = _block_match_key(b)
            if key in prev_key_set:
                persisted_ids.append(b.block_id)
            else:
                added_ids.append(b.block_id)

        removed_ids = []
        seen_prev: set[str] = set()
        for b in prev_blocks:
            if b.block_id in seen_prev:
                continue
            seen_prev.add(b.block_id)

            key = _block_match_key(b)
            if key not in curr_key_set:
                removed_ids.append(b.block_id)

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
