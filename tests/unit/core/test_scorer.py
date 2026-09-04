"""Unit tests for PollutionScorer composite health scoring and summary calculation."""

from src.core.analyzer.scorer import PollutionScorer
from src.schema.ast import CanonicalTurn, RuleViolation, ViolationSeverity


def _make_turn_with_violations(
    turn_index: int,
    violations: list[RuleViolation],
    input_tokens: int = 1000,
    cached_tokens: int = 500,
    turn_cost: float = 0.05,
    wasted_cost: float = 0.01,
) -> CanonicalTurn:
    return CanonicalTurn(
        turn_id=f"t-{turn_index}",
        correlation_id=f"c-{turn_index}",
        session_id="sess-scorer",
        turn_index=turn_index,
        timestamp=1710000000.0 + turn_index * 5,
        provider="anthropic",
        model="claude-3-5-sonnet",
        input_tokens=input_tokens,
        output_tokens=100,
        cached_read_tokens=cached_tokens,
        duration_ms=1200.0,
        violations=violations,
        turn_cost_usd=turn_cost,
        wasted_cost_usd=wasted_cost,
    )


def test_scorer_empty_turns_returns_zero():
    assert PollutionScorer.calculate_score([]) == 0.0
    summary = PollutionScorer.calculate_summary([])
    assert summary["pollutionScore"] == 0.0
    assert summary["totalTurns"] == 0


def test_scorer_clean_session_returns_zero():
    turns = [_make_turn_with_violations(i, []) for i in range(5)]
    assert PollutionScorer.calculate_score(turns) == 0.0


def test_scorer_warn_penalty():
    # 1 WARN violation: penalty = 8.0. Normalizer for 1 turn = 20.0 -> score = 40.0
    v_warn = RuleViolation(
        rule_id="CTX-001",
        severity=ViolationSeverity.WARN,
        title="Stale Tool",
        message="warning",
        estimated_waste_usd=0.01,
        suggested_fix="prune",
    )
    turn = _make_turn_with_violations(0, [v_warn])
    score = PollutionScorer.calculate_score([turn])
    assert score == 40.0


def test_scorer_critical_penalty():
    # 1 CRITICAL violation: penalty = 20.0. Normalizer for 1 turn = 20.0 -> score = 100.0
    v_crit = RuleViolation(
        rule_id="CTX-003",
        severity=ViolationSeverity.CRITICAL,
        title="Error Loop",
        message="error",
        estimated_waste_usd=0.02,
        suggested_fix="break",
    )
    turn = _make_turn_with_violations(0, [v_crit])
    score = PollutionScorer.calculate_score([turn])
    assert score == 100.0


def test_scorer_score_capped_at_100():
    # Multiple critical violations on a single turn
    crit1 = RuleViolation("C1", ViolationSeverity.CRITICAL, "t1", "m1", 0.01, "f1")
    crit2 = RuleViolation("C2", ViolationSeverity.CRITICAL, "t2", "m2", 0.01, "f2")
    turn = _make_turn_with_violations(0, [crit1, crit2])
    score = PollutionScorer.calculate_score([turn])
    assert score == 100.0


def test_calculate_score_from_violations():
    assert PollutionScorer.calculate_score_from_violations([], turn_count=2) == 0.0
    v_info = RuleViolation("I1", ViolationSeverity.INFO, "info", "msg", 0.0, "fix")
    # penalty = 2.0. normalizer = 1 * 20 = 20. score = 10.0
    score = PollutionScorer.calculate_score_from_violations([v_info], turn_count=1)
    assert score == 10.0


def test_calculate_summary_aggregates_properly():
    v1 = RuleViolation("CTX-001", ViolationSeverity.WARN, "t", "m", 0.01, "f")
    v2 = RuleViolation("CTX-003", ViolationSeverity.CRITICAL, "t", "m", 0.02, "f")

    t1 = _make_turn_with_violations(0, [v1], input_tokens=1000, cached_tokens=400, turn_cost=0.05, wasted_cost=0.01)
    t2 = _make_turn_with_violations(1, [v2], input_tokens=2000, cached_tokens=1600, turn_cost=0.08, wasted_cost=0.02)

    summary = PollutionScorer.calculate_summary([t1, t2])
    assert summary["totalTurns"] == 2
    assert summary["totalInputTokens"] == 3000
    assert summary["totalOutputTokens"] == 200
    assert summary["cachedInputTokens"] == 2000
    assert summary["cacheHitRatio"] == round(2000 / 3000, 4)
    assert summary["estimatedCostUSD"] == 0.13
    assert summary["potentialSavingsUSD"] == 0.03
    assert summary["activeViolationsCount"] == 2
    assert summary["violationsBySeverity"]["WARN"] == 1
    assert summary["violationsBySeverity"]["CRITICAL"] == 1
    assert summary["violationsBySeverity"]["INFO"] == 0
