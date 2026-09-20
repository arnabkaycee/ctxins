"""Unit tests for TurnDiffEngine 4-state lifecycle transitions and cache breakpoint detection."""

from src.core.graph.diff import TurnDiffEngine
from src.core.graph.hasher import compute_block_hash
from src.core.graph.turn_tree import ContextGraph
from src.schema.ast import BlockType, CanonicalTurn, ContextBlock


def _make_block(
    block_id: str,
    block_type: BlockType,
    content: str,
    identity_key: str = "",
    token_count: int = 10,
) -> ContextBlock:
    return ContextBlock(
        block_id=block_id,
        block_type=block_type,
        content_hash=compute_block_hash(content),
        token_count=token_count,
        content=content,
        identity_key=identity_key,
    )


def _make_turn(
    turn_index: int,
    blocks: list[ContextBlock],
    session_id: str = "sess-01",
    input_tokens: int = 0,
) -> CanonicalTurn:
    sys_blocks = [b for b in blocks if b.block_type == BlockType.SYSTEM]
    tool_defs = [b for b in blocks if b.block_type == BlockType.TOOL_DEF]
    history = [b for b in blocks if b.block_type in (BlockType.USER_MSG, BlockType.ASSISTANT_MSG)]
    tool_results = [b for b in blocks if b.block_type == BlockType.TOOL_RESULT]

    return CanonicalTurn(
        turn_id=f"turn-{turn_index}",
        correlation_id=f"corr-{turn_index}",
        session_id=session_id,
        turn_index=turn_index,
        timestamp=1710000000.0 + turn_index * 10,
        provider="anthropic",
        model="claude-3-5-sonnet",
        system_blocks=sys_blocks,
        tool_defs=tool_defs,
        conversation_history=history,
        tool_results=tool_results,
        assistant_blocks=[],
        input_tokens=input_tokens or sum(b.token_count for b in blocks),
    )


def test_four_state_transitions():
    """Verify added, persisted, mutated, and removed transitions between turns."""
    sys_0 = _make_block("sys-1", BlockType.SYSTEM, "System prompt v1", identity_key="sys:prompt")
    tool_a_0 = _make_block(
        "tool-a", BlockType.TOOL_DEF, "def get_weather(): pass", identity_key="tool:weather"
    )
    tool_b_0 = _make_block(
        "tool-b", BlockType.TOOL_DEF, "def calculate(): pass", identity_key="tool:calc"
    )
    u1_0 = _make_block("u-1", BlockType.USER_MSG, "What is the weather?", identity_key="")

    turn_0 = _make_turn(0, [sys_0, tool_a_0, tool_b_0, u1_0])

    # Turn 1:
    # - sys-1: identical content and identity -> PERSISTED
    # - tool-a: same identity_key, modified content -> MUTATED
    # - tool-b: absent -> REMOVED
    # - tool-c: new identity_key -> ADDED
    # - u-1: same content_hash -> PERSISTED
    # - a-1: new message -> ADDED
    sys_1 = _make_block("sys-1", BlockType.SYSTEM, "System prompt v1", identity_key="sys:prompt")
    tool_a_1 = _make_block(
        "tool-a-v2",
        BlockType.TOOL_DEF,
        "def get_weather(loc, units): pass",
        identity_key="tool:weather",
    )
    tool_c_1 = _make_block(
        "tool-c", BlockType.TOOL_DEF, "def search(): pass", identity_key="tool:search"
    )
    u1_1 = _make_block("u-1", BlockType.USER_MSG, "What is the weather?", identity_key="")
    a1_1 = _make_block("a-1", BlockType.ASSISTANT_MSG, "I can help with that.", identity_key="")

    turn_1 = _make_turn(1, [sys_1, tool_a_1, tool_c_1, u1_1, a1_1])

    delta = TurnDiffEngine.compute_delta(turn_0, turn_1)

    assert delta.turn_index == 1
    assert set(delta.persisted_block_ids) == {"sys-1", "u-1"}
    assert set(delta.mutated_block_ids) == {"tool-a-v2"}
    assert set(delta.added_block_ids) == {"tool-c", "a-1"}
    assert set(delta.removed_block_ids) == {"tool-b"}

    # Mutated block must NOT be in added or removed
    assert "tool-a-v2" not in delta.added_block_ids
    assert "tool-a" not in delta.removed_block_ids


def test_mutating_content_with_identical_identity_key_lands_in_mutated():
    """Mutating content with identical identity_key must land in mutated_block_ids, not added+removed."""
    b0 = _make_block("b0", BlockType.TOOL_DEF, "content v1", identity_key="tool:execute")
    b1 = _make_block("b1", BlockType.TOOL_DEF, "content v2 mutated", identity_key="tool:execute")

    turn_0 = _make_turn(0, [b0])
    turn_1 = _make_turn(1, [b1])

    delta = TurnDiffEngine.compute_delta(turn_0, turn_1)

    assert delta.mutated_block_ids == ["b1"]
    assert delta.added_block_ids == []
    assert delta.removed_block_ids == []
    assert delta.persisted_block_ids == []


def test_cache_breakpoint_prefix_mutation():
    """Prefix mutation breaks cache at the mutated block."""
    sys_0 = _make_block("sys-1", BlockType.SYSTEM, "System prompt v1", identity_key="sys:prompt")
    u1_0 = _make_block("u-1", BlockType.USER_MSG, "Hello")

    sys_1 = _make_block(
        "sys-1-mutated", BlockType.SYSTEM, "System prompt v2 mutated", identity_key="sys:prompt"
    )
    u1_1 = _make_block("u-1", BlockType.USER_MSG, "Hello")

    turn_0 = _make_turn(0, [sys_0, u1_0])
    turn_1 = _make_turn(1, [sys_1, u1_1])

    delta = TurnDiffEngine.compute_delta(turn_0, turn_1)

    assert delta.mutated_block_ids == ["sys-1-mutated"]
    assert delta.persisted_block_ids == ["u-1"]
    # In prefix order, sys-1-mutated is first and is not in persisted_block_ids
    assert delta.cache_breakpoint_block_id == "sys-1-mutated"


def test_cache_breakpoint_tail_addition():
    """Tail addition breaks cache at the newly appended block."""
    sys_0 = _make_block("sys-1", BlockType.SYSTEM, "System prompt")
    u1_0 = _make_block("u-1", BlockType.USER_MSG, "Hello")

    sys_1 = _make_block("sys-1", BlockType.SYSTEM, "System prompt")
    u1_1 = _make_block("u-1", BlockType.USER_MSG, "Hello")
    u2_1 = _make_block("u-2", BlockType.USER_MSG, "How are you?")

    turn_0 = _make_turn(0, [sys_0, u1_0])
    turn_1 = _make_turn(1, [sys_1, u1_1, u2_1])

    delta = TurnDiffEngine.compute_delta(turn_0, turn_1)

    assert delta.persisted_block_ids == ["sys-1", "u-1"]
    assert delta.added_block_ids == ["u-2"]
    # In prefix order, first non-persisted block is u-2
    assert delta.cache_breakpoint_block_id == "u-2"


def test_cache_breakpoint_all_cached():
    """When all blocks are persisted from turn_prev, cache_breakpoint_block_id is None."""
    sys_0 = _make_block("sys-1", BlockType.SYSTEM, "System prompt")
    u1_0 = _make_block("u-1", BlockType.USER_MSG, "Hello")

    sys_1 = _make_block("sys-1", BlockType.SYSTEM, "System prompt")
    u1_1 = _make_block("u-1", BlockType.USER_MSG, "Hello")

    turn_0 = _make_turn(0, [sys_0, u1_0])
    turn_1 = _make_turn(1, [sys_1, u1_1])

    delta = TurnDiffEngine.compute_delta(turn_0, turn_1)

    assert delta.persisted_block_ids == ["sys-1", "u-1"]
    assert delta.added_block_ids == []
    assert delta.mutated_block_ids == []
    assert delta.removed_block_ids == []
    assert delta.cache_breakpoint_block_id is None


def test_cache_breakpoint_initial_turn():
    """Initial turn (turn_prev is None) has no cache breakpoint."""
    sys_0 = _make_block("sys-1", BlockType.SYSTEM, "System prompt")
    u1_0 = _make_block("u-1", BlockType.USER_MSG, "Hello")

    turn_0 = _make_turn(0, [sys_0, u1_0])

    delta = TurnDiffEngine.compute_delta(None, turn_0)

    assert delta.turn_index == 0
    assert delta.added_block_ids == ["sys-1", "u-1"]
    assert delta.persisted_block_ids == []
    assert delta.mutated_block_ids == []
    assert delta.removed_block_ids == []
    assert delta.cache_breakpoint_block_id is None


def test_context_graph_mutation_survival_lifecycle():
    """ContextGraph maintains survival and lineage across turns with mutations."""
    graph = ContextGraph(session_id="sess-lifecycle")

    # Turn 0: initial definition
    tool_t0 = _make_block(
        "tool-v1", BlockType.TOOL_DEF, "def run(): pass", identity_key="tool:runner"
    )
    turn_0 = _make_turn(0, [tool_t0])
    graph.add_turn(turn_0)

    assert tool_t0.first_seen_turn == 0
    assert tool_t0.turns_survived == 0

    # Turn 1: mutated definition
    tool_t1 = _make_block(
        "tool-v2", BlockType.TOOL_DEF, "def run(timeout=30): pass", identity_key="tool:runner"
    )
    turn_1 = _make_turn(1, [tool_t1])
    delta_1 = graph.add_turn(turn_1)

    assert delta_1.mutated_block_ids == ["tool-v2"]
    assert delta_1.added_block_ids == []
    assert delta_1.removed_block_ids == []
    # Retains first_seen_turn=0 across mutation, but survival streak resets to 0
    assert tool_t1.first_seen_turn == 0
    assert tool_t1.turns_survived == 0

    # Turn 2: persisted identical definition
    tool_t2 = _make_block(
        "tool-v3", BlockType.TOOL_DEF, "def run(timeout=30): pass", identity_key="tool:runner"
    )
    turn_2 = _make_turn(2, [tool_t2])
    delta_2 = graph.add_turn(turn_2)

    assert delta_2.persisted_block_ids == ["tool-v3"]
    assert delta_2.mutated_block_ids == []
    # Increments turns_survived to 1
    assert tool_t2.first_seen_turn == 0
    assert tool_t2.turns_survived == 1

    # Turn 3: persisted again
    tool_t3 = _make_block(
        "tool-v4", BlockType.TOOL_DEF, "def run(timeout=30): pass", identity_key="tool:runner"
    )
    turn_3 = _make_turn(3, [tool_t3])
    delta_3 = graph.add_turn(turn_3)

    assert delta_3.persisted_block_ids == ["tool-v4"]
    assert tool_t3.first_seen_turn == 0
    assert tool_t3.turns_survived == 2

    # Historical lineage lookup
    hash_v1 = compute_block_hash("def run(): pass")
    hash_v2 = compute_block_hash("def run(timeout=30): pass")

    lineage_v1 = graph.get_lineage(hash_v1, BlockType.TOOL_DEF)
    assert lineage_v1 is not None
    assert lineage_v1.identity_key == "tool:runner"
    assert lineage_v1.first_seen_turn == 0
    assert lineage_v1.turns_survived == 2

    lineage_v2 = graph.get_lineage(hash_v2, BlockType.TOOL_DEF)
    assert lineage_v2 is not None
    assert lineage_v2 == lineage_v1

    lineage_by_id = graph.get_lineage(identity_key="tool:runner")
    assert lineage_by_id is not None
    assert lineage_by_id == lineage_v1
