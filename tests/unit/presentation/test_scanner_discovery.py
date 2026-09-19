"""Unit tests for proactive agent scanning, live session discovery, and lifecycle erasure."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.cli import CorePipelineBridge
from src.core.store.session_store import SessionStore
from src.interceptor.detection.process_detector import AgentIdentity, ProcessInfo
from src.presentation.broadcaster import PresentationBroadcaster
from src.presentation.events import UIEvent, UIEventType
from src.presentation.tui.state import TUIState
from src.presentation.web.server import create_app


def test_bridge_scan_and_register_agents() -> None:
    """Verify scan_and_register_agents proactively discovers and registers running agent processes."""
    store = SessionStore()
    broadcaster = PresentationBroadcaster()
    bridge = CorePipelineBridge(store=store, broadcaster=broadcaster)

    mock_agent1 = AgentIdentity(
        name="agy",
        display_name="Antigravity",
        is_known=True,
        confidence=0.9,
        pid=12345,
        command="agy --interactive",
        process_info=ProcessInfo(pid=12345, name="agy", cmdline="agy --interactive"),
    )
    mock_agent2 = AgentIdentity(
        name="claude-code",
        display_name="Claude Code",
        is_known=True,
        confidence=0.9,
        pid=67890,
        command="claude",
        process_info=ProcessInfo(pid=67890, name="claude", cmdline="claude"),
    )

    with patch(
        "src.interceptor.detection.process_detector.ProcessDetector.scan_running_agents",
        return_value=[mock_agent1, mock_agent2],
    ):
        new_sessions = bridge.scan_and_register_agents()
        assert len(new_sessions) == 2
        assert "sess_agy_12345" in new_sessions
        assert "sess_claude-code_67890" in new_sessions

        # Verify sessions in store
        sessions = store.list_sessions()
        assert "sess_agy_12345" in sessions
        assert "sess_claude-code_67890" in sessions

        meta1 = store.get_session_metadata("sess_agy_12345")
        assert meta1 is not None
        assert meta1["harness"] == "agy"
        assert meta1["status"] == "detected"

        # Subsequent scan should not duplicate
        second_scan = bridge.scan_and_register_agents()
        assert len(second_scan) == 0


def test_bridge_cleanup_dead_agent_sessions() -> None:
    """Verify cleanup_dead_agent_sessions erases unexported sessions and preserves exported sessions."""
    store = SessionStore()
    broadcaster = PresentationBroadcaster()
    bridge = CorePipelineBridge(store=store, broadcaster=broadcaster, auto_scan=False)

    # Register two detected sessions: one will be exported, one will not
    store.register_session(
        "sess_agy_11111",
        metadata={
            "sessionId": "sess_agy_11111",
            "harness": "agy",
            "agent": {"pid": 11111, "name": "agy"},
        },
    )
    store.register_session(
        "sess_claude_22222",
        metadata={
            "sessionId": "sess_claude_22222",
            "harness": "claude-code",
            "agent": {"pid": 22222, "name": "claude"},
        },
    )

    # Mark claude session as exported via JSONC
    store.mark_exported("sess_claude_22222")

    events_received: list[UIEvent] = []

    def mock_publish(evt: UIEvent) -> None:
        events_received.append(evt)

    # Mock os.kill: both processes are dead (raise ProcessLookupError)
    with patch("os.kill", side_effect=ProcessLookupError), \
         patch.object(broadcaster, "publish_nowait", side_effect=mock_publish):
        dead = bridge.cleanup_dead_agent_sessions()
        assert "sess_agy_11111" in dead
        assert "sess_claude_22222" in dead

    # Unexported session sess_agy_11111 must be erased from store
    assert "sess_agy_11111" not in store.list_sessions()
    assert store.get_session_metadata("sess_agy_11111") is None

    # Exported session sess_claude_22222 must be preserved
    assert "sess_claude_22222" in store.list_sessions()

    # Check broadcast events
    erased_events = [e for e in events_received if e.event_type == UIEventType.SESSION_ERASED]
    disc_events = [e for e in events_received if e.event_type == UIEventType.SESSION_DISCONNECTED]

    assert len(erased_events) == 1
    assert erased_events[0].session_id == "sess_agy_11111"

    assert len(disc_events) == 1
    assert disc_events[0].session_id == "sess_claude_22222"


def test_tui_state_discovery_and_erasure() -> None:
    """Verify TUIState multi-session tracking, switching, and erasure behavior."""
    state = TUIState()

    # Initial state
    assert state.session_id == ""
    assert len(state.available_sessions) == 0

    # Session 1 detected
    state.apply_event(
        UIEvent(
            event_type=UIEventType.SESSION_CREATED,
            session_id="sess_agy_1234",
            payload={
                "sessionId": "sess_agy_1234",
                "agentHarness": "agy",
                "model": "auto-detect",
                "status": "detected",
            },
        )
    )
    assert state.session_id == "sess_agy_1234"
    assert state.agent_harness == "agy"
    assert "sess_agy_1234" in state.available_sessions

    # Session 2 detected
    state.apply_event(
        UIEvent(
            event_type=UIEventType.SESSION_CREATED,
            session_id="sess_aider_5678",
            payload={
                "sessionId": "sess_aider_5678",
                "agentHarness": "aider",
                "model": "gpt-4o",
                "status": "detected",
            },
        )
    )
    assert len(state.available_sessions) == 2
    # Active session remains the first one until switched
    assert state.session_id == "sess_agy_1234"

    # Switch session
    next_sid = state.switch_session()
    assert next_sid == "sess_aider_5678"
    assert state.session_id == "sess_aider_5678"
    assert state.agent_harness == "aider"

    # Session 2 erased
    state.apply_event(
        UIEvent(
            event_type=UIEventType.SESSION_ERASED,
            session_id="sess_aider_5678",
            payload={"sessionId": "sess_aider_5678", "reason": "process_exit"},
        )
    )
    # Automatically falls back to remaining available session sess_agy_1234
    assert "sess_aider_5678" not in state.available_sessions
    assert state.session_id == "sess_agy_1234"
    assert state.agent_harness == "agy"

    # Session 1 erased
    state.apply_event(
        UIEvent(
            event_type=UIEventType.SESSION_ERASED,
            session_id="sess_agy_1234",
            payload={"sessionId": "sess_agy_1234", "reason": "process_exit"},
        )
    )
    assert len(state.available_sessions) == 0
    assert state.session_id == "sess_default"
    assert state.status == "Erased (Unexported)"


def test_web_api_lists_zero_turn_detected_sessions() -> None:
    """Verify Web API lists proactively detected sessions before traffic occurs."""
    store = SessionStore()
    broadcaster = PresentationBroadcaster()
    app = create_app(store=store, broadcaster=broadcaster)
    client = TestClient(app)

    # Empty initially
    res = client.get("/api/v1/sessions")
    assert res.status_code == 200
    assert res.json() == []

    # Proactively register a zero-turn detected session
    store.register_session(
        "sess_opencode_9999",
        metadata={
            "sessionId": "sess_opencode_9999",
            "harness": "opencode",
            "agentHarness": "opencode",
            "model": "auto-detect",
            "provider": "auto-detect",
            "status": "detected",
            "agent": {"pid": 9999, "name": "opencode"},
        },
    )

    # Verify GET /api/v1/sessions returns it
    res = client.get("/api/v1/sessions")
    assert res.status_code == 200
    sessions = res.json()
    assert len(sessions) == 1
    s = sessions[0]
    assert s["sessionId"] == "sess_opencode_9999"
    assert s["agentHarness"] == "opencode"
    assert s["turnsCount"] == 0
    assert s["summary"]["totalTurns"] == 0
    assert s["status"] == "detected"

    # Verify GET /api/v1/sessions/{id} returns details
    res_detail = client.get("/api/v1/sessions/sess_opencode_9999")
    assert res_detail.status_code == 200
    det = res_detail.json()
    assert det["sessionId"] == "sess_opencode_9999"
    assert det["agentHarness"] == "opencode"
    assert det["turns"] == []


@pytest.mark.asyncio
async def test_tui_renders_detected_agent_details_when_no_turns() -> None:
    """Verify TUI panes show detected agent processes prominently before any turns occur."""
    from textual.widgets import OptionList, Static

    from src.presentation.tui.app import CtxinsTUIApp
    from src.presentation.tui.widgets.context_breakdown import ContextBreakdownWidget
    from src.presentation.tui.widgets.recommendations import RecommendationsWidget
    from src.presentation.tui.widgets.turn_timeline import TurnTimelineWidget

    store = SessionStore()
    broadcaster = PresentationBroadcaster()
    store.register_session(
        "sess_agy_44444",
        metadata={
            "sessionId": "sess_agy_44444",
            "harness": "agy",
            "agentHarness": "agy",
            "status": "detected",
            "agent": {"pid": 44444, "command": "agy -c", "name": "agy"},
        },
    )

    state = TUIState()
    app = CtxinsTUIApp(state=state, store=store, broadcaster=broadcaster)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        # 1. Dedicated Sessions panel shows detected agent
        from src.presentation.tui.widgets.sessions_panel import SessionsPanelWidget
        sp = app.query_one(SessionsPanelWidget)
        ol = sp.query_one("#sessions-option-list", OptionList)
        assert ol.option_count >= 1
        first_opt = ol.get_option_at_index(0)
        assert "sess_agy_44444" in str(first_opt.prompt)
        assert "44444" in str(first_opt.prompt)
        assert "agy" in str(first_opt.prompt)

        # 2. Timeline pane shows awaiting turns
        tl = app.query_one(TurnTimelineWidget)
        tl_ol = tl.query_one("#turns-option-list", OptionList)
        assert tl_ol.option_count >= 1
        assert "sess_agy_44444" in str(tl_ol.get_option_at_index(0).prompt)

        # 3. Context breakdown pane shows awaiting first turn telemetry
        bd = app.query_one(ContextBreakdownWidget)
        bd_content = bd.query_one("#breakdown-content", Static).render()
        assert "AWAITING FIRST TURN" in str(bd_content)

        # 4. Recommendations pane shows heuristics rules preview
        rec = app.query_one(RecommendationsWidget)
        rec_content = rec.query_one("#recommendations-content", Static).render()
        assert "HEURISTIC RECOMMENDATIONS" in str(rec_content)

