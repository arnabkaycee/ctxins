"""Unit and integration tests for Web Dashboard REST APIs and WebSocket Hub."""

from __future__ import annotations

import json
from typing import Generator

import pytest
from starlette.testclient import TestClient

from src.core.store.session_store import SessionStore
from src.presentation.broadcaster import PresentationBroadcaster
from src.presentation.events import UIEvent, UIEventType
from src.presentation.web.server import create_app
from src.presentation.web.ws import WebSocketHub
from src.schema.ast import (
    BlockType,
    CanonicalTurn,
    ContextBlock,
    RuleViolation,
    ViolationSeverity,
)


@pytest.fixture
def populated_store() -> SessionStore:
    """Fixture providing a SessionStore with two turns and a violation."""
    store = SessionStore()

    block_sys = ContextBlock(
        block_id="blk_sys_01",
        block_type=BlockType.SYSTEM,
        content_hash="hash_sys_1",
        token_count=100,
        content="System instructions.",
    )
    block_user = ContextBlock(
        block_id="blk_user_01",
        block_type=BlockType.USER_MSG,
        content_hash="hash_user_1",
        token_count=50,
        content="User query.",
    )
    violation = RuleViolation(
        rule_id="CTX-001",
        severity=ViolationSeverity.WARN,
        title="Stale Tool Bloat",
        message="Unused tool output retained in context",
        estimated_waste_usd=0.015,
        suggested_fix="Prune tool results after 2 turns",
        block_ids=["blk_user_01"],
    )

    turn0 = CanonicalTurn(
        turn_id="turn_0",
        correlation_id="corr_0",
        session_id="sess_test_1",
        turn_index=0,
        timestamp=1700000000.0,
        provider="anthropic",
        model="claude-3-5-sonnet",
        system_blocks=[block_sys],
        conversation_history=[block_user],
        input_tokens=150,
        output_tokens=30,
        cached_read_tokens=100,
        cached_created_tokens=0,
        duration_ms=450.0,
        ttft_ms=120.0,
        turn_cost_usd=0.005,
        wasted_cost_usd=0.001,
        violations=[violation],
    )

    block_res = ContextBlock(
        block_id="blk_res_01",
        block_type=BlockType.TOOL_RESULT,
        content_hash="hash_res_1",
        token_count=300,
        content="Tool command result output.",
    )

    turn1 = CanonicalTurn(
        turn_id="turn_1",
        correlation_id="corr_1",
        session_id="sess_test_1",
        turn_index=1,
        timestamp=1700000010.0,
        provider="anthropic",
        model="claude-3-5-sonnet",
        system_blocks=[block_sys],
        conversation_history=[block_user],
        tool_results=[block_res],
        input_tokens=450,
        output_tokens=50,
        cached_read_tokens=150,
        cached_created_tokens=300,
        duration_ms=620.0,
        ttft_ms=150.0,
        turn_cost_usd=0.015,
        wasted_cost_usd=0.003,
        violations=[],
    )

    store.append_turn(turn0)
    store.append_turn(turn1)
    return store


@pytest.fixture
def test_broadcaster() -> PresentationBroadcaster:
    """Fixture providing a PresentationBroadcaster."""
    return PresentationBroadcaster(queue_capacity=50)


@pytest.fixture
def client(
    populated_store: SessionStore,
    test_broadcaster: PresentationBroadcaster,
) -> Generator[TestClient, None, None]:
    """Fixture providing a TestClient attached to create_app."""
    app = create_app(store=populated_store, broadcaster=test_broadcaster)
    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# REST API Endpoint Tests
# ---------------------------------------------------------------------------


def test_list_sessions_empty() -> None:
    """Verify /api/v1/sessions returns empty list when store has no sessions."""
    app = create_app(store=SessionStore())
    client = TestClient(app)
    response = client.get("/api/v1/sessions")
    assert response.status_code == 200
    assert response.json() == []


def test_list_sessions_populated(client: TestClient) -> None:
    """Verify /api/v1/sessions returns sessions with summary metrics."""
    response = client.get("/api/v1/sessions")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    sess = data[0]
    assert sess["sessionId"] == "sess_test_1"
    assert sess["turnsCount"] == 2
    assert sess["provider"] == "anthropic"
    assert sess["model"] == "claude-3-5-sonnet"
    assert "summary" in sess
    assert sess["summary"]["totalTurns"] == 2
    assert sess["summary"]["totalInputTokens"] == 600
    assert sess["summary"]["totalOutputTokens"] == 80


def test_get_session_details_success(client: TestClient) -> None:
    """Verify /api/v1/sessions/{id} returns complete session details."""
    response = client.get("/api/v1/sessions/sess_test_1")
    assert response.status_code == 200
    data = response.json()
    assert data["sessionId"] == "sess_test_1"
    assert data["turnIndices"] == [0, 1]
    assert len(data["turns"]) == 2
    assert len(data["violations"]) == 1
    assert data["violations"][0]["rule_id"] == "CTX-001"
    assert data["summary"]["totalTurns"] == 2


def test_get_session_details_not_found(client: TestClient) -> None:
    """Verify /api/v1/sessions/{id} returns 404 for non-existent session."""
    response = client.get("/api/v1/sessions/non_existent_id")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_get_session_turns(client: TestClient) -> None:
    """Verify /api/v1/sessions/{id}/turns returns list of turns."""
    response = client.get("/api/v1/sessions/sess_test_1/turns")
    assert response.status_code == 200
    turns = response.json()
    assert len(turns) == 2
    assert turns[0]["turn_index"] == 0
    assert turns[1]["turn_index"] == 1


def test_get_session_turns_not_found(client: TestClient) -> None:
    """Verify /api/v1/sessions/{id}/turns returns 404 for unknown session."""
    response = client.get("/api/v1/sessions/missing/turns")
    assert response.status_code == 404


def test_get_session_turn_by_index_success(client: TestClient) -> None:
    """Verify /api/v1/sessions/{id}/turns/{index} returns specific turn with blocks."""
    response = client.get("/api/v1/sessions/sess_test_1/turns/0")
    assert response.status_code == 200
    turn = response.json()
    assert turn["turn_index"] == 0
    assert turn["input_tokens"] == 150
    assert len(turn["system_blocks"]) == 1
    assert turn["system_blocks"][0]["block_id"] == "blk_sys_01"
    assert "all_blocks" in turn
    assert len(turn["all_blocks"]) == 2


def test_get_session_turn_by_index_not_found(client: TestClient) -> None:
    """Verify /api/v1/sessions/{id}/turns/{index} returns 404 for missing index."""
    response = client.get("/api/v1/sessions/sess_test_1/turns/99")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_get_session_recommendations(client: TestClient) -> None:
    """Verify /api/v1/sessions/{id}/recommendations returns rule violations."""
    response = client.get("/api/v1/sessions/sess_test_1/recommendations")
    assert response.status_code == 200
    recs = response.json()
    assert len(recs) == 1
    assert recs[0]["rule_id"] == "CTX-001"
    assert recs[0]["severity"] == "WARN"
    assert "suggested_fix" in recs[0]


def test_get_session_diff_success(client: TestClient) -> None:
    """Verify /api/v1/sessions/{id}/diff/{t1}/{t2} computes AST block delta."""
    response = client.get("/api/v1/sessions/sess_test_1/diff/0/1")
    assert response.status_code == 200
    diff = response.json()
    assert diff["sessionId"] == "sess_test_1"
    assert diff["fromTurnIndex"] == 0
    assert diff["toTurnIndex"] == 1
    assert "blk_res_01" in diff["addedBlockIds"]
    assert "blk_sys_01" in diff["persistedBlockIds"]
    assert diff["tokenGrowth"] == 300  # 450 input tokens - 150 input tokens


def test_get_session_diff_invalid_turn(client: TestClient) -> None:
    """Verify /api/v1/sessions/{id}/diff/{t1}/{t2} returns 404 when index is invalid."""
    response = client.get("/api/v1/sessions/sess_test_1/diff/0/99")
    assert response.status_code == 404


def test_export_session_jsonc(client: TestClient) -> None:
    """Verify /api/v1/sessions/{id}/export returns valid .jsonc string."""
    response = client.get("/api/v1/sessions/sess_test_1/export?format=jsonc")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/jsonc")
    assert "attachment; filename=\"sess_test_1.jsonc\"" in response.headers["content-disposition"]
    text = response.text
    assert "https://ctxins.dev/schemas/session.v1.json" in text
    assert "// 0 = pristine, 100 = critical bloat" in text


def test_export_session_plain_json(client: TestClient) -> None:
    """Verify /api/v1/sessions/{id}/export?format=json returns parseable JSON."""
    response = client.get("/api/v1/sessions/sess_test_1/export?format=json")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    data = json.loads(response.text)
    assert data["sessionId"] == "sess_test_1"
    assert data["summary"]["totalTurns"] == 2


def test_static_assets_serving(client: TestClient) -> None:
    """Verify static html, css, and js files are served properly."""
    res_html = client.get("/")
    assert res_html.status_code == 200
    assert "ctxins - Context Inspector Dashboard" in res_html.text

    res_css = client.get("/css/styles.css")
    assert res_css.status_code == 200
    assert "--bg-canvas:" in res_css.text

    res_js = client.get("/js/ws_client.js")
    assert res_js.status_code == 200
    assert "class WSClient" in res_js.text


# ---------------------------------------------------------------------------
# WebSocket Endpoint & WebSocketHub Tests
# ---------------------------------------------------------------------------


def test_websocket_initial_snapshot(client: TestClient) -> None:
    """Verify connecting to /ws/live receives initial SNAPSHOT message."""
    with client.websocket_connect("/ws/live?session_id=sess_test_1") as ws:
        snapshot = ws.receive_json()
        assert snapshot["type"] == "SNAPSHOT"
        assert snapshot["sessionId"] == "sess_test_1"
        assert "summary" in snapshot
        assert snapshot["summary"]["totalTurns"] == 2
        assert len(snapshot["turns"]) == 2
        assert len(snapshot["violations"]) == 1


def test_websocket_broadcaster_event_forwarding(
    client: TestClient,
    test_broadcaster: PresentationBroadcaster,
) -> None:
    """Verify live events published to PresentationBroadcaster are forwarded over WebSocket."""
    with client.websocket_connect("/ws/live?session_id=sess_test_1") as ws:
        # First message is snapshot
        snapshot = ws.receive_json()
        assert snapshot["type"] == "SNAPSHOT"

        # Publish a TURN_COMPLETED event
        event = UIEvent(
            event_type=UIEventType.TURN_COMPLETED,
            session_id="sess_test_1",
            payload={"turnIndex": 2, "tokens": 520},
        )
        test_broadcaster.publish_nowait(event)

        # Receive streamed event
        received = ws.receive_json()
        assert received["type"] == "turn_completed"
        assert received["sessionId"] == "sess_test_1"
        assert received["payload"]["turnIndex"] == 2


def test_websocket_hub_direct_methods() -> None:
    """Verify WebSocketHub register, unregister, and connection tracking."""
    hub = WebSocketHub()
    assert hub.client_count == 0
    assert len(hub.connections) == 0

    class DummyWS:
        pass

    dummy = DummyWS()  # type: ignore[assignment]
    hub.register(dummy, session_id="sess_abc")  # type: ignore[arg-type]
    assert hub.client_count == 1
    assert dummy in hub.connections

    hub.unregister(dummy)  # type: ignore[arg-type]
    assert hub.client_count == 0


def test_static_assets_and_json_viewer_served(client: TestClient) -> None:
    """Verify index.html, json_viewer.js, and styles.css are correctly served."""
    # 1. index.html includes JSON viewer markup and script tag
    resp = client.get("/")
    assert resp.status_code == 200
    assert "modal-tree-container" in resp.text
    assert "modal-expand-all-btn" in resp.text
    assert "modal-view-tree-btn" in resp.text
    assert "/js/json_viewer.js" in resp.text

    # 2. json_viewer.js is served
    resp_js = client.get("/js/json_viewer.js")
    assert resp_js.status_code == 200
    assert "class JsonViewer" in resp_js.text
    assert "collapseAll" in resp_js.text
    assert "expandAll" in resp_js.text

    # 3. styles.css contains json tree styles
    resp_css = client.get("/css/styles.css")
    assert resp_css.status_code == 200
    assert ".json-tree-container" in resp_css.text
    assert ".json-caret" in resp_css.text
