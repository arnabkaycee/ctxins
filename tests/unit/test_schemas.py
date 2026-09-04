"""Unit tests for shared wire and canonical AST schemas."""

import pytest

from src.schema.ast import (
    BlockType,
    CanonicalTurn,
    ContextBlock,
    RuleViolation,
    TurnDelta,
    ViolationSeverity,
)
from src.schema.wire import (
    ActiveTurnContext,
    ContentBlock,
    Provider,
    TimingMetrics,
    UsageMetrics,
    WireEnvelope,
    WireEventType,
)


def test_provider_enum():
    assert Provider.ANTHROPIC == "anthropic"
    assert Provider.OPENAI == "openai"
    assert Provider.GEMINI == "gemini"
    assert Provider.UNKNOWN == "unknown"


def test_wire_event_type():
    assert WireEventType.REQUEST_INITIATED == "REQUEST_INITIATED"
    assert WireEventType.TURN_COMPLETED == "TURN_COMPLETED"
    assert WireEventType.TURN_ERROR == "TURN_ERROR"
    assert WireEventType.SYSTEM_TELEMETRY == "SYSTEM_TELEMETRY"


def test_timing_metrics():
    t = TimingMetrics(
        request_dispatched_at=10.0,
        first_byte_received_at=10.05,
        stream_closed_at=10.5,
    )
    assert t.ttft_ms == pytest.approx(50.0)
    assert t.total_duration_ms == pytest.approx(500.0)

    # Incomplete timing
    t_pending = TimingMetrics(request_dispatched_at=10.0)
    assert t_pending.ttft_ms is None
    assert t_pending.total_duration_ms is None

    # Serialization roundtrip
    d = t.to_dict()
    assert d["ttft_ms"] == pytest.approx(50.0)
    recovered = TimingMetrics.from_dict(d)
    assert recovered.request_dispatched_at == 10.0
    assert recovered.first_byte_received_at == 10.05
    assert recovered.stream_closed_at == 10.5


def test_usage_metrics_serialization():
    u = UsageMetrics(
        input_tokens=100,
        output_tokens=50,
        cache_creation_input_tokens=20,
        cache_read_input_tokens=80,
        reasoning_tokens=15,
    )
    d = u.to_dict()
    recovered = UsageMetrics.from_dict(d)
    assert recovered == u


def test_content_block_serialization():
    cb = ContentBlock(
        index=0,
        block_type="tool_use",
        tool_id="call_123",
        tool_name="read_file",
        partial_json='{"path": "test.txt"}',
        parsed_input={"path": "test.txt"},
    )
    d = cb.to_dict()
    recovered = ContentBlock.from_dict(d)
    assert recovered == cb


def test_wire_envelope_serialization():
    env = WireEnvelope(
        event_type=WireEventType.TURN_COMPLETED,
        correlation_id="corr-1",
        session_id="sess-1",
        timestamp=1700000000.0,
        payload={"model": "claude-3-opus", "input_tokens": 100},
    )
    data = env.to_dict()
    assert data["event_type"] == "TURN_COMPLETED"

    raw_bytes = env.to_bytes()
    recovered = WireEnvelope.from_bytes(raw_bytes)
    assert recovered.event_type == WireEventType.TURN_COMPLETED
    assert recovered.correlation_id == "corr-1"
    assert recovered.session_id == "sess-1"
    assert recovered.timestamp == 1700000000.0
    assert recovered.payload["model"] == "claude-3-opus"


def test_active_turn_context():
    timing = TimingMetrics(request_dispatched_at=100.0)
    ctx = ActiveTurnContext(
        correlation_id="corr-1",
        session_id="sess-1",
        provider=Provider.ANTHROPIC,
        model="claude-3-5-sonnet",
        timing=timing,
        endpoint="/v1/messages",
        sanitized_headers={"host": "api.anthropic.com"},
        request_payload={"messages": []},
    )
    assert ctx.provider == Provider.ANTHROPIC
    assert ctx.model == "claude-3-5-sonnet"
    assert ctx.sanitized_headers["host"] == "api.anthropic.com"


def test_canonical_turn_and_context_blocks():
    block1 = ContextBlock(
        block_id="b1",
        block_type=BlockType.SYSTEM,
        content_hash="hash-1",
        token_count=150,
        content="You are a helpful assistant.",
        metadata={"role": "system"},
        first_seen_turn=0,
        turns_survived=3,
    )
    block2 = ContextBlock(
        block_id="b2",
        block_type=BlockType.USER_MSG,
        content_hash="hash-2",
        token_count=30,
        content="Hello!",
    )
    violation = RuleViolation(
        rule_id="CTX-001",
        severity=ViolationSeverity.WARN,
        title="Stale Tool Output",
        message="Tool output not referenced in 3 turns",
        estimated_waste_usd=0.015,
        suggested_fix="Prune tool output from prompt",
        block_ids=["b1"],
    )

    turn = CanonicalTurn(
        turn_id="turn-1",
        correlation_id="corr-1",
        session_id="sess-1",
        turn_index=1,
        timestamp=1700000010.0,
        provider="anthropic",
        model="claude-3-5-sonnet",
        system_blocks=[block1],
        conversation_history=[block2],
        input_tokens=180,
        output_tokens=40,
        duration_ms=450.0,
        ttft_ms=75.0,
        violations=[violation],
        turn_cost_usd=0.02,
        wasted_cost_usd=0.015,
    )

    assert turn.total_tokens == 220
    assert len(turn.all_blocks) == 2

    # Serialization roundtrip
    data = turn.to_dict()
    recovered = CanonicalTurn.from_dict(data)
    assert recovered.turn_id == "turn-1"
    assert recovered.total_tokens == 220
    assert len(recovered.system_blocks) == 1
    assert recovered.system_blocks[0].content_hash == "hash-1"
    assert recovered.system_blocks[0].block_type == BlockType.SYSTEM
    assert len(recovered.violations) == 1
    assert recovered.violations[0].severity == ViolationSeverity.WARN


def test_turn_delta():
    delta = TurnDelta(
        turn_index=2,
        added_block_ids=["b3"],
        removed_block_ids=["b1"],
        persisted_block_ids=["b2"],
        token_growth=50,
    )
    data = delta.to_dict()
    recovered = TurnDelta.from_dict(data)
    assert recovered == delta
