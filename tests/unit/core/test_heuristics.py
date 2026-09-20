"""Unit tests for context pollution and prompt cache heuristic rules."""

from src.core.analyzer.engine import PollutionAnalyzer
from src.core.analyzer.heuristics.cache001_prefix_break import PrefixBreakHeuristic
from src.core.analyzer.heuristics.cache_invalidation import CacheBustingPrefixRule
from src.core.analyzer.heuristics.ctx001_stale_tool import StaleToolHeuristic
from src.core.analyzer.heuristics.ctx002_schema_bloat import SchemaBloatHeuristic
from src.core.analyzer.heuristics.ctx003_error_loop import ErrorLoopHeuristic
from src.core.analyzer.heuristics.ctx004_recurring_results import RecurringResultsHeuristic
from src.core.analyzer.heuristics.zombie_context import ZombieContextRule
from src.core.graph.hasher import compute_block_hash
from src.core.graph.turn_tree import ContextGraph
from src.schema.ast import BlockType, CanonicalTurn, ContextBlock, ViolationSeverity


def _make_block(
    block_id: str,
    block_type: BlockType,
    content: str,
    token_count: int = 10,
    metadata: dict | None = None,
    turns_survived: int = 0,
    identity_key: str = "",
) -> ContextBlock:
    return ContextBlock(
        block_id=block_id,
        block_type=block_type,
        content_hash=compute_block_hash(content),
        token_count=token_count,
        content=content,
        metadata=metadata or {},
        turns_survived=turns_survived,
        identity_key=identity_key,
    )


def _make_turn(
    turn_index: int,
    blocks: list[ContextBlock],
    session_id: str = "sess-test",
    model: str = "claude-3-5-sonnet",
    provider: str = "anthropic",
    input_tokens: int = 0,
) -> CanonicalTurn:
    sys_blocks = [b for b in blocks if b.block_type in (BlockType.SYSTEM, BlockType.SKILL)]
    tool_defs = [b for b in blocks if b.block_type == BlockType.TOOL_DEF]
    history = [b for b in blocks if b.block_type in (BlockType.USER_MSG, BlockType.ASSISTANT_MSG)]

    tool_results = [b for b in blocks if b.block_type == BlockType.TOOL_RESULT]
    assistant_blocks = [b for b in blocks if b.block_type == BlockType.ASSISTANT_MSG]

    eff_input = input_tokens or sum(
        b.token_count for b in blocks if b.block_type != BlockType.ASSISTANT_MSG
    )

    return CanonicalTurn(
        turn_id=f"turn-{turn_index}",
        correlation_id=f"corr-{turn_index}",
        session_id=session_id,
        turn_index=turn_index,
        timestamp=1710000000.0 + turn_index * 10,
        provider=provider,
        model=model,
        system_blocks=sys_blocks,
        tool_defs=tool_defs,
        conversation_history=history,
        tool_results=tool_results,
        assistant_blocks=assistant_blocks,
        input_tokens=eff_input,
        output_tokens=50,
    )


# ===========================================================================
# CTX-001: Stale Tool Output Tests
# ===========================================================================


def test_ctx001_stale_tool_triggers_after_3_turns():
    """Turn 0 has 4,000-token tool result. Turns 1, 2, 3 linger without reference."""
    heuristic = StaleToolHeuristic(min_tokens=3000, min_turns=3)

    tool_res = _make_block(
        block_id="tool_res_1",
        block_type=BlockType.TOOL_RESULT,
        content="HUGE TOOL OUTPUT DATA",
        token_count=4000,
        metadata={"tool_use_id": "call_abc_123"},
    )
    user_msg = _make_block("u1", BlockType.USER_MSG, "Continue work", token_count=10)
    asst_msg = _make_block(
        "a1", BlockType.ASSISTANT_MSG, "I am thinking about another topic", token_count=20
    )

    turn_0 = _make_turn(0, [tool_res, asst_msg])
    turn_1 = _make_turn(1, [tool_res, user_msg, asst_msg])
    turn_2 = _make_turn(2, [tool_res, user_msg, asst_msg])
    turn_3 = _make_turn(3, [tool_res, user_msg, asst_msg])

    # Turn 0, 1, 2: not yet 3 surviving turns
    assert heuristic.analyze(turn_0, previous_turns=[]) == []
    assert heuristic.analyze(turn_1, previous_turns=[turn_0]) == []
    assert heuristic.analyze(turn_2, previous_turns=[turn_0, turn_1]) == []

    # Turn 3: lingering for >= 3 turns without reference
    violations = heuristic.analyze(turn_3, previous_turns=[turn_0, turn_1, turn_2])
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "CTX-001"
    assert v.severity == ViolationSeverity.WARN
    assert "call_abc_123" not in asst_msg.content
    assert v.estimated_waste_usd > 0
    assert "tool_res_1" in v.block_ids


def test_ctx001_not_flagged_when_assistant_references_tool():
    """If the assistant message references the tool_use_id, no violation is raised."""
    heuristic = StaleToolHeuristic(min_tokens=3000, min_turns=3)

    tool_res = _make_block(
        block_id="tool_res_1",
        block_type=BlockType.TOOL_RESULT,
        content="HUGE TOOL OUTPUT DATA",
        token_count=4000,
        metadata={"tool_use_id": "call_abc_123"},
    )
    asst_ref = _make_block(
        "a_ref",
        BlockType.ASSISTANT_MSG,
        "Based on result from call_abc_123, here is the answer.",
        token_count=30,
    )

    turn_0 = _make_turn(0, [tool_res])
    turn_1 = _make_turn(1, [tool_res])
    turn_2 = _make_turn(2, [tool_res])
    turn_3 = _make_turn(3, [tool_res, asst_ref])

    violations = heuristic.analyze(turn_3, previous_turns=[turn_0, turn_1, turn_2])
    assert violations == []


def test_ctx001_small_tool_result_ignored():
    """Tool results below min_tokens threshold (e.g. 500 tokens) should not trigger."""
    heuristic = StaleToolHeuristic(min_tokens=3000, min_turns=3)

    tool_res = _make_block(
        block_id="tool_res_small",
        block_type=BlockType.TOOL_RESULT,
        content="small output",
        token_count=500,
        metadata={"tool_use_id": "call_small"},
    )
    turns = [_make_turn(i, [tool_res]) for i in range(4)]
    violations = heuristic.analyze(turns[3], previous_turns=turns[:3])
    assert violations == []


# ===========================================================================
# CTX-002: Tool Schema Overweight Tests
# ===========================================================================


def test_ctx002_schema_bloat_triggers_on_high_ratio_low_use():
    """20 tools consuming 5,000 tokens (50% of 10,000 input tokens). Only 1 tool invoked."""
    heuristic = SchemaBloatHeuristic(
        max_schema_ratio=0.35, min_tool_count=5, max_invocation_ratio=0.15
    )

    # 20 tool definitions, each 250 tokens = 5,000 tokens
    tool_defs = [
        _make_block(
            f"tool_def_{i}",
            BlockType.TOOL_DEF,
            f'{{"name": "tool_{i}", "description": "description of tool {i}"}}',
            token_count=250,
            metadata={"name": f"tool_{i}"},
        )
        for i in range(20)
    ]

    # Only tool_0 is invoked across turns
    called_tool_res = _make_block(
        "res_0",
        BlockType.TOOL_RESULT,
        '{"status": "ok"}',
        token_count=50,
        metadata={"name": "tool_0"},
    )

    turns = []
    for idx in range(5):
        blocks = list(tool_defs)
        if idx == 1:
            blocks.append(called_tool_res)
        turn = _make_turn(idx, blocks, input_tokens=10000)
        turns.append(turn)

    violations = heuristic.analyze(turns[-1], previous_turns=turns[:-1])
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "CTX-002"
    assert v.severity == ViolationSeverity.WARN
    assert "occupy 5000 tokens" in v.message
    assert "1/20 tools" in v.message
    assert v.estimated_waste_usd > 0


def test_ctx002_healthy_tool_usage_does_not_trigger():
    """When > 15% of tools are invoked (e.g. 5 out of 10), no violation should be raised."""
    heuristic = SchemaBloatHeuristic(
        max_schema_ratio=0.35, min_tool_count=5, max_invocation_ratio=0.15
    )

    tool_defs = [
        _make_block(
            f"tool_def_{i}",
            BlockType.TOOL_DEF,
            f'{{"name": "tool_{i}"}}',
            token_count=400,
            metadata={"name": f"tool_{i}"},
        )
        for i in range(10)
    ]

    called_results = [
        _make_block(
            f"res_{i}",
            BlockType.TOOL_RESULT,
            "result",
            token_count=20,
            metadata={"name": f"tool_{i}"},
        )
        for i in range(5)  # 50% invoked
    ]

    turn_0 = _make_turn(0, tool_defs + called_results, input_tokens=8000)
    violations = heuristic.analyze(turn_0, previous_turns=[])
    assert violations == []


# ===========================================================================
# CTX-003: Repetitive Error Loop Tests
# ===========================================================================


def test_ctx003_error_loop_triggers_on_3_consecutive_errors():
    """3 consecutive turns with tool results having is_error: True triggers CRITICAL."""
    heuristic = ErrorLoopHeuristic(consecutive_errors=3)

    err1 = _make_block(
        "err_1",
        BlockType.TOOL_RESULT,
        "File not found: /tmp/missing.txt",
        token_count=20,
        metadata={"is_error": True},
    )
    err2 = _make_block(
        "err_2",
        BlockType.TOOL_RESULT,
        "File not found: /tmp/missing.txt (retry 1)",
        token_count=22,
        metadata={"is_error": True},
    )
    err3 = _make_block(
        "err_3",
        BlockType.TOOL_RESULT,
        "File not found: /tmp/missing.txt (retry 2)",
        token_count=22,
        metadata={"is_error": True},
    )

    turn_0 = _make_turn(0, [err1])
    turn_1 = _make_turn(1, [err2])
    turn_2 = _make_turn(2, [err3])

    # 1 or 2 turns do not trigger
    assert heuristic.analyze(turn_0, previous_turns=[]) == []
    assert heuristic.analyze(turn_1, previous_turns=[turn_0]) == []

    # 3 consecutive turns trigger CTX-003
    violations = heuristic.analyze(turn_2, previous_turns=[turn_0, turn_1])
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "CTX-003"
    assert v.severity == ViolationSeverity.CRITICAL
    assert "across 3 consecutive turns" in v.message


def test_ctx003_resolved_error_does_not_trigger():
    """If turn 2 succeeds (is_error: False), the error streak is broken."""
    heuristic = ErrorLoopHeuristic(consecutive_errors=3)

    err1 = _make_block("err_1", BlockType.TOOL_RESULT, "error", metadata={"is_error": True})
    err2 = _make_block("err_2", BlockType.TOOL_RESULT, "error", metadata={"is_error": True})
    ok_res = _make_block(
        "ok_3", BlockType.TOOL_RESULT, "Success: file created", metadata={"is_error": False}
    )

    turn_0 = _make_turn(0, [err1])
    turn_1 = _make_turn(1, [err2])
    turn_2 = _make_turn(2, [ok_res])

    violations = heuristic.analyze(turn_2, previous_turns=[turn_0, turn_1])
    assert violations == []


# ===========================================================================
# CACHE-001: Prompt Cache Dynamic Prefix Invalidation Tests
# ===========================================================================


def test_cache001_detects_mutated_system_prompt_prefix():
    """Turn 1: System 'Instructions'. Turn 2: System '[Time: 12:00:01] Instructions'."""
    heuristic = PrefixBreakHeuristic(max_token_drift=100)

    sys_1 = _make_block(
        "sys_1", BlockType.SYSTEM, "Instructions for agent execution", token_count=100
    )
    sys_2 = _make_block(
        "sys_2",
        BlockType.SYSTEM,
        "[Time: 12:00:01] Instructions for agent execution",
        token_count=105,
    )

    turn_1 = _make_turn(1, [sys_1], input_tokens=2000)
    turn_2 = _make_turn(2, [sys_2], input_tokens=2500)

    violations = heuristic.analyze(turn_2, previous_turns=[turn_1])
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "CACHE-001"
    assert v.severity == ViolationSeverity.CRITICAL
    assert "System prompt prefix was modified" in v.message
    assert v.estimated_waste_usd > 0
    assert "sys_2" in v.block_ids


def test_cache001_static_system_prompt_no_violation():
    """If system prompt remains identical between turns, no cache break is raised."""
    heuristic = PrefixBreakHeuristic()

    sys = _make_block("sys_static", BlockType.SYSTEM, "Static instructions", token_count=100)
    turn_1 = _make_turn(1, [sys], input_tokens=1000)
    turn_2 = _make_turn(2, [sys], input_tokens=1200)

    violations = heuristic.analyze(turn_2, previous_turns=[turn_1])
    assert violations == []


def test_cache001_multi_block_decomposed_system_prompt_detects_mutation():
    """When system prompt is decomposed into multiple blocks, mutation in subsequent blocks is detected."""
    heuristic = PrefixBreakHeuristic()

    sys_preamble = _make_block("sys_0", BlockType.SYSTEM, "Root system prompt", token_count=100)
    sys_dyn_1 = _make_block("sys_1", BlockType.SYSTEM, "State: timestamp=100", token_count=40)
    sys_dyn_2 = _make_block("sys_1", BlockType.SYSTEM, "State: timestamp=101", token_count=40)

    turn_1 = _make_turn(0, [sys_preamble, sys_dyn_1], input_tokens=1500)
    turn_2 = _make_turn(1, [sys_preamble, sys_dyn_2], input_tokens=1500)

    violations = heuristic.analyze(turn_2, previous_turns=[turn_1])
    assert len(violations) == 1
    assert violations[0].rule_id == "CACHE-001"
    assert "sys_1" in violations[0].block_ids


def test_cache001_large_system_prompt_mutation_detects_violation():
    """Large system prompt modifications (>100 tokens drift without substring match) are detected."""
    heuristic = PrefixBreakHeuristic(max_token_drift=100)

    sys_1 = _make_block(
        "sys_large", BlockType.SYSTEM, "Instructions version A: " + "alpha " * 100, token_count=200
    )
    sys_2 = _make_block(
        "sys_large",
        BlockType.SYSTEM,
        "Completely rewritten instructions version B: " + "beta " * 180,
        token_count=360,
    )

    turn_1 = _make_turn(0, [sys_1], input_tokens=2000)
    turn_2 = _make_turn(1, [sys_2], input_tokens=2000)

    violations = heuristic.analyze(turn_2, previous_turns=[turn_1])
    assert len(violations) == 1
    assert violations[0].rule_id == "CACHE-001"
    assert "sys_large" in violations[0].block_ids


def test_cache001_system_block_added_or_removed_detects_violation():
    """Adding or removing a system block changes the prefix structure and triggers CACHE-001."""
    heuristic = PrefixBreakHeuristic()

    sys_0 = _make_block("sys_0", BlockType.SYSTEM, "Preamble", token_count=50)
    sys_1 = _make_block("sys_1", BlockType.SYSTEM, "Inserted policy block", token_count=80)

    turn_1 = _make_turn(0, [sys_0], input_tokens=1000)
    turn_2 = _make_turn(1, [sys_0, sys_1], input_tokens=1080)

    violations = heuristic.analyze(turn_2, previous_turns=[turn_1])
    assert len(violations) == 1
    assert violations[0].rule_id == "CACHE-001"
    assert "sys_1" in violations[0].block_ids


# ===========================================================================
# PollutionAnalyzer Orchestration Tests
# ===========================================================================


def test_analyzer_orchestration_attaches_violations_and_costs():
    """PollutionAnalyzer runs heuristics, assigns violations, and calculates turn cost."""
    analyzer = PollutionAnalyzer()
    graph = ContextGraph(session_id="sess-orch")

    sys_1 = _make_block("s1", BlockType.SYSTEM, "System prompt", token_count=100)
    turn_0 = _make_turn(0, [sys_1], input_tokens=500)
    graph.add_turn(turn_0)
    analyzer.analyze_turn(turn_0, graph=graph)

    assert turn_0.turn_cost_usd > 0
    assert turn_0.wasted_cost_usd == 0.0
    assert len(turn_0.violations) == 0

    # Turn 1 with dynamic prefix break
    sys_2 = _make_block("s2", BlockType.SYSTEM, "[Time: 10:00:00] System prompt", token_count=105)
    turn_1 = _make_turn(1, [sys_2], input_tokens=600)
    graph.add_turn(turn_1)
    analyzer.analyze_turn(turn_1, graph=graph)

    assert len(turn_1.violations) >= 1
    assert any(v.rule_id == "CACHE-001" for v in turn_1.violations)
    assert any(v.rule_id == "CTX-007" for v in turn_1.violations)
    assert turn_1.wasted_cost_usd > 0.0


# ===========================================================================
# CTX-006: Zombie Context Detection Tests
# ===========================================================================


def test_ctx006_zombie_tool_result_triggers_after_3_turns():
    """Tool result (> 300 tokens) surviving >= 3 turns unreferenced triggers CTX-006."""
    rule = ZombieContextRule(min_tokens=300, min_turns=3)

    tool_res = _make_block(
        block_id="tool_res_zombie",
        block_type=BlockType.TOOL_RESULT,
        content="LARGE QUERY RESULTS IN CONTEXT " * 30,
        token_count=500,
        metadata={"tool_use_id": "call_sql_query_42"},
    )
    user_msg = _make_block("u1", BlockType.USER_MSG, "What is the weather today?", token_count=10)
    asst_msg = _make_block("a1", BlockType.ASSISTANT_MSG, "The weather is sunny.", token_count=10)

    turn_0 = _make_turn(0, [tool_res, asst_msg], model="claude-3-5-sonnet")
    turn_1 = _make_turn(1, [tool_res, user_msg, asst_msg], model="claude-3-5-sonnet")
    turn_2 = _make_turn(2, [tool_res, user_msg, asst_msg], model="claude-3-5-sonnet")
    turn_3 = _make_turn(3, [tool_res, user_msg, asst_msg], model="claude-3-5-sonnet")

    # Turns 0, 1, 2 do not trigger (< 3 surviving turns)
    assert rule.analyze(turn_0, previous_turns=[]) == []
    assert rule.analyze(turn_1, previous_turns=[turn_0]) == []
    assert rule.analyze(turn_2, previous_turns=[turn_0, turn_1]) == []

    # Turn 3 triggers CTX-006
    violations = rule.analyze(turn_3, previous_turns=[turn_0, turn_1, turn_2])
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "CTX-006"
    assert v.title == "Zombie Tool Result Detected"
    assert v.severity in (ViolationSeverity.WARN, ViolationSeverity.INFO)
    assert v.block_ids == ["tool_res_zombie"]
    assert v.suggested_fix == "Prune or summarize stale tool result from prompt"

    # Waste USD = token_count * turns_survived * model input price
    # 500 / 1000 * 3 * 0.003 = 0.0045
    assert v.estimated_waste_usd == 0.0045


def test_ctx006_referenced_tool_result_does_not_trigger():
    """Referencing tool_use_id in recent turns suppresses CTX-006 violation."""
    rule = ZombieContextRule(min_tokens=300, min_turns=3)

    tool_res = _make_block(
        block_id="tool_res_1",
        block_type=BlockType.TOOL_RESULT,
        content="TOOL DATA",
        token_count=600,
        metadata={"tool_use_id": "call_abc_123"},
    )
    asst_ref = _make_block(
        "a_ref",
        BlockType.ASSISTANT_MSG,
        "Referencing call_abc_123 in assistant response",
        token_count=15,
    )

    turn_0 = _make_turn(0, [tool_res])
    turn_1 = _make_turn(1, [tool_res])
    turn_2 = _make_turn(2, [tool_res])
    turn_3 = _make_turn(3, [tool_res, asst_ref])

    violations = rule.analyze(turn_3, previous_turns=[turn_0, turn_1, turn_2])
    assert violations == []


def test_ctx006_small_tool_result_ignored():
    """Tool result with <= 300 tokens does not trigger CTX-006."""
    rule = ZombieContextRule(min_tokens=300, min_turns=3)

    tool_res = _make_block(
        block_id="tool_res_small",
        block_type=BlockType.TOOL_RESULT,
        content="Short tool output",
        token_count=250,
        metadata={"tool_use_id": "call_short"},
    )

    turns = [_make_turn(i, [tool_res]) for i in range(4)]
    violations = rule.analyze(turns[3], previous_turns=turns[:3])
    assert violations == []


def test_ctx006_with_context_graph():
    """CTX-006 works when ContextGraph tracks turns_survived lineage."""
    rule = ZombieContextRule(min_tokens=300, min_turns=3)
    graph = ContextGraph(session_id="sess-zombie-graph")

    tool_res = _make_block(
        block_id="tool_res_dag",
        block_type=BlockType.TOOL_RESULT,
        content="DAG DATA " * 50,
        token_count=450,
        metadata={"tool_use_id": "call_dag_0"},
    )
    user_msg = _make_block("u1", BlockType.USER_MSG, "status update", token_count=10)

    turns = [_make_turn(i, [tool_res, user_msg]) for i in range(4)]
    for t in turns:
        graph.add_turn(t)

    violations = rule.analyze(turns[3], graph=graph)
    assert len(violations) == 1
    assert violations[0].rule_id == "CTX-006"
    assert violations[0].block_ids == ["tool_res_dag"]


# ===========================================================================
# CTX-007: Cache-Busting Prefix Mutation Tests
# ===========================================================================


def test_ctx007_prefix_mutation_system_prompt():
    """Mutating system instructions invalidates prompt cache prefix."""
    rule = CacheBustingPrefixRule()

    sys_1 = _make_block("sys_1", BlockType.SYSTEM, "You are an assistant.", token_count=100)
    sys_2 = _make_block(
        "sys_2", BlockType.SYSTEM, "You are a helpful coding assistant.", token_count=110
    )
    user_msg = _make_block("u1", BlockType.USER_MSG, "Hello", token_count=10)

    turn_1 = _make_turn(1, [sys_1, user_msg], input_tokens=1000)
    turn_2 = _make_turn(2, [sys_2, user_msg], input_tokens=1010)

    violations = rule.analyze(turn_2, previous_turns=[turn_1])
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "CTX-007"
    assert v.title == "Prefix Cache Mutation"
    assert v.severity in (ViolationSeverity.WARN, ViolationSeverity.CRITICAL)
    assert v.suggested_fix == (
        "Keep system instructions and skills immutable across turns; move dynamic state to tail"
    )
    assert v.estimated_waste_usd > 0.0
    assert "sys_2" in v.block_ids


def test_ctx007_prefix_mutation_tool_def():
    """Mutating or adding a tool definition in the prefix triggers CTX-007."""
    rule = CacheBustingPrefixRule()

    td1 = _make_block("td1", BlockType.TOOL_DEF, '{"name": "read_file"}', token_count=150)
    td2 = _make_block("td2", BlockType.TOOL_DEF, '{"name": "write_file"}', token_count=150)
    td3_new = _make_block("td3", BlockType.TOOL_DEF, '{"name": "execute_command"}', token_count=200)

    turn_1 = _make_turn(1, [td1, td2], input_tokens=300)
    turn_2 = _make_turn(2, [td1, td3_new, td2], input_tokens=500)

    violations = rule.analyze(turn_2, previous_turns=[turn_1])
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "CTX-007"
    assert v.estimated_waste_usd > 0.0


def test_ctx007_prefix_mutation_skill_block():
    """Mutating a SKILL block near top of context prefix triggers CTX-007."""
    rule = CacheBustingPrefixRule()

    sys = _make_block("sys", BlockType.SYSTEM, "Base system", token_count=100)
    skill_1 = _make_block("skill_v1", BlockType.SKILL, "Skill instructions v1", token_count=200)
    skill_2 = _make_block(
        "skill_v2", BlockType.SKILL, "Skill instructions v2 updated", token_count=220
    )
    user_msg = _make_block("u", BlockType.USER_MSG, "Do task", token_count=20)

    turn_1 = _make_turn(1, [sys, skill_1, user_msg], input_tokens=800)
    turn_2 = _make_turn(2, [sys, skill_2, user_msg], input_tokens=820)

    violations = rule.analyze(turn_2, previous_turns=[turn_1])
    assert len(violations) == 1
    assert violations[0].rule_id == "CTX-007"
    assert violations[0].severity == ViolationSeverity.CRITICAL


def test_ctx007_static_prefix_tail_continuation_no_violation():
    """Appending user and assistant messages at the tail does not bust prefix cache."""
    rule = CacheBustingPrefixRule()

    sys = _make_block("sys", BlockType.SYSTEM, "System prompt", token_count=100)
    td = _make_block("td", BlockType.TOOL_DEF, '{"name": "tool"}', token_count=150)
    u1 = _make_block("u1", BlockType.USER_MSG, "Turn 1 question", token_count=20)
    a1 = _make_block("a1", BlockType.ASSISTANT_MSG, "Turn 1 answer", token_count=30)
    u2 = _make_block("u2", BlockType.USER_MSG, "Turn 2 question", token_count=20)

    turn_1 = _make_turn(1, [sys, td, u1, a1], input_tokens=500)
    turn_2 = _make_turn(2, [sys, td, u1, a1, u2], input_tokens=520)

    violations = rule.analyze(turn_2, previous_turns=[turn_1])
    assert violations == []


def test_ctx007_with_context_graph():
    """CacheBustingPrefixRule retrieves deltas directly from ContextGraph."""
    rule = CacheBustingPrefixRule()
    graph = ContextGraph(session_id="sess-cache-dag")

    sys_1 = _make_block("sys_1", BlockType.SYSTEM, "Instructions v1", token_count=100)
    sys_2 = _make_block("sys_2", BlockType.SYSTEM, "Instructions v2 with mutation", token_count=120)

    turn_1 = _make_turn(1, [sys_1], input_tokens=1000)
    turn_2 = _make_turn(2, [sys_2], input_tokens=1020)

    graph.add_turn(turn_1)
    graph.add_turn(turn_2)

    violations = rule.analyze(turn_2, graph=graph)
    assert len(violations) == 1
    assert violations[0].rule_id == "CTX-007"
    assert violations[0].block_ids == ["sys_2"]


# ===========================================================================
# CTX-004: Recurring Execution Results Exceeded Tests
# ===========================================================================


def test_ctx004_below_threshold_no_violation():
    """Tool result in single turn below occurrence threshold does not trigger."""
    heuristic = RecurringResultsHeuristic(token_threshold=2000, occurrence_threshold=2)

    tool_res = _make_block(
        "res_1",
        BlockType.TOOL_RESULT,
        "pytest collected 10 items ... passed",
        token_count=1500,
        metadata={"tool_use_id": "call_1"},
    )
    turn = _make_turn(0, [tool_res], input_tokens=4000)

    violations = heuristic.analyze(turn, previous_turns=[])
    assert violations == []


def test_ctx004_recurring_results_exceeds_token_threshold():
    """Recurring identical tool result across 2 turns exceeding 2,000 tokens triggers CTX-004."""
    heuristic = RecurringResultsHeuristic(token_threshold=2000, occurrence_threshold=2)

    res_content = "================ 250 items passed in test suite ================"
    tool_res_1 = _make_block(
        "res_1",
        BlockType.TOOL_RESULT,
        res_content,
        token_count=2200,
        metadata={"tool_use_id": "call_pytest_1"},
    )
    tool_res_2 = _make_block(
        "res_2",
        BlockType.TOOL_RESULT,
        res_content,
        token_count=2200,
        metadata={"tool_use_id": "call_pytest_2"},
    )

    turn_1 = _make_turn(1, [tool_res_1], input_tokens=5000)
    turn_2 = _make_turn(2, [tool_res_2], input_tokens=5000)

    violations = heuristic.analyze(turn_2, previous_turns=[turn_1])
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "CTX-004"
    assert v.severity == ViolationSeverity.WARN
    assert "2,200 tokens" in v.message
    assert "exceeded threshold" in v.message
    assert "Shrink context" in v.suggested_fix
    assert "/compact" in v.suggested_fix
    assert "Start a fresh session" in v.suggested_fix
    assert "/clear" in v.suggested_fix
    assert "res_2" in v.block_ids


def test_ctx004_critical_severity_on_large_recurring_volume():
    """Recurring tool results exceeding 5,000 tokens trigger CRITICAL severity."""
    heuristic = RecurringResultsHeuristic(token_threshold=2000, occurrence_threshold=2)

    res_content = "DUMP OF LARGE DATASET WITH REPEATED LINES " * 100
    res_1 = _make_block(
        "res_large_1",
        BlockType.TOOL_RESULT,
        res_content,
        token_count=5500,
        metadata={"tool_use_id": "call_dump_1"},
    )
    res_2 = _make_block(
        "res_large_2",
        BlockType.TOOL_RESULT,
        res_content,
        token_count=5500,
        metadata={"tool_use_id": "call_dump_2"},
    )

    turn_1 = _make_turn(1, [res_1], input_tokens=10000)
    turn_2 = _make_turn(2, [res_2], input_tokens=10000)

    violations = heuristic.analyze(turn_2, previous_turns=[turn_1])
    assert len(violations) == 1
    assert violations[0].severity == ViolationSeverity.CRITICAL


def test_ctx004_multiple_identical_results_in_same_turn():
    """Duplicate tool results injected in the same turn exceeding threshold triggers CTX-004."""
    heuristic = RecurringResultsHeuristic(token_threshold=2000, occurrence_threshold=2)

    content = "FINDINGS TRACE REPORT"
    res_a = _make_block("res_a", BlockType.TOOL_RESULT, content, token_count=1200)
    res_b = _make_block("res_b", BlockType.TOOL_RESULT, content, token_count=1200)

    turn = _make_turn(1, [res_a, res_b], input_tokens=5000)
    violations = heuristic.analyze(turn, previous_turns=[])

    assert len(violations) == 1
    assert violations[0].rule_id == "CTX-004"
    assert "2,400 tokens" in violations[0].message


def test_ctx004_integrated_in_pollution_analyzer():
    """PollutionAnalyzer includes RecurringResultsHeuristic by default."""
    analyzer = PollutionAnalyzer()
    has_ctx004 = any(isinstance(h, RecurringResultsHeuristic) for h in analyzer.heuristics)
    assert has_ctx004

    res_content = "REPEATED TEST RESULT CONTENT"
    res_1 = _make_block("res_1", BlockType.TOOL_RESULT, res_content, token_count=2500)
    res_2 = _make_block("res_2", BlockType.TOOL_RESULT, res_content, token_count=2500)

    turn_1 = _make_turn(1, [res_1], input_tokens=6000)
    turn_2 = _make_turn(2, [res_2], input_tokens=6000)

    detected = analyzer.analyze_turn(turn_2, previous_turns=[turn_1])
    ctx004_viols = [v for v in detected if v.rule_id == "CTX-004"]
    assert len(ctx004_viols) == 1
    assert turn_2.violations == detected
    assert "Shrink context" in ctx004_viols[0].suggested_fix
    assert "/clear" in ctx004_viols[0].suggested_fix

