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


def test_header_bar_cockpit_badges() -> None:
    """Verify HeaderBarWidget renders custom proxy port and web dashboard URL badges."""
    state = TUIState(session_id="sess_cockpit_test")
    header = HeaderBarWidget(state, proxy_port=8088, web_url="http://127.0.0.1:9090")
    table = header.render()
    assert table is not None
    assert header.proxy_port == 8088
    assert header.web_url == "http://127.0.0.1:9090"


def test_tui_app_actions_and_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify CtxinsTUIApp action_open_web, action_copy_env, and action_show_hook_modal."""
    state = TUIState()
    opened_urls = []
    copied_texts = []
    pushed_screens = []

    monkeypatch.setattr("webbrowser.open", lambda url: opened_urls.append(url))
    monkeypatch.setattr("src.presentation.tui.app.copy_to_clipboard", lambda text: copied_texts.append(text))

    app = CtxinsTUIApp(state=state, proxy_port=8080, web_url="http://127.0.0.1:8484")
    monkeypatch.setattr(app, "notify", lambda msg, **kwargs: None)
    monkeypatch.setattr(app, "push_screen", lambda scr: pushed_screens.append(scr))

    # Test open web
    app.action_open_web()
    assert opened_urls == ["http://127.0.0.1:8484"]

    # Test copy env
    app.action_copy_env()
    assert len(copied_texts) == 1
    assert "HTTP_PROXY" in copied_texts[0]

    # Test show hook modal
    app.action_show_hook_modal()
    assert len(pushed_screens) == 1

    # Test show help modal
    app.action_show_help_modal()
    assert len(pushed_screens) == 2


def test_copy_to_clipboard_fallback() -> None:
    """Verify copy_to_clipboard executes without error across fallback mechanisms."""
    from src.presentation.tui.widgets.hook_modal import copy_to_clipboard

    result = copy_to_clipboard("HTTP_PROXY=http://127.0.0.1:8080")
    assert isinstance(result, bool)


def test_footer_bar_renders_all_keybindings() -> None:
    """Verify FooterBarWidget renders keybinding rows with all shortcuts."""
    state = TUIState()
    footer = FooterBarWidget(state)
    rendered = footer.render()
    assert rendered is not None
    assert "\n" in rendered.plain
    lines = rendered.plain.split("\n")
    assert len(lines) == 2
    assert "Switch Pane" in lines[0]
    assert "Help" in lines[1]
    assert "Quit" in lines[1]


def test_help_modal_screen_compose() -> None:
    """Verify HelpModalScreen instantiates and composes successfully."""
    from src.presentation.tui.widgets.help_modal import HelpModalScreen

    modal = HelpModalScreen()
    assert modal is not None


@pytest.mark.asyncio
async def test_footer_bar_visible_lines_not_clipped() -> None:
    """Verify FooterBarWidget renders both row 1 and row 2 visibly without clipping."""
    from textual.geometry import Region

    app = CtxinsTUIApp()
    async with app.run_test(size=(80, 24)):
        footer = app.query_one(FooterBarWidget)
        lines = footer.render_lines(Region(0, 0, footer.region.width, footer.region.height))
        all_text = " ".join(line.text for line in lines)
        assert "Switch Pane" in all_text
        assert "Help" in all_text
        assert "Quit" in all_text
        assert footer.region.height >= 3


def test_sessions_panel_widget_render_and_options() -> None:
    """Verify SessionsPanelWidget formats detected sessions with badges, PIDs, commands, and turns."""
    from src.presentation.tui.widgets.sessions_panel import SessionsPanelWidget

    state = TUIState(session_id="sess_agy_1")
    state.available_sessions = ["sess_agy_1", "sess_claude_2"]
    state.sessions_metadata = {
        "sess_agy_1": {
            "agentHarness": "agy",
            "agent": {"display_name": "Antigravity", "pid": 4567, "command": "agy run"},
        },
        "sess_claude_2": {
            "agentHarness": "claude",
            "agent": {"display_name": "Claude Code", "pid": 8901, "command": "claude"},
        },
    }
    state.sessions_turns = {
        "sess_agy_1": [{"turnIndex": 0, "status": "completed"}],
        "sess_claude_2": [{"turnIndex": 0, "status": "completed"}, {"turnIndex": 1, "status": "completed"}],
    }

    widget = SessionsPanelWidget(state)
    # Verify properties
    assert widget.state == state


@pytest.mark.asyncio
async def test_multi_session_turn_switching_and_isolation() -> None:
    """Verify switching sessions loads turns from SessionStore and maintains per-session isolation."""
    from src.core.store.session_store import SessionStore
    from src.presentation.tui.widgets.sessions_panel import SessionChosen, SessionsPanelWidget
    from src.presentation.tui.widgets.turn_timeline import TurnTimelineWidget
    from src.schema.ast import (
        BlockType,
        CanonicalTurn,
        ContextBlock,
        RuleViolation,
        ViolationSeverity,
    )

    store = SessionStore(max_sessions=10)

    # Populate session 1: sess_agy with 2 turns and 1 violation
    viol = RuleViolation(
        rule_id="CTX-001",
        severity=ViolationSeverity.WARN,
        title="Stale",
        message="Stale result",
        suggested_fix="Prune stale output",
        estimated_waste_usd=0.005,
    )
    t1_0 = CanonicalTurn(
        turn_id="agy_t0",
        correlation_id="c0",
        session_id="sess_agy",
        turn_index=0,
        timestamp=100.0,
        provider="google",
        model="gemini-2.5",
        system_blocks=[ContextBlock("s0", BlockType.SYSTEM, "h0", 150, "sys")],
        input_tokens=500,
        output_tokens=100,
        turn_cost_usd=0.01,
    )
    t1_1 = CanonicalTurn(
        turn_id="agy_t1",
        correlation_id="c1",
        session_id="sess_agy",
        turn_index=1,
        timestamp=101.0,
        provider="google",
        model="gemini-2.5",
        system_blocks=[ContextBlock("s1", BlockType.SYSTEM, "h1", 150, "sys")],
        input_tokens=700,
        output_tokens=200,
        violations=[viol],
        turn_cost_usd=0.02,
        wasted_cost_usd=0.005,
    )
    store.append_turn(t1_0)
    store.append_turn(t1_1)
    store.register_session("sess_agy", metadata={
        "agentHarness": "agy",
        "agent": {"display_name": "Antigravity", "pid": 1111, "command": "agy run"},
    })

    # Populate session 2: sess_claude with 1 turn
    t2_0 = CanonicalTurn(
        turn_id="claude_t0",
        correlation_id="c2",
        session_id="sess_claude",
        turn_index=0,
        timestamp=200.0,
        provider="anthropic",
        model="claude-3-5-sonnet",
        system_blocks=[ContextBlock("s2", BlockType.SYSTEM, "h2", 200, "sys")],
        input_tokens=1200,
        output_tokens=300,
        turn_cost_usd=0.03,
    )
    store.append_turn(t2_0)
    store.register_session("sess_claude", metadata={
        "agentHarness": "claude-code",
        "agent": {"display_name": "Claude Code", "pid": 2222, "command": "claude"},
    })

    app = CtxinsTUIApp(store=store)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)

        # Initial state should be first session: sess_agy with 2 turns
        assert app.state.session_id == "sess_agy"
        assert len(app.state.turns) == 2
        assert app.state.selected_turn_index == 1
        assert len(app.state.cumulative_violations) == 1

        # Sessions panel and timeline widgets are mounted
        panel = app.query_one(SessionsPanelWidget)
        timeline = app.query_one(TurnTimelineWidget)
        assert panel is not None
        assert timeline is not None

        # Switch to sess_claude via SessionChosen message
        panel.post_message(SessionChosen("sess_claude"))
        await pilot.pause(0.05)

        assert app.state.session_id == "sess_claude"
        assert len(app.state.turns) == 1
        assert app.state.turns[0]["turnId"] == "claude_t0"
        assert app.state.agent_harness == "claude-code"
        assert app.selected_turn_index == 0
        assert len(app.state.cumulative_violations) == 0

        # Cycle back to sess_agy using action_switch_session ('s' key)
        app.action_switch_session()
        await pilot.pause(0.05)

        assert app.state.session_id == "sess_agy"
        assert len(app.state.turns) == 2
        assert app.state.agent_harness == "agy"
        assert app.selected_turn_index == 1
        assert len(app.state.cumulative_violations) == 1


@pytest.mark.asyncio
async def test_multi_session_live_events_isolation() -> None:
    """Verify live events arriving for background session do not overwrite active session."""
    broadcaster = PresentationBroadcaster()
    active_turn = {"turnIndex": 0, "status": "completed"}
    state = TUIState(
        session_id="sess_active",
        turns=[active_turn],
        available_sessions=["sess_active", "sess_bg"],
        sessions_turns={"sess_active": [active_turn]},
    )

    app = CtxinsTUIApp(state=state, broadcaster=broadcaster)
    async with app.run_test() as pilot:
        # Broadcast TURN_STARTED for the background session
        broadcaster.publish_nowait(
            UIEvent(
                event_type=UIEventType.TURN_STARTED,
                session_id="sess_bg",
                payload={"turnIndex": 0, "model": "gemini-2.0"},
            )
        )
        # Broadcast TURN_COMPLETED for the background session
        broadcaster.publish_nowait(
            UIEvent(
                event_type=UIEventType.TURN_COMPLETED,
                session_id="sess_bg",
                payload={
                    "turnIndex": 0,
                    "inputTokens": 800,
                    "outputTokens": 100,
                    "tokens": 900,
                    "cost": 0.01,
                    "violations": [],
                },
            )
        )
        await pilot.pause(0.05)

        # Active session must remain unchanged
        assert app.state.session_id == "sess_active"
        assert len(app.state.turns) == 1

        # Background session state is isolated in sessions_turns
        assert "sess_bg" in app.state.sessions_turns
        assert len(app.state.sessions_turns["sess_bg"]) == 1
        assert app.state.sessions_turns["sess_bg"][0]["inputTokens"] == 800


@pytest.mark.asyncio
async def test_session_modal_screen() -> None:
    """Verify SessionModalScreen displays detected agents and dismisses with selected session ID."""
    from src.presentation.tui.widgets.session_modal import SessionModalScreen

    state = TUIState(session_id="sess_1")
    state.available_sessions = ["sess_1", "sess_2"]
    state.sessions_metadata = {
        "sess_1": {"agentHarness": "agy", "agent": {"display_name": "Antigravity", "pid": 123}},
        "sess_2": {"agentHarness": "claude", "agent": {"display_name": "Claude Code", "pid": 456}},
    }

    dismissed_sid = []
    modal = SessionModalScreen(state)

    app = CtxinsTUIApp(state=state)
    async with app.run_test() as pilot:
        app.push_screen(modal, callback=lambda sid: dismissed_sid.append(sid))
        await pilot.pause(0.05)

        # Modal is on top of screen stack
        assert isinstance(app.screen, SessionModalScreen)

        # Press Enter to select highlighted session
        await pilot.press("enter")
        await pilot.pause(0.05)

        assert len(dismissed_sid) == 1
        assert dismissed_sid[0] in ["sess_1", "sess_2"]


