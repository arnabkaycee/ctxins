"""Integration and unit tests for context lifecycle and categorized breakdown UI."""

import time

from starlette.testclient import TestClient

from src.core.graph.diff import TurnDiffEngine
from src.core.store.session_store import SessionStore
from src.presentation.tui.state import TUIState
from src.presentation.tui.widgets.context_breakdown import ContextBreakdownWidget
from src.presentation.web.server import create_app
from src.presentation.web.turn_serializer import (
    annotate_blocks_lifecycle,
    calculate_category_breakdown,
)
from src.schema.ast import BlockType, CanonicalTurn, ContextBlock


def _build_fixture_turns() -> tuple[CanonicalTurn, CanonicalTurn]:
    b_sys = ContextBlock(
        block_id="sys_1",
        block_type=BlockType.SYSTEM,
        content_hash="hash_sys",
        token_count=100,
        content="System base prompt",
        identity_key="system:base",
    )
    b_skill = ContextBlock(
        block_id="skill_1",
        block_type=BlockType.SKILL,
        content_hash="hash_skill_v1",
        token_count=150,
        content="Git workflow guide",
        identity_key="skill:git",
    )
    b_tool = ContextBlock(
        block_id="tool_1",
        block_type=BlockType.TOOL_DEF,
        content_hash="hash_tool",
        token_count=80,
        content='{"name": "view_file"}',
        identity_key="tool_def:view_file",
    )
    b_res = ContextBlock(
        block_id="res_1",
        block_type=BlockType.TOOL_RESULT,
        content_hash="hash_res",
        token_count=350,
        content="File contents for turn 0",
        identity_key="tool_result:call_1",
    )

    turn_0 = CanonicalTurn(
        turn_id="turn_0",
        correlation_id="corr_0",
        session_id="sess_lifecycle",
        turn_index=0,
        timestamp=time.time(),
        provider="anthropic",
        model="claude-3-5-sonnet",
        system_blocks=[b_sys, b_skill],
        tool_defs=[b_tool],
        tool_results=[b_res],
        input_tokens=680,
        output_tokens=50,
    )

    # Turn 1: b_skill mutated, b_res evicted, b_thought added
    b_skill_mutated = ContextBlock(
        block_id="skill_1_mut",
        block_type=BlockType.SKILL,
        content_hash="hash_skill_v2",
        token_count=160,
        content="Git workflow guide updated",
        identity_key="skill:git",
    )
    b_thought = ContextBlock(
        block_id="th_1",
        block_type=BlockType.THOUGHT,
        content_hash="hash_th",
        token_count=40,
        content="Reflecting on edits",
        identity_key="thought:1",
    )

    turn_1 = CanonicalTurn(
        turn_id="turn_1",
        correlation_id="corr_1",
        session_id="sess_lifecycle",
        turn_index=1,
        timestamp=time.time() + 10,
        provider="anthropic",
        model="claude-3-5-sonnet",
        system_blocks=[b_sys, b_skill_mutated],
        tool_defs=[b_tool],
        assistant_blocks=[b_thought],
        input_tokens=340,
        output_tokens=40,
    )

    return turn_0, turn_1


def test_calculate_category_breakdown():
    turn_0, turn_1 = _build_fixture_turns()
    bd_0 = calculate_category_breakdown(turn_0)
    assert bd_0["system"] == 100
    assert bd_0["skills"] == 150
    assert bd_0["tools"] == 80
    assert bd_0["tool_results"] == 350
    assert bd_0["thoughts"] == 0

    bd_1 = calculate_category_breakdown(turn_1)
    assert bd_1["system"] == 100
    assert bd_1["skills"] == 160
    assert bd_1["tools"] == 80
    assert bd_1["tool_results"] == 0
    assert bd_1["thoughts"] == 40


def test_annotate_blocks_with_lifecycle():
    turn_0, turn_1 = _build_fixture_turns()
    annotated = annotate_blocks_lifecycle(turn_1, prev_turn=turn_0)
    statuses = {b["block_id"]: b["lifecycle_status"] for b in annotated}

    # sys_1 and tool_1 persisted
    assert statuses["sys_1"] == "persisted"
    assert statuses["tool_1"] == "persisted"
    # skill_1 mutated
    assert statuses["skill_1_mut"] == "mutated"
    # th_1 added
    assert statuses["th_1"] == "added"
    # res_1 evicted
    assert statuses["res_1"] == "evicted"


def test_tui_context_breakdown_toggle():
    turn_0, turn_1 = _build_fixture_turns()
    delta = TurnDiffEngine.compute_delta(turn_0, turn_1)

    state = TUIState()
    state.turns = [turn_0.to_dict(), turn_1.to_dict()]
    state.deltas = [delta.to_dict()]
    state.selected_turn_index = 1

    widget = ContextBreakdownWidget(state)
    assert widget.view_mode == "composition"
    widget.action_toggle_diff()
    assert widget.view_mode == "diff"
    widget.action_toggle_diff()
    assert widget.view_mode == "composition"


def test_web_api_lifecycle_endpoints():
    store = SessionStore()
    turn_0, turn_1 = _build_fixture_turns()

    store.append_turn(turn_0)
    store.append_turn(turn_1)

    app = create_app(store)
    client = TestClient(app)

    # 1. Get turns list with category_breakdown and delta
    resp = client.get("/api/sessions/sess_lifecycle/turns")
    assert resp.status_code == 200
    turns = resp.json()
    assert len(turns) == 2
    assert "category_breakdown" in turns[1]
    assert turns[1]["category_breakdown"]["skills"] == 160
    assert "delta" in turns[1]
    assert turns[1]["delta"]["mutated_block_ids"] == ["skill_1_mut"]

    # 2. Get annotated blocks
    resp_blocks = client.get("/api/sessions/sess_lifecycle/turns/1/blocks")
    assert resp_blocks.status_code == 200
    blocks = resp_blocks.json()
    assert isinstance(blocks, list)
    mutated = [b for b in blocks if b["lifecycle_status"] == "mutated"]
    assert len(mutated) == 1
    assert mutated[0]["block_id"] == "skill_1_mut"
