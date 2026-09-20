"""Session context graph and turn lineage tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Optional

from src.core.graph.diff import TurnDiffEngine
from src.schema.ast import BlockType, CanonicalTurn, ContextBlock, TurnDelta


@dataclass(slots=True)
class BlockLineage:
    """Historical tracking record for an atomic context block across turns."""

    block_type: BlockType
    content_hash: str
    first_seen_turn: int
    last_seen_turn: int
    turns_survived: int
    seen_turns: list[int] = field(default_factory=list)
    identity_key: str = ""
    historical_hashes: list[str] = field(default_factory=list)


class ContextGraph:
    """Session DAG and turn lineage tracker.

    Tracks conversational turns and block turn-over-turn survival based on
    matching content hashes and block types. Computes TurnDeltas on insertion.
    """

    def __init__(self, session_id: Optional[str] = None) -> None:
        """Initialize ContextGraph.

        Args:
            session_id: Optional session identifier. Inferred from first turn if omitted.
        """
        self.session_id: Optional[str] = session_id
        self.turns: list[CanonicalTurn] = []
        self._turns_by_id: dict[str, CanonicalTurn] = {}
        self._turns_by_index: dict[int, CanonicalTurn] = {}
        self._lineage: dict[tuple[str, str], BlockLineage] = {}
        self._deltas: list[TurnDelta] = []
        self._deltas_by_turn: dict[int, TurnDelta] = {}
        self._parents: dict[str, Optional[str]] = {}
        self._children: dict[str, list[str]] = {}

    @property
    def turn_count(self) -> int:
        """Total number of turns recorded."""
        return len(self.turns)

    def add_turn(
        self,
        turn: CanonicalTurn,
        parent_turn_id: Optional[str] = None,
    ) -> TurnDelta:
        """Add a canonical turn to the context graph.

        Updates block first_seen_turn and turns_survived based on matching
        content_hash and block_type. Calculates the TurnDelta between the
        preceding turn and this turn.

        Args:
            turn: CanonicalTurn instance to insert.
            parent_turn_id: Optional parent turn ID. Defaults to preceding turn if omitted.

        Returns:
            The calculated TurnDelta for this turn.
        """
        if self.session_id is None and turn.session_id:
            self.session_id = turn.session_id

        # Update block lineage for all blocks in this turn
        for block in turn.all_blocks:
            key = self._block_key(block)
            if key in self._lineage:
                rec = self._lineage[key]
                if rec.last_seen_turn == turn.turn_index:
                    # Already processed earlier in this same turn
                    block.first_seen_turn = rec.first_seen_turn
                    block.turns_survived = rec.turns_survived
                elif rec.last_seen_turn == turn.turn_index - 1:
                    # Present in immediate previous turn
                    block.first_seen_turn = rec.first_seen_turn
                    if block.content_hash == rec.content_hash:
                        # Persisted without mutation
                        block.turns_survived = rec.turns_survived + 1
                    else:
                        # Mutated block: survival streak resets
                        block.turns_survived = 0
                        if rec.content_hash and rec.content_hash not in rec.historical_hashes:
                            rec.historical_hashes.append(rec.content_hash)
                        rec.content_hash = block.content_hash
                        if block.content_hash and block.content_hash not in rec.historical_hashes:
                            rec.historical_hashes.append(block.content_hash)
                    rec.turns_survived = block.turns_survived
                    rec.last_seen_turn = turn.turn_index
                    rec.seen_turns.append(turn.turn_index)
                else:
                    # Re-introduced after absence: retain first_seen_turn, reset survival
                    block.first_seen_turn = rec.first_seen_turn
                    block.turns_survived = 0
                    if block.content_hash != rec.content_hash:
                        if rec.content_hash and rec.content_hash not in rec.historical_hashes:
                            rec.historical_hashes.append(rec.content_hash)
                        rec.content_hash = block.content_hash
                        if block.content_hash and block.content_hash not in rec.historical_hashes:
                            rec.historical_hashes.append(block.content_hash)
                    rec.turns_survived = 0
                    rec.last_seen_turn = turn.turn_index
                    rec.seen_turns.append(turn.turn_index)
            else:
                # Brand new block
                block.first_seen_turn = turn.turn_index
                block.turns_survived = 0
                historical_hashes = [block.content_hash] if block.content_hash else []
                rec = BlockLineage(
                    block_type=block.block_type,
                    content_hash=block.content_hash,
                    first_seen_turn=turn.turn_index,
                    last_seen_turn=turn.turn_index,
                    turns_survived=0,
                    seen_turns=[turn.turn_index],
                    identity_key=block.identity_key,
                    historical_hashes=historical_hashes,
                )
                self._lineage[key] = rec

        # Compute delta with previous turn
        prev_turn = self.turns[-1] if self.turns else None
        delta = TurnDiffEngine.compute_delta(prev_turn, turn)

        # Record turn and DAG lineage
        self.turns.append(turn)
        self._turns_by_id[turn.turn_id] = turn
        self._turns_by_index[turn.turn_index] = turn
        self._deltas.append(delta)
        self._deltas_by_turn[turn.turn_index] = delta

        # Link parent/child in DAG
        resolved_parent_id = (
            parent_turn_id
            if parent_turn_id is not None
            else (prev_turn.turn_id if prev_turn else None)
        )
        self._parents[turn.turn_id] = resolved_parent_id
        if resolved_parent_id:
            if resolved_parent_id not in self._children:
                self._children[resolved_parent_id] = []
            self._children[resolved_parent_id].append(turn.turn_id)

        return delta

    def update_turn(self, turn: CanonicalTurn) -> None:
        """Update an existing turn in the context graph and refresh block lineage."""
        if 0 <= turn.turn_index < len(self.turns):
            self.turns[turn.turn_index] = turn
        elif self.turns:
            self.turns[-1] = turn

        self._turns_by_id[turn.turn_id] = turn
        self._turns_by_index[turn.turn_index] = turn

        for block in turn.all_blocks:
            key = self._block_key(block)
            if key in self._lineage:
                rec = self._lineage[key]
                rec.last_seen_turn = turn.turn_index
                if turn.turn_index not in rec.seen_turns:
                    rec.seen_turns.append(turn.turn_index)
            else:
                historical_hashes = [block.content_hash] if block.content_hash else []
                self._lineage[key] = BlockLineage(
                    block_type=block.block_type,
                    content_hash=block.content_hash,
                    first_seen_turn=turn.turn_index,
                    last_seen_turn=turn.turn_index,
                    turns_survived=0,
                    seen_turns=[turn.turn_index],
                    identity_key=block.identity_key,
                    historical_hashes=historical_hashes,
                )

    def get_turn(self, turn_index: int) -> Optional[CanonicalTurn]:
        """Retrieve a turn by its 0-based turn index."""
        return self._turns_by_index.get(turn_index)

    def get_turn_by_id(self, turn_id: str) -> Optional[CanonicalTurn]:
        """Retrieve a turn by its turn_id / correlation_id."""
        return self._turns_by_id.get(turn_id)

    def get_delta(self, turn_index: int) -> Optional[TurnDelta]:
        """Retrieve the TurnDelta calculated for a specific turn index."""
        return self._deltas_by_turn.get(turn_index)

    def get_all_deltas(self) -> list[TurnDelta]:
        """Return all calculated TurnDeltas in sequence."""
        return list(self._deltas)

    def get_lineage(
        self,
        content_hash: Optional[str] = None,
        block_type: Optional[BlockType] = None,
        identity_key: Optional[str] = None,
    ) -> Optional[BlockLineage]:
        """Retrieve the lineage record for a block matching hash, identity key, and optional type."""
        target_identity = identity_key
        if target_identity is not None:
            if block_type is not None:
                key = (block_type.value, target_identity)
                if key in self._lineage:
                    return self._lineage[key]
            for rec in self._lineage.values():
                if rec.identity_key == target_identity and (
                    block_type is None or rec.block_type == block_type
                ):
                    return rec

        if content_hash is not None:
            if block_type is not None:
                key = (block_type.value, content_hash)
                if key in self._lineage:
                    return self._lineage[key]
            for rec in self._lineage.values():
                if (rec.content_hash == content_hash or content_hash in rec.historical_hashes) and (
                    block_type is None or rec.block_type == block_type
                ):
                    return rec
            # Fallback: check if the string passed as content_hash matches identity_key
            for rec in self._lineage.values():
                if rec.identity_key == content_hash and (
                    block_type is None or rec.block_type == block_type
                ):
                    return rec

        return None

    def get_parent_turn(self, turn_id: str) -> Optional[CanonicalTurn]:
        """Retrieve the parent CanonicalTurn of the given turn_id."""
        parent_id = self._parents.get(turn_id)
        return self._turns_by_id.get(parent_id) if parent_id else None

    def get_child_turns(self, turn_id: str) -> list[CanonicalTurn]:
        """Retrieve child CanonicalTurns branched or stepped from the given turn_id."""
        child_ids = self._children.get(turn_id, [])
        return [self._turns_by_id[cid] for cid in child_ids if cid in self._turns_by_id]

    def get_surviving_blocks(self, min_turns: int = 1) -> list[ContextBlock]:
        """Return all blocks in the most recent turn that have survived >= min_turns."""
        if not self.turns:
            return []
        latest = self.turns[-1]
        return [b for b in latest.all_blocks if b.turns_survived >= min_turns]

    def get_tool_call_for_result(
        self,
        result_block_or_id: ContextBlock | str,
    ) -> Optional[tuple[CanonicalTurn, ContextBlock]]:
        """Find the CanonicalTurn and ContextBlock of the tool call that produced this result.

        Supports querying across all turns in the session DAG by matching call_id,
        metadata tool_use_id, or tool name.

        Args:
            result_block_or_id: ContextBlock instance or block_id string of the tool result.

        Returns:
            Tuple of (CanonicalTurn, ContextBlock) where the tool call was made, or None.
        """
        target_res: Optional[ContextBlock] = None
        if isinstance(result_block_or_id, ContextBlock):
            target_res = result_block_or_id
        else:
            for turn in self.turns:
                for b in turn.tool_results:
                    if b.block_id == result_block_or_id:
                        target_res = b
                        break
                if target_res:
                    break

        if not target_res:
            return None

        call_id = target_res.call_id or target_res.metadata.get("tool_use_id") or ""
        tool_name = (
            target_res.metadata.get("name")
            or target_res.metadata.get("tool_name")
            or ""
        )

        # 1. Exact call_id match across all turns (searching assistant_blocks + conversation_history)
        if call_id:
            for turn in self.turns:
                for b in turn.assistant_blocks + turn.conversation_history:
                    if b.block_type != BlockType.TOOL_RESULT and (
                        b.call_id == call_id
                        or b.metadata.get("tool_use_id") == call_id
                    ):
                        return (turn, b)

        # 2. Tool name fallback: search turns for assistant block with matching name
        if tool_name:
            for turn in self.turns:
                for b in turn.assistant_blocks + turn.conversation_history:
                    b_name = b.metadata.get("name") or b.metadata.get("tool_name") or ""
                    if b.block_type != BlockType.TOOL_RESULT and b_name == tool_name:
                        return (turn, b)

        return None

    def get_tool_result_for_call(
        self,
        call_block_or_id: ContextBlock | str,
    ) -> Optional[tuple[CanonicalTurn, ContextBlock]]:
        """Find the CanonicalTurn and ContextBlock of the tool result produced by this tool call.

        Args:
            call_block_or_id: ContextBlock instance or block_id string of the tool call.

        Returns:
            Tuple of (CanonicalTurn, ContextBlock) where the result was delivered, or None.
        """
        target_call: Optional[ContextBlock] = None
        if isinstance(call_block_or_id, ContextBlock):
            target_call = call_block_or_id
        else:
            for turn in self.turns:
                for b in turn.assistant_blocks + turn.conversation_history:
                    if b.block_id == call_block_or_id:
                        target_call = b
                        break
                if target_call:
                    break

        if not target_call:
            return None

        call_id = target_call.call_id or target_call.metadata.get("tool_use_id") or ""
        tool_name = (
            target_call.metadata.get("name")
            or target_call.metadata.get("tool_name")
            or ""
        )

        # 1. Exact call_id match
        if call_id:
            for turn in self.turns:
                for b in turn.tool_results:
                    if (
                        b.call_id == call_id
                        or b.metadata.get("tool_use_id") == call_id
                    ):
                        return (turn, b)

        # 2. Tool name fallback
        if tool_name:
            for turn in self.turns:
                for b in turn.tool_results:
                    b_name = b.metadata.get("name") or b.metadata.get("tool_name") or ""
                    if b_name == tool_name:
                        return (turn, b)

        return None

    @staticmethod
    def _block_key(block: ContextBlock) -> tuple[str, str]:
        if block.identity_key:
            return (block.block_type.value, block.identity_key)
        if block.content_hash:
            return (block.block_type.value, block.content_hash)
        return (block.block_type.value, block.block_id)

    def __len__(self) -> int:
        return len(self.turns)

    def __iter__(self) -> Iterator[CanonicalTurn]:
        return iter(self.turns)
