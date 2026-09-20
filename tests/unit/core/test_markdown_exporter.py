"""Unit tests for MarkdownExporter report generation with grouped violations."""

from src.core.store.markdown_exporter import MarkdownExporter
from src.schema.ast import (
    BlockType,
    CanonicalTurn,
    ContextBlock,
    RuleViolation,
    ViolationSeverity,
)


def test_markdown_exporter_grouped_violations_across_turns():
    """Verify MarkdownExporter groups repeating warnings once with count and earlier/current turns."""
    turns = []
    violations = []

    for i in range(3):
        v = RuleViolation(
            rule_id="CTX-002",
            severity=ViolationSeverity.WARN,
            title="Tool Schema Overweight",
            message=f"Tool schemas occupy 400 tokens (Turn {i}).",
            estimated_waste_usd=0.012,
            suggested_fix="Group tools into subagents or filter tool schemas dynamically.",
            turn_index=i,
        )
        violations.append(v)
        turn = CanonicalTurn(
            turn_id=f"turn_{i}",
            correlation_id=f"corr_{i}",
            session_id="sess_md_test",
            turn_index=i,
            timestamp=1700000000.0 + i,
            provider="anthropic",
            model="claude-3-5-sonnet",
            system_blocks=[
                ContextBlock(
                    block_id=f"sys_{i}",
                    block_type=BlockType.SYSTEM,
                    content_hash=f"hash_{i}",
                    token_count=200,
                    content="System instructions",
                )
            ],
            input_tokens=1000,
            output_tokens=100,
            violations=[v],
            turn_cost_usd=0.02,
            wasted_cost_usd=0.012,
        )
        turns.append(turn)

    report = MarkdownExporter.generate_report("sess_md_test", turns, violations)

    # Should only have one header for CTX-002
    assert report.count("Tool Schema Overweight") == 1
    assert "### 1. [WARN] Tool Schema Overweight (3 occurrences across 3 turns" in report
    assert "- **Total Violations:** 3 across turns #0, #1, #2" in report
    assert "- **Current Turn (#2):** Active" in report
    assert "Tool schemas occupy 400 tokens (Turn 2)." in report
    assert "- **Earlier Turns (#0, #1):** 2 violation(s)" in report
    assert (
        "- **Remediation:** Group tools into subagents or filter tool schemas dynamically."
        in report
    )


def test_markdown_exporter_zero_violations():
    """Verify MarkdownExporter renders clean state when no violations exist."""
    turns = [
        CanonicalTurn(
            turn_id="turn_0",
            correlation_id="corr_0",
            session_id="sess_clean",
            turn_index=0,
            timestamp=1700000000.0,
            provider="anthropic",
            model="claude-3-5-sonnet",
            input_tokens=500,
            output_tokens=50,
            violations=[],
        )
    ]
    report = MarkdownExporter.generate_report("sess_clean", turns, [])
    assert "Zero Context Violations Detected" in report
