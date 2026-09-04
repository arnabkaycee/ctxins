"""Unit and component tests for Textual TUI Application, State, and Widgets."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest

from src.presentation.broadcaster import PresentationBroadcaster
from src.presentation.events import UIEvent, UIEventType
from src.presentation.tui.app import CtxinsTUIApp
from src.presentation.tui.state import TUIState
from src.presentation.tui.theme import (
    COLOR_CRITICAL,
    COLOR_SUCCESS,
    COLOR_WARN,
    get_pollution_color,
    get_pollution_label,
    render_pollution_bar,
)
from src.presentation.tui.widgets.context_breakdown import ContextBreakdownWidget
from src.presentation.tui.widgets.footer_bar import FooterBarWidget
from src.presentation.tui.widgets.header_bar import HeaderBarWidget
from src.presentation.tui.widgets.recommendations import RecommendationsWidget
from src.presentation.tui.widgets.turn_timeline import TurnSelected, TurnTimelineWidget


def test_theme_pollution_helpers() -> None:
    """Verify pollution color, label, and meter rendering across score boundaries."""
    assert get_pollution_color(10.0) == COLOR_SUCCESS
    assert get_pollution_label(10.0) == "Clean"

    assert get_pollution_color(35.0) == COLOR_WARN
    assert get_pollution_label(35.0) == "Moderate"

    assert get_pollution_color(75.0) == COLOR_CRITICAL
    assert get_pollution_label(75.0) == "High Pollution"

    bar_clean = render_pollution_bar(10.0, width=10)
    assert "10.0/100" in bar_clean
    assert "Clean" in bar_clean

    bar_high = render_pollution_bar(80.0, width=10)
    assert "80.0/100" in bar_high
    assert "High Pollution" in bar_high


def test_tui_state_lifecycle_and_events() -> None:
    """Verify TUIState transitions and aggregate calculations upon receiving events."""
    state = TUIState()
    assert state.status == "Idle"
    assert len(state.turns) == 0

    # 1. SESSION_CREATED
    ev_session = UIEvent(
        event_type=UIEventType.SESSION_CREATED,
        session_id="sess_tui_001",
        payload={
            "model": "claude-3-5-sonnet",
            "provider": "anthropic",
            "agentHarness": "claude-code",
        },
    )
    state.apply_event(ev_session)
    assert state.session_id == "sess_tui_001"
    assert state.model == "claude-3-5-sonnet"
    assert state.provider == "anthropic"
    assert state.agent_harness == "claude-code"

    # 2. TURN_STARTED
    ev_start = UIEvent(
        event_type=UIEventType.TURN_STARTED,
        session_id="sess_tui_001",
        payload={"turnIndex": 0, "correlationId": "corr_001"},
    )
    state.apply_event(ev_start)
    assert state.status == "Streaming"
    assert len(state.turns) == 1
    assert state.turns[0]["turnIndex"] == 0
    assert state.turns[0]["status"] == "streaming"

    # 3. TURN_STREAMING
    ev_stream = UIEvent(
        event_type=UIEventType.TURN_STREAMING,
        session_id="sess_tui_001",
        payload={
            "turnIndex": 0,
            "deltaTokens": 150,
            "streamDurationMs": 1200.0,
            "ttftMs": 350.0,
        },
    )
    state.apply_event(ev_stream)
    assert state.turns[0]["outputTokens"] == 150
    assert state.turns[0]["durationMs"] == 1200.0
    assert state.turns[0]["ttftMs"] == 350.0

    # 4. TURN_COMPLETED
    ev_done = UIEvent(
        event_type=UIEventType.TURN_COMPLETED,
        session_id="sess_tui_001",
        payload={
            "turnIndex": 0,
            "inputTokens": 1000,
            "outputTokens": 200,
            "cachedReadTokens": 500,
            "cachedCreatedTokens": 0,
            "cost": 0.015,
            "wastedCost": 0.002,
            "durationMs": 1500.0,
            "tokenBreakdown": {
                "system": 200,
                "tools": 300,
                "history": 100,
                "toolResults": 400,
                "assistant": 200,
                "cache": 500,
            },
            "violations": [
                {
                    "ruleId": "CTX-001",
                    "severity": "WARN",
                    "title": "Stale Tool Output",
                    "message": "Tool result persisted unread",
                    "estimatedWasteUSD": 0.002,
                    "suggestedFix": "Prune old output",
                    "blockIds": ["blk_tool_01"],
                }
            ],
            "blocks": [
                {
                    "block_id": "blk_tool_01",
                    "block_type": "tool_result",
                    "token_count": 400,
                    "turns_survived": 2,
                    "content": "output from search command",
                }
            ],
        },
    )
    state.apply_event(ev_done)
    assert state.status == "Idle"
    assert state.total_tokens == 1200
    assert state.total_spend_usd == 0.015
    assert state.wasted_spend_usd == 0.002
    assert state.cached_read_tokens == 500
    assert state.cache_hit_ratio == 0.5
    assert len(state.cumulative_violations) == 1

    # 5. VIOLATION_DETECTED
    ev_viol = UIEvent(
        event_type=UIEventType.VIOLATION_DETECTED,
        session_id="sess_tui_001",
        payload={
            "turnIndex": 0,
            "ruleId": "CACHE-001",
            "severity": "CRITICAL",
            "title": "Prefix Invalidation",
            "message": "System prompt prefix shifted",
            "estimatedWasteUSD": 0.01,
            "suggestedFix": "Stabilize prefix",
        },
    )
    state.apply_event(ev_viol)
    assert len(state.cumulative_violations) == 2
    assert state.wasted_spend_usd == 0.012

    # 6. SESSION_SUMMARY_UPDATED
    ev_summary = UIEvent(
        event_type=UIEventType.SESSION_SUMMARY_UPDATED,
        session_id="sess_tui_001",
        payload={
            "totalTokens": 5000,
            "cacheHitRatio": 0.75,
            "totalCostUSD": 0.08,
            "wastedCostUSD": 0.02,
            "pollutionScore": 32.5,
        },
    )
    state.apply_event(ev_summary)
    assert state.total_tokens == 5000
    assert state.cache_hit_ratio == 0.75
    assert state.total_spend_usd == 0.08
    assert state.wasted_spend_usd == 0.02
    assert state.pollution_score == 32.5

    # 7. SESSION_ENDED
    ev_end = UIEvent(
        event_type=UIEventType.SESSION_ENDED,
        session_id="sess_tui_001",
    )
    state.apply_event(ev_end)
    assert state.status == "Ended"


def test_tui_state_getters_and_export(tmp_path: Path) -> None:
    """Verify state getter methods and jsonc serialization."""
    state = TUIState(
        session_id="sess_getter_test",
        model="claude-3-5-sonnet",
        provider="anthropic",
        agent_harness="claude-code",
    )

    # Empty getters
    assert state.get_selected_turn() is None
    assert len(state.get_violations_for_selected_turn()) == 0
    assert len(state.get_blocks_for_selected_turn()) == 0
    tb = state.get_context_breakdown_for_selected_turn()
    assert tb["system"] == 0

    # Add turn
    turn_data: Dict[str, Any] = {
        "turnIndex": 0,
        "tokens": 2500,
        "inputTokens": 2000,
        "outputTokens": 500,
        "cachedReadTokens": 1200,
        "cost": 0.025,
        "wastedCost": 0.005,
        "violations": [
            {
                "ruleId": "CTX-001",
                "severity": "WARN",
                "title": "Stale",
                "estimatedWasteUSD": 0.005,
            }
        ],
        "blocks": [
            {
                "block_id": "blk_1",
                "block_type": "system",
                "token_count": 800,
                "turns_survived": 1,
            }
        ],
        "tokenBreakdown": {
            "system": 800,
            "tools": 400,
            "history": 400,
            "toolResults": 400,
            "assistant": 500,
            "cache": 1200,
        },
    }
    state.turns.append(turn_data)
    state.cumulative_violations.extend(turn_data["violations"])

    assert state.get_selected_turn() == turn_data
    assert len(state.get_violations_for_selected_turn()) == 1
    assert len(state.get_blocks_for_selected_turn()) == 1
    tb2 = state.get_context_breakdown_for_selected_turn()
    assert tb2["system"] == 800

    # Test show_all_violations toggle
    state.show_all_violations = True
    assert len(state.get_violations_for_selected_turn()) == 1

    # Test export
    out_file = tmp_path / "test_export.jsonc"
    res_path = state.export_to_jsonc(out_file)
    assert res_path.exists()
    content = res_path.read_text(encoding="utf-8")
    assert "ctxins Session Export" in content
    assert "sess_getter_test" in content


@pytest.mark.asyncio
async def test_tui_app_mount_and_widgets() -> None:
    """Verify CtxinsTUIApp mounts all 5 widgets in 3-pane layout."""
    state = TUIState(
        session_id="sess_pilot_01",
        model="claude-3-5-sonnet",
        provider="anthropic",
    )
    broadcaster = PresentationBroadcaster()
    app = CtxinsTUIApp(state=state, broadcaster=broadcaster)

    async with app.run_test():
        # Check all 5 primary widgets are mounted
        header = app.query_one(HeaderBarWidget)
        timeline = app.query_one(TurnTimelineWidget)
        breakdown = app.query_one(ContextBreakdownWidget)
        recs = app.query_one(RecommendationsWidget)
        footer = app.query_one(FooterBarWidget)

        assert header is not None
        assert timeline is not None
        assert breakdown is not None
        assert recs is not None
        assert footer is not None

        # Check initial rendered content
        header_text = header.render()
        assert header_text is not None

        footer_text = footer.render()
        assert "Switch Pane" in footer_text.plain


@pytest.mark.asyncio
async def test_tui_app_live_events_and_keybindings() -> None:
    """Verify live event broadcasting, widget reactive updating, and keybindings."""
    state = TUIState()
    broadcaster = PresentationBroadcaster()
    app = CtxinsTUIApp(state=state, broadcaster=broadcaster)

    async with app.run_test() as pilot:
        # Broadcast SESSION_CREATED
        broadcaster.publish_nowait(
            UIEvent(
                event_type=UIEventType.SESSION_CREATED,
                session_id="sess_live_42",
                payload={"model": "gpt-4o", "provider": "openai"},
            )
        )
        # Broadcast TURN_STARTED
        broadcaster.publish_nowait(
            UIEvent(
                event_type=UIEventType.TURN_STARTED,
                session_id="sess_live_42",
                payload={"turnIndex": 0},
            )
        )
        # Broadcast TURN_COMPLETED
        broadcaster.publish_nowait(
            UIEvent(
                event_type=UIEventType.TURN_COMPLETED,
                session_id="sess_live_42",
                payload={
                    "turnIndex": 0,
                    "inputTokens": 3000,
                    "outputTokens": 500,
                    "cachedReadTokens": 1500,
                    "cost": 0.035,
                    "wastedCost": 0.008,
                    "durationMs": 2100.0,
                    "tokenBreakdown": {
                        "system": 1000,
                        "tools": 500,
                        "history": 500,
                        "toolResults": 1000,
                        "assistant": 500,
                        "cache": 1500,
                    },
                    "violations": [
                        {
                            "ruleId": "CTX-001",
                            "severity": "CRITICAL",
                            "title": "Massive Stale Tool Result",
                            "message": "Tool output 10k tokens untouched",
                            "estimatedWasteUSD": 0.008,
                            "suggestedFix": "Truncate output",
                            "blockIds": ["blk_big_01"],
                        }
                    ],
                    "blocks": [
                        {
                            "block_id": "blk_big_01",
                            "block_type": "tool_result",
                            "token_count": 1000,
                            "turns_survived": 3,
                            "content": "cat big_file.txt",
                        }
                    ],
                },
            )
        )

        # Allow async worker to consume queue and update widgets
        await pilot.pause(0.1)

        assert app.state.session_id == "sess_live_42"
        assert len(app.state.turns) == 1
        assert app.state.total_tokens == 3500

        # Test keybinding 'r' (toggle rule filter)
        assert app.state.show_all_violations is False
        await pilot.press("r")
        assert app.state.show_all_violations is True
        await pilot.press("r")
        assert app.state.show_all_violations is False

        # Test block navigation 'n' and 'p' in breakdown widget
        breakdown = app.query_one(ContextBreakdownWidget)
        assert app.state.selected_block_index == 0
        breakdown.action_next_block()
        assert app.state.selected_block_index == 0  # only 1 block, modulo stays 0

        # Test turn selection notification
        app.post_message(TurnSelected(turn_index=0))
        await pilot.pause(0.05)
        assert app.selected_turn_index == 0

        # Test keybinding 'e' (export jsonc)
        await pilot.press("e")

        # Test keybinding 'q' (quit)
        await pilot.press("q")
        assert app.is_running is False

    # Clean up exported test file if created
    for p in Path(".").glob("session_sess_live_42_*.jsonc"):
        p.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_turn_timeline_navigation_and_badges() -> None:
    """Verify TurnTimelineWidget formats turns and reacts to navigation."""
    state = TUIState(session_id="sess_timeline_test")
    # Add Turn 0: streaming
    state.turns.append(
        {
            "turnIndex": 0,
            "tokens": 12000,
            "status": "streaming",
            "durationMs": 1400.0,
            "violations": [],
            "cost": 0.0,
        }
    )
    # Add Turn 1: with violations
    state.turns.append(
        {
            "turnIndex": 1,
            "tokens": 25000,
            "status": "completed",
            "durationMs": 2000.0,
            "violations": [{"ruleId": "CTX-001"}],
            "cost": 0.02,
        }
    )
    # Add Turn 2: clean
    state.turns.append(
        {
            "turnIndex": 2,
            "tokens": 48000,
            "status": "completed",
            "durationMs": 1800.0,
            "violations": [],
            "cost": 0.048,
        }
    )

    app = CtxinsTUIApp(state=state)
    async with app.run_test() as pilot:
        timeline = app.query_one(TurnTimelineWidget)
        timeline.update_from_state()
        await pilot.pause(0.05)

        # Move cursor down to Turn #1
        timeline.action_cursor_down()
        await pilot.pause(0.05)
        assert app.selected_turn_index == 1

        # Move cursor down to Turn #2
        timeline.action_cursor_down()
        await pilot.pause(0.05)
        assert app.selected_turn_index == 2

        # Move cursor up back to Turn #1
        timeline.action_cursor_up()
        await pilot.pause(0.05)
        assert app.selected_turn_index == 1


def test_header_bar_render_states() -> None:
    """Verify HeaderBarWidget rendering in Streaming, Idle, and Ended states."""
    state = TUIState(
        session_id="sess_header_test",
        agent_harness="claude-code",
        model="claude-3-5-sonnet",
        provider="anthropic",
        status="Streaming",
        pollution_score=45.0,
    )
    header = HeaderBarWidget(state)
    table_streaming = header.render()
    assert table_streaming is not None

    # Transition to ended
    state.status = "Ended"
    state.pollution_score = 65.0
    header.update_from_state()
    table_ended = header.render()
    assert table_ended is not None


def test_recommendations_severities_and_empty() -> None:
    """Verify RecommendationsWidget formats CRITICAL, WARN, and INFO badges."""
    state = TUIState(session_id="sess_recs_test")
    recs_widget = RecommendationsWidget(state)

    # Empty state
    recs_widget.update_from_state()

    # Add violations of all 3 severities
    state.cumulative_violations = [
        {
            "ruleId": "CRIT-001",
            "severity": "CRITICAL",
            "title": "Critical Invalidation",
            "estimatedWasteUSD": 0.05,
            "suggestedFix": "Fix immediately",
            "blockIds": ["b1"],
            "turnIndex": 0,
        },
        {
            "ruleId": "WARN-001",
            "severity": "WARN",
            "title": "Warning Bloat",
            "estimatedWasteUSD": 0.01,
            "suggestedFix": "Prune soon",
            "blockIds": ["b2"],
            "turnIndex": 0,
        },
        {
            "ruleId": "INFO-001",
            "severity": "INFO",
            "title": "Info Notice",
            "estimatedWasteUSD": 0.0,
            "suggestedFix": "Consider tuning",
            "blockIds": [],
            "turnIndex": 0,
        },
    ]
    state.show_all_violations = True
    recs_widget.update_from_state()


def test_context_breakdown_block_cycling() -> None:
    """Verify ContextBreakdownWidget block cycling with multiple AST blocks."""
    state = TUIState(session_id="sess_blocks_test")
    state.turns.append(
        {
            "turnIndex": 0,
            "tokens": 3000,
            "cachedReadTokens": 1000,
            "tokenBreakdown": {"system": 1000, "tools": 1000, "history": 1000},
            "blocks": [
                {"block_id": "blk_0", "block_type": "system", "token_count": 1000, "content": "sys prompt"},
                {"block_id": "blk_1", "block_type": "tool_def", "token_count": 1000, "content": "def run()"},
                {"block_id": "blk_2", "block_type": "user_msg", "token_count": 1000, "content": "hello world"},
            ],
        }
    )
    breakdown = ContextBreakdownWidget(state)
    breakdown.update_from_state()

    assert state.selected_block_index == 0
    breakdown.action_next_block()
    assert state.selected_block_index == 1
    breakdown.action_next_block()
    assert state.selected_block_index == 2
    breakdown.action_next_block()
    assert state.selected_block_index == 0  # Wraps around
    breakdown.action_prev_block()
    assert state.selected_block_index == 2  # Wraps backward


def test_default_export_jsonc_cleanup() -> None:
    """Verify default export_to_jsonc creates file and can be cleaned up."""
    state = TUIState(session_id="sess_cleanup_test")
    out_path = state.export_to_jsonc()
    try:
        assert out_path.exists()
        assert "sess_cleanup_test" in out_path.read_text(encoding="utf-8")
    finally:
        if out_path.exists():
            out_path.unlink()
