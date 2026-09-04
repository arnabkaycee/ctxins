"""Unit tests for ContextGraph lineage tracking and TurnDiffEngine delta computation."""

from src.core.graph.diff import TurnDiffEngine
from src.core.graph.hasher import compute_block_hash
from src.core.graph.turn_tree import ContextGraph
from src.schema.ast import BlockType, CanonicalTurn, ContextBlock


def _make_block(
    block_id: str,
    block_type: BlockType,
    content: str,
    token_count: int = 10,
) -> ContextBlock:
    return ContextBlock(
        block_id=block_id,
        block_type=block_type,
        content_hash=compute_block_hash(content),
        token_count=token_count,
        content=content,
    )


def _make_turn(
    turn_index: int,
    blocks: list[ContextBlock],
    session_id: str = "sess-01",
    input_tokens: int = 0,
) -> CanonicalTurn:
    sys_blocks = [b for b in blocks if b.block_type == BlockType.SYSTEM]
    tool_defs = [b for b in blocks if b.block_type == BlockType.TOOL_DEF]
    history = [
        b for b in blocks if b.block_type in (BlockType.USER_MSG, BlockType.ASSISTANT_MSG)
    ]
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


# ---------------------------------------------------------------------------
# ContextGraph Lineage Tracking Tests
# ---------------------------------------------------------------------------


def test_context_graph_lineage_survival():
    graph = ContextGraph(session_id="sess-01")

    # Turn 0: System prompt + User msg 1
    blk_sys = _make_block("sys-1", BlockType.SYSTEM, "System instructions")
    blk_u1 = _make_block("u-1", BlockType.USER_MSG, "Hello world")

    turn_0 = _make_turn(0, [blk_sys, blk_u1], input_tokens=100)
    delta_0 = graph.add_turn(turn_0)

    # Turn 0 assertions
    assert blk_sys.first_seen_turn == 0
    assert blk_sys.turns_survived == 0
    assert blk_u1.first_seen_turn == 0
    assert blk_u1.turns_survived == 0
    assert set(delta_0.added_block_ids) == {"sys-1", "u-1"}
    assert delta_0.persisted_block_ids == []
    assert delta_0.removed_block_ids == []
    assert delta_0.token_growth == 100

    # Turn 1: System prompt + User msg 1 + Assistant msg 1 + User msg 2
    blk_sys_t1 = _make_block("sys-1", BlockType.SYSTEM, "System instructions")
    blk_u1_t1 = _make_block("u-1", BlockType.USER_MSG, "Hello world")
    blk_a1_t1 = _make_block("a-1", BlockType.ASSISTANT_MSG, "Hi there!")
    blk_u2_t1 = _make_block("u-2", BlockType.USER_MSG, "What is 2+2?")

    turn_1 = _make_turn(1, [blk_sys_t1, blk_u1_t1, blk_a1_t1, blk_u2_t1], input_tokens=160)
    delta_1 = graph.add_turn(turn_1)

    # Turn 1 assertions
    assert blk_sys_t1.first_seen_turn == 0
    assert blk_sys_t1.turns_survived == 1
    assert blk_u1_t1.first_seen_turn == 0
    assert blk_u1_t1.turns_survived == 1
    assert blk_a1_t1.first_seen_turn == 1
    assert blk_a1_t1.turns_survived == 0
    assert blk_u2_t1.first_seen_turn == 1
    assert blk_u2_t1.turns_survived == 0

    assert set(delta_1.persisted_block_ids) == {"sys-1", "u-1"}
    assert set(delta_1.added_block_ids) == {"a-1", "u-2"}
    assert delta_1.removed_block_ids == []
    assert delta_1.token_growth == 60

    # Turn 2: System prompt survived again, u-1 pruned/dropped, u-2 persisted
    blk_sys_t2 = _make_block("sys-1", BlockType.SYSTEM, "System instructions")
    blk_u2_t2 = _make_block("u-2", BlockType.USER_MSG, "What is 2+2?")
    blk_u3_t2 = _make_block("u-3", BlockType.USER_MSG, "Now what is 3+3?")

    turn_2 = _make_turn(2, [blk_sys_t2, blk_u2_t2, blk_u3_t2], input_tokens=140)
    delta_2 = graph.add_turn(turn_2)

    assert blk_sys_t2.first_seen_turn == 0
    assert blk_sys_t2.turns_survived == 2
    assert blk_u2_t2.first_seen_turn == 1
    assert blk_u2_t2.turns_survived == 1
    assert blk_u3_t2.first_seen_turn == 2
    assert blk_u3_t2.turns_survived == 0

    assert set(delta_2.persisted_block_ids) == {"sys-1", "u-2"}
    assert set(delta_2.added_block_ids) == {"u-3"}
    assert set(delta_2.removed_block_ids) == {"u-1", "a-1"}
    assert delta_2.token_growth == -20

    # Turn 3: System survived 3 turns, u-1 resurrected
    blk_sys_t3 = _make_block("sys-1", BlockType.SYSTEM, "System instructions")
    blk_u1_t3 = _make_block("u-1", BlockType.USER_MSG, "Hello world")  # re-added after being dropped

    turn_3 = _make_turn(3, [blk_sys_t3, blk_u1_t3], input_tokens=110)
    graph.add_turn(turn_3)

    assert blk_sys_t3.turns_survived == 3
    assert blk_sys_t3.first_seen_turn == 0
    # Resurrected block retains original first_seen_turn, but survival streak was broken
    assert blk_u1_t3.first_seen_turn == 0
    assert blk_u1_t3.turns_survived == 0

    # Test get_surviving_blocks
    surviving_2 = graph.get_surviving_blocks(min_turns=2)
    assert len(surviving_2) == 1
    assert surviving_2[0].block_id == "sys-1"

    # Lineage lookup
    lineage = graph.get_lineage(compute_block_hash("System instructions"), BlockType.SYSTEM)
    assert lineage is not None
    assert lineage.first_seen_turn == 0
    assert lineage.turns_survived == 3
    assert lineage.seen_turns == [0, 1, 2, 3]


def test_context_graph_dag_and_retrieval():
    graph = ContextGraph()

    t0 = _make_turn(0, [_make_block("b0", BlockType.USER_MSG, "Turn 0")])
    t1 = _make_turn(1, [_make_block("b1", BlockType.USER_MSG, "Turn 1")])
    t2 = _make_turn(2, [_make_block("b2", BlockType.USER_MSG, "Turn 2")])

    graph.add_turn(t0)
    graph.add_turn(t1)
    graph.add_turn(t2)

    assert len(graph) == 3
    assert graph.turn_count == 3
    assert graph.get_turn(1) == t1
    assert graph.get_turn_by_id("turn-2") == t2
    assert graph.get_delta(1) is not None

    # DAG relationships
    assert graph.get_parent_turn("turn-1") == t0
    assert graph.get_parent_turn("turn-2") == t1
    assert graph.get_child_turns("turn-0") == [t1]
    assert graph.get_child_turns("turn-1") == [t2]


# ---------------------------------------------------------------------------
# TurnDiffEngine Standalone Tests
# ---------------------------------------------------------------------------


def test_turn_diff_engine_standalone():
    b1 = _make_block("b1", BlockType.SYSTEM, "System", token_count=50)
    b2 = _make_block("b2", BlockType.USER_MSG, "Prompt 1", token_count=30)
    b3 = _make_block("b3", BlockType.USER_MSG, "Prompt 2", token_count=40)

    turn_prev = _make_turn(0, [b1, b2], input_tokens=80)
    turn_curr = _make_turn(1, [b1, b3], input_tokens=90)

    delta = TurnDiffEngine.compute_delta(turn_prev, turn_curr)
    assert delta.turn_index == 1
    assert delta.persisted_block_ids == ["b1"]
    assert delta.removed_block_ids == ["b2"]
    assert delta.added_block_ids == ["b3"]
    assert delta.token_growth == 10

    # Verify alias works
    delta_alias = TurnDiffEngine.diff(turn_prev, turn_curr)
    assert delta_alias == delta
