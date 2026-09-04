"""Base interface for context pollution and prompt cache heuristics."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from src.core.graph.turn_tree import ContextGraph
from src.schema.ast import CanonicalTurn, RuleViolation


class BaseHeuristic(ABC):
    """Abstract base class for all context pollution detection heuristics."""

    rule_id: str
    name: str
    description: str

    @abstractmethod
    def analyze(
        self,
        turn: CanonicalTurn,
        graph: Optional[ContextGraph] = None,
        previous_turns: Optional[List[CanonicalTurn]] = None,
    ) -> List[RuleViolation]:
        """Analyze a turn within context lineage and return detected violations.

        Args:
            turn: The current CanonicalTurn to inspect.
            graph: Optional ContextGraph containing session history and block lineage.
            previous_turns: Optional explicit list of preceding turns. If omitted,
                extracted from graph.

        Returns:
            List of RuleViolation records triggered by this turn.
        """
        pass

    @staticmethod
    def resolve_previous_turns(
        turn: CanonicalTurn,
        graph: Optional[ContextGraph] = None,
        previous_turns: Optional[List[CanonicalTurn]] = None,
    ) -> List[CanonicalTurn]:
        """Helper to resolve preceding turns from either explicit list or graph."""
        if previous_turns is not None:
            return previous_turns
        if graph is not None:
            # If turn is already in graph, take strictly earlier turns
            return [t for t in graph.turns if t.turn_index < turn.turn_index]
        return []
