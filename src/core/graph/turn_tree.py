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
                    # Persisted from immediate previous turn
                    block.first_seen_turn = rec.first_seen_turn
                    block.turns_survived = rec.turns_survived + 1
                    rec.turns_survived = block.turns_survived
                    rec.last_seen_turn = turn.turn_index
                    rec.seen_turns.append(turn.turn_index)
                else:
                    # Re-introduced after absence: retain first_seen_turn, reset survival
                    block.first_seen_turn = rec.first_seen_turn
                    block.turns_survived = 0
                    rec.turns_survived = 0
                    rec.last_seen_turn = turn.turn_index
                    rec.seen_turns.append(turn.turn_index)
            else:
                # Brand new block
                block.first_seen_turn = turn.turn_index
                block.turns_survived = 0
                rec = BlockLineage(
                    block_type=block.block_type,
                    content_hash=block.content_hash,
                    first_seen_turn=turn.turn_index,
                    last_seen_turn=turn.turn_index,
                    turns_survived=0,
                    seen_turns=[turn.turn_index],
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
        resolved_parent_id = parent_turn_id if parent_turn_id is not None else (prev_turn.turn_id if prev_turn else None)
        self._parents[turn.turn_id] = resolved_parent_id
        if resolved_parent_id:
            if resolved_parent_id not in self._children:
                self._children[resolved_parent_id] = []
            self._children[resolved_parent_id].append(turn.turn_id)

        return delta

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
        content_hash: str,
        block_type: Optional[BlockType] = None,
    ) -> Optional[BlockLineage]:
        """Retrieve the lineage record for a block matching hash and optional type."""
        if block_type is not None:
            return self._lineage.get((block_type.value, content_hash))
        for (b_type, h), rec in self._lineage.items():
            if h == content_hash:
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

    @staticmethod
    def _block_key(block: ContextBlock) -> tuple[str, str]:
        if block.content_hash:
            return (block.block_type.value, block.content_hash)
        return (block.block_type.value, block.block_id)

    def __len__(self) -> int:
        return len(self.turns)

    def __iter__(self) -> Iterator[CanonicalTurn]:
        return iter(self.turns)
