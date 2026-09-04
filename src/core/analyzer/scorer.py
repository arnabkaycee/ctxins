"""Pollution scoring engine computing composite health scores and metrics."""

from __future__ import annotations

from typing import Any, Dict, List

from src.schema.ast import CanonicalTurn, RuleViolation, ViolationSeverity


class PollutionScorer:
    """Calculates normalized 0-100 composite pollution scores and summary metrics."""

    SEVERITY_WEIGHTS = {
        ViolationSeverity.INFO: 2.0,
        ViolationSeverity.WARN: 8.0,
        ViolationSeverity.CRITICAL: 20.0,
    }

    @classmethod
    def calculate_score(cls, turns: List[CanonicalTurn]) -> float:
        """Calculate normalized 0-100 score across all session turns.

        0 = pristine clean context, 100 = critical bloat and error thrashing.

        Args:
            turns: List of CanonicalTurns in the session.

        Returns:
            Normalized score between 0.0 and 100.0.
        """
        if not turns:
            return 0.0

        total_penalty = 0.0
        for turn in turns:
            for v in turn.violations:
                total_penalty += cls.SEVERITY_WEIGHTS.get(v.severity, 2.0)

        # Baseline: 1 critical violation per turn gives 100.0
        max_normalizer = len(turns) * cls.SEVERITY_WEIGHTS[ViolationSeverity.CRITICAL]
        score = (total_penalty / max_normalizer) * 100.0 if max_normalizer > 0 else 0.0
        return min(100.0, round(score, 1))

    @classmethod
    def calculate_score_from_violations(
        cls,
        violations: List[RuleViolation],
        turn_count: int = 1,
    ) -> float:
        """Calculate normalized score given an explicit list of violations."""
        if not violations or turn_count <= 0:
            return 0.0

        total_penalty = sum(
            cls.SEVERITY_WEIGHTS.get(v.severity, 2.0) for v in violations
        )
        max_normalizer = turn_count * cls.SEVERITY_WEIGHTS[ViolationSeverity.CRITICAL]
        score = (total_penalty / max_normalizer) * 100.0 if max_normalizer > 0 else 0.0
        return min(100.0, round(score, 1))

    @classmethod
    def calculate_summary(cls, turns: List[CanonicalTurn]) -> Dict[str, Any]:
        """Generate comprehensive aggregate context health summary for a session."""
        if not turns:
            return {
                "totalTurns": 0,
                "totalInputTokens": 0,
                "totalOutputTokens": 0,
                "cachedInputTokens": 0,
                "cacheHitRatio": 0.0,
                "totalDurationMs": 0.0,
                "estimatedCostUSD": 0.0,
                "pollutionScore": 0.0,
                "potentialSavingsUSD": 0.0,
                "activeViolationsCount": 0,
                "violationsBySeverity": {"INFO": 0, "WARN": 0, "CRITICAL": 0},
            }

        total_input = sum(t.input_tokens for t in turns)
        total_output = sum(t.output_tokens for t in turns)
        cached_input = sum(t.cached_read_tokens for t in turns)
        total_duration = sum(t.duration_ms for t in turns)
        total_cost = sum(t.turn_cost_usd for t in turns)
        wasted_cost = sum(t.wasted_cost_usd for t in turns)

        all_violations = [v for t in turns for v in t.violations]
        severity_counts = {"INFO": 0, "WARN": 0, "CRITICAL": 0}
        for v in all_violations:
            sev_key = v.severity.value
            severity_counts[sev_key] = severity_counts.get(sev_key, 0) + 1

        hit_ratio = round(cached_input / total_input, 4) if total_input > 0 else 0.0

        return {
            "totalTurns": len(turns),
            "totalInputTokens": total_input,
            "totalOutputTokens": total_output,
            "cachedInputTokens": cached_input,
            "cacheHitRatio": hit_ratio,
            "totalDurationMs": round(total_duration, 2),
            "estimatedCostUSD": round(total_cost, 6),
            "pollutionScore": cls.calculate_score(turns),
            "potentialSavingsUSD": round(wasted_cost, 6),
            "activeViolationsCount": len(all_violations),
            "violationsBySeverity": severity_counts,
        }
