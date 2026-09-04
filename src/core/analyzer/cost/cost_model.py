"""Cost calculation engine for turns and context pollution waste."""

from __future__ import annotations

from typing import List, Optional

from src.core.analyzer.cost.pricing_table import ModelPricing, get_pricing
from src.schema.ast import CanonicalTurn, RuleViolation


class CostModel:
    """Calculates LLM token cost in USD and estimated wasted spend."""

    @staticmethod
    def calculate_turn_cost(
        turn: CanonicalTurn,
        pricing: Optional[ModelPricing] = None,
    ) -> float:
        """Calculate the estimated financial cost of a single turn in USD.

        Formula:
            Cost = (T_in - T_cache_read) * P_in +
                   T_cache_read * P_cache_read +
                   T_cache_write * P_cache_write +
                   T_out * P_out
        (where P_* are rates per 1,000 tokens).

        Args:
            turn: The CanonicalTurn to evaluate.
            pricing: Optional explicit ModelPricing. Inferred from model/provider if None.

        Returns:
            Total turn cost in USD.
        """
        rates = pricing or get_pricing(turn.model, turn.provider)

        uncached_input = max(0, turn.input_tokens - turn.cached_read_tokens)

        cost = (
            (uncached_input / 1000.0) * rates.input_cost_per_1k
            + (turn.cached_read_tokens / 1000.0) * rates.cache_read_cost_per_1k
            + (turn.cached_created_tokens / 1000.0) * rates.cache_write_cost_per_1k
            + (turn.output_tokens / 1000.0) * rates.output_cost_per_1k
        )
        return round(cost, 6)

    @staticmethod
    def calculate_wasted_spend(
        turn: CanonicalTurn,
        violations: Optional[List[RuleViolation]] = None,
        pricing: Optional[ModelPricing] = None,
    ) -> float:
        """Calculate estimated wasted spend in USD for a turn based on rule violations.

        Args:
            turn: The CanonicalTurn with context and token counts.
            violations: Optional explicit list of violations (uses turn.violations if None).
            pricing: Optional ModelPricing.

        Returns:
            Total estimated financial waste in USD.
        """
        target_violations = violations if violations is not None else turn.violations
        if not target_violations:
            return 0.0

        total_waste = sum(v.estimated_waste_usd for v in target_violations)
        return round(total_waste, 6)

    @staticmethod
    def calculate_token_waste(
        wasted_tokens: int,
        pricing: Optional[ModelPricing] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> float:
        """Calculate USD waste for a specific quantity of unreferenced or bloated tokens.

        Args:
            wasted_tokens: Number of wasted input tokens.
            pricing: Optional explicit ModelPricing.
            model: Optional model name if pricing not provided.
            provider: Optional provider name if pricing not provided.

        Returns:
            Waste in USD.
        """
        rates = pricing or get_pricing(model, provider)
        return round((wasted_tokens / 1000.0) * rates.input_cost_per_1k, 6)
