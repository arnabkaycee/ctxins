"""Context pollution and prompt cache analyzer engine orchestrator."""

from __future__ import annotations

from typing import List, Optional, Union

from src.core.analyzer.cost.cost_model import CostModel
from src.core.analyzer.cost.pricing_table import ModelPricing, get_pricing
from src.core.analyzer.heuristics.base import BaseHeuristic
from src.core.analyzer.heuristics.cache001_prefix_break import PrefixBreakHeuristic
from src.core.analyzer.heuristics.ctx001_stale_tool import StaleToolHeuristic
from src.core.analyzer.heuristics.ctx002_schema_bloat import SchemaBloatHeuristic
from src.core.analyzer.heuristics.ctx003_error_loop import ErrorLoopHeuristic
from src.core.analyzer.scorer import PollutionScorer
from src.core.graph.turn_tree import ContextGraph
from src.schema.ast import CanonicalTurn, RuleViolation


class PollutionAnalyzer:
    """Orchestrates heuristic rule evaluation, financial modeling, and pollution scoring."""

    def __init__(
        self,
        heuristics: Optional[List[BaseHeuristic]] = None,
    ) -> None:
        """Initialize PollutionAnalyzer with heuristic detectors.

        Args:
            heuristics: Optional custom list of BaseHeuristic instances. If omitted,
                defaults to the standard suite (CTX-001, CTX-002, CTX-003, CACHE-001).
        """
        if heuristics is not None:
            self.heuristics: List[BaseHeuristic] = list(heuristics)
        else:
            self.heuristics = [
                StaleToolHeuristic(),
                SchemaBloatHeuristic(),
                ErrorLoopHeuristic(),
                PrefixBreakHeuristic(),
            ]

    def analyze_turn(
        self,
        turn: CanonicalTurn,
        graph: Optional[ContextGraph] = None,
        previous_turns: Optional[List[CanonicalTurn]] = None,
        pricing: Optional[ModelPricing] = None,
    ) -> List[RuleViolation]:
        """Execute heuristics against a turn, attach results, and compute cost metrics.

        Args:
            turn: CanonicalTurn to analyze.
            graph: Optional ContextGraph containing session lineage.
            previous_turns: Optional list of preceding turns.
            pricing: Optional ModelPricing override.

        Returns:
            List of detected RuleViolation objects.
        """
        resolved_pricing = pricing or get_pricing(turn.model, turn.provider)
        violations: List[RuleViolation] = []

        # 1. Run all registered heuristics
        for heuristic in self.heuristics:
            detected = heuristic.analyze(
                turn=turn,
                graph=graph,
                previous_turns=previous_turns,
            )
            violations.extend(detected)

        # 2. Attach violations to the turn
        turn.violations = violations

        # 3. Calculate financial cost metrics
        turn.turn_cost_usd = CostModel.calculate_turn_cost(turn, pricing=resolved_pricing)
        turn.wasted_cost_usd = CostModel.calculate_wasted_spend(turn, violations=violations, pricing=resolved_pricing)

        return violations

    def analyze_session(
        self,
        target: Union[ContextGraph, List[CanonicalTurn]],
    ) -> float:
        """Analyze all turns in a session or context graph and return composite score.

        Args:
            target: ContextGraph or list of CanonicalTurn objects.

        Returns:
            Normalized session pollution score (0.0 to 100.0).
        """
        turns = target.turns if isinstance(target, ContextGraph) else target
        graph = target if isinstance(target, ContextGraph) else None

        for i, turn in enumerate(turns):
            prev = turns[:i]
            self.analyze_turn(turn=turn, graph=graph, previous_turns=prev)

        return PollutionScorer.calculate_score(turns)
