"""Unit tests for JsoncExporter serialization, schema validation, and comment parsing."""

import tempfile

from src.core.store.jsonc_exporter import JSONC_SCHEMA_URI, JsoncExporter
from src.core.store.session_store import SessionStore
from src.schema.ast import BlockType, CanonicalTurn, ContextBlock, RuleViolation, ViolationSeverity


def _make_export_turn(
    session_id: str,
    turn_index: int,
    violations: list[RuleViolation] | None = None,
) -> CanonicalTurn:
    sys_blk = ContextBlock(
        block_id=f"sys_{turn_index}",
        block_type=BlockType.SYSTEM,
        content_hash="sys_h",
        token_count=1800,
        content="System instructions",
    )
    tool_blk = ContextBlock(
        block_id=f"tool_{turn_index}",
        block_type=BlockType.TOOL_DEF,
        content_hash="tool_h",
        token_count=2400,
        content="Tool schema",
    )
    res_blk = ContextBlock(
        block_id=f"res_{turn_index}",
        block_type=BlockType.TOOL_RESULT,
        content_hash="res_h",
        token_count=14500,
        content="Large result",
    )

    return CanonicalTurn(
        turn_id=f"turn_{turn_index}",
        correlation_id=f"corr_{turn_index}",
        session_id=session_id,
        turn_index=turn_index,
        timestamp=1725000004.120 + turn_index,
        provider="anthropic",
        model="claude-3-5-sonnet-20241022",
        system_blocks=[sys_blk],
        tool_defs=[tool_blk],
        conversation_history=[],
        tool_results=[res_blk],
        assistant_blocks=[],
        input_tokens=18700,
        output_tokens=350,
        cached_read_tokens=4200,
        cached_created_tokens=14500,
        duration_ms=2450.0,
        ttft_ms=310.0,
        violations=violations or [],
        turn_cost_usd=0.048,
        wasted_cost_usd=0.021 if violations else 0.0,
    )


def test_jsonc_export_schema_compliance():
    v1 = RuleViolation(
        rule_id="CTX-001",
        severity=ViolationSeverity.WARN,
        title="Stale Tool Output Bloat",
        message="Tool injected 14.5k tokens that remain unreferenced after 2 turns.",
        estimated_waste_usd=0.021,
        suggested_fix="Prune unreferenced tool payload or apply line filtering.",
    )
    turn_0 = _make_export_turn("sess_01j7abc991", 0, violations=[v1])
    jsonc_str = JsoncExporter.export_session(
        turns=[turn_0],
        session_id="sess_01j7abc991",
        include_comments=True,
    )

    assert "// 0 = pristine, 100 = critical bloat" in jsonc_str

    # Parse JSONC with comments stripped
    parsed = JsoncExporter.parse_jsonc(jsonc_str)

    # Schema assertions
    assert parsed["$schema"] == JSONC_SCHEMA_URI
    assert parsed["sessionId"] == "sess_01j7abc991"
    assert parsed["version"] == "1.0"
    assert parsed["model"]["provider"] == "anthropic"
    assert parsed["model"]["name"] == "claude-3-5-sonnet-20241022"

    # Summary assertions
    summary = parsed["summary"]
    assert summary["totalTurns"] == 1
    assert summary["totalInputTokens"] == 18700
    assert summary["totalOutputTokens"] == 350
    assert summary["cachedInputTokens"] == 4200
    assert summary["activeViolationsCount"] == 1

    # Turns assertions
    turns = parsed["turns"]
    assert len(turns) == 1
    t0 = turns[0]
    assert t0["turnIndex"] == 0
    assert t0["correlationId"] == "corr_0"
    assert t0["timing"]["ttftMs"] == 310.0
    assert t0["timing"]["durationMs"] == 2450.0
    assert t0["tokens"]["system"] == 1800
    assert t0["tokens"]["tools"] == 2400
    assert t0["tokens"]["toolResults"] == 14500
    assert t0["cache"]["readTokens"] == 4200
    assert t0["cost"]["turnCostUSD"] == 0.048
    assert t0["cost"]["wastedCostUSD"] == 0.021
    assert len(t0["violations"]) == 1
    assert t0["violations"][0]["ruleId"] == "CTX-001"


def test_jsonc_export_to_file():
    turn = _make_export_turn("sess_file_test", 0)
    with tempfile.NamedTemporaryFile(suffix=".jsonc", delete=True) as tmp:
        exported_str = JsoncExporter.export_session(
            turns=[turn],
            session_id="sess_file_test",
            output_path=tmp.name,
            include_comments=True,
        )
        with open(tmp.name, "r", encoding="utf-8") as f:
            content = f.read()

        assert content == exported_str
        parsed = JsoncExporter.parse_jsonc(content)
        assert parsed["sessionId"] == "sess_file_test"


def test_jsonc_export_from_store():
    store = SessionStore()
    turn = _make_export_turn("sess_store_test", 0)
    store.append_turn(turn)

    jsonc_str = JsoncExporter.export_from_store(store, "sess_store_test")
    parsed = JsoncExporter.parse_jsonc(jsonc_str)
    assert parsed["sessionId"] == "sess_store_test"
    assert parsed["summary"]["totalTurns"] == 1
