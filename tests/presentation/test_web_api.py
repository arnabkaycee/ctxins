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
    assert 'attachment; filename="sess_test_1.jsonc"' in response.headers["content-disposition"]
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


def test_export_session_markdown(client: TestClient) -> None:
    """Verify /api/v1/sessions/{id}/export?format=markdown returns actionable Markdown audit."""
    response = client.get("/api/v1/sessions/sess_test_1/export?format=markdown")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert (
        'attachment; filename="sess_test_1_optimization_report.md"'
        in response.headers["content-disposition"]
    )
    text = response.text
    assert "# 🔍 ctxins Context Optimization Report: `sess_test_1`" in text
    assert "## 📊 Executive Summary & Financial Audit" in text
    assert "## 🚨 Triggered Context Health Violations" in text
    assert "## 🛠️ Recommended Directives for AGENTS.md / .cursorrules" in text


def test_static_assets_serving(client: TestClient) -> None:
    """Verify static html, css, and js files are served properly."""
    res_html = client.get("/")
    assert res_html.status_code == 200
    assert "ctxins - Context Inspector Dashboard" in res_html.text
    assert 'id="auto-diff-ribbon"' in res_html.text
    assert "diff-advanced-details" in res_html.text

    res_css = client.get("/css/styles.css")
    assert res_css.status_code == 200
    assert "--bg-canvas:" in res_css.text
    assert ".auto-diff-ribbon" in res_css.text
    assert ".delta-pill" in res_css.text
    assert ".breakpoint-callout" in res_css.text
    assert ".diff-block-pill" in res_css.text
    assert ".highlight-diff-target" in res_css.text
    assert ".diff-results-hint" in res_css.text

    res_js = client.get("/js/ws_client.js")
    assert res_js.status_code == 200
    assert "class WSClient" in res_js.text

    res_app_js = client.get("/js/app.js")
    assert res_app_js.status_code == 200
    assert "renderAutoDiffRibbon" in res_app_js.text
    assert "fetchAutoDiff" in res_app_js.text
    assert "exportMarkdownReport" in res_app_js.text
    assert "locateAndHighlightBlock" in res_app_js.text
    assert "getBlockInfo" in res_app_js.text
    assert "_populateDiffSelects" in res_app_js.text
    assert "Turn #${idx + 1}" in res_app_js.text


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


def test_static_assets_proportion_bar_and_filter_chips(client: TestClient) -> None:
    """Verify context-proportion-bar and blocks-filter-chips are present in index.html, styles.css, and app.js."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "context-proportion-bar" in resp.text
    assert "blocks-filter-chips" in resp.text
    assert 'data-filter="ALL"' in resp.text
    assert 'data-filter="SYSTEM"' in resp.text
    assert 'data-filter="SKILLS"' in resp.text
    assert 'data-filter="TOOLS"' in resp.text
    assert 'data-filter="MESSAGES"' in resp.text
    assert 'data-filter="TOOL_RESULTS"' in resp.text
    assert 'data-filter="ADDED"' in resp.text
    assert 'data-filter="MUTATED"' in resp.text
    assert "blocks-expand-all-btn" in resp.text
    assert "blocks-collapse-all-btn" in resp.text

    resp_css = client.get("/css/styles.css")
    assert resp_css.status_code == 200
    assert "#context-proportion-bar" in resp_css.text
    assert ".proportion-segment" in resp_css.text
    assert ".filter-chips" in resp_css.text
    assert ".filter-chip" in resp_css.text
    assert ".section-system-header" in resp_css.text
    assert ".section-skills-header" in resp_css.text
    assert ".section-tools-header" in resp_css.text
    assert ".section-executions-header" in resp_css.text

    resp_js = client.get("/js/app.js")
    assert resp_js.status_code == 200
    assert "renderProportionBar" in resp_js.text
    assert "currentBlockFilter" in resp_js.text
    assert "setBlockFilter" in resp_js.text
    assert "_isSkillBlock" in resp_js.text
    assert "_isToolCallBlock" in resp_js.text
    assert "_isToolResultBlock" in resp_js.text
    assert "expandAllSections" in resp_js.text
    assert "collapseAllSections" in resp_js.text


def test_static_assets_cache_headers_and_busting(client: TestClient) -> None:
    """Verify static assets are served with Cache-Control headers to prevent stale browser caching."""
    resp_html = client.get("/")
    assert resp_html.status_code == 200
    assert "no-cache" in resp_html.headers.get("cache-control", "")
    assert "/css/styles.css?v=" in resp_html.text
    assert "/js/app.js?v=" in resp_html.text

    resp_css = client.get("/css/styles.css")
    assert resp_css.status_code == 200
    assert "no-cache" in resp_css.headers.get("cache-control", "")

    resp_js = client.get("/js/app.js")
    assert resp_js.status_code == 200
    assert "no-cache" in resp_js.headers.get("cache-control", "")


def test_get_grouped_session_recommendations(
    client: TestClient, populated_store: SessionStore
) -> None:
    """Verify recommendations across multi-turn session are grouped once with count and earlier turns."""
    from src.schema.ast import CanonicalTurn, RuleViolation, ViolationSeverity

    # sess_test_1 already has 2 turns. Let's add turn 2 with same CTX-001 violation as turn 1
    v = RuleViolation(
        rule_id="CTX-001",
        severity=ViolationSeverity.WARN,
        title="Stale Tool Output Bloat",
        message="Stale tool block #2",
        estimated_waste_usd=0.015,
        suggested_fix="Prune older tool payloads or truncate large responses.",
        turn_index=2,
    )
    t = CanonicalTurn(
        turn_id="turn_2",
        correlation_id="corr_2",
        session_id="sess_test_1",
        turn_index=2,
        timestamp=1700000020.0,
        provider="anthropic",
        model="claude-3-5-sonnet",
        violations=[v],
        turn_cost_usd=0.015,
        wasted_cost_usd=0.015,
    )
    populated_store.append_turn(t)

    response = client.get("/api/v1/sessions/sess_test_1/recommendations")
    assert response.status_code == 200
    recs = response.json()
    # Should be grouped once
    assert len(recs) == 1
    r = recs[0]
    assert r["rule_id"] == "CTX-001"
    assert r["total_occurrences"] == 2
    assert r["turn_count"] == 2
    assert r["turn_indices"] == [0, 2]
    assert r["current_turn_index"] == 2
    assert r["current_violation"] is not None
    assert r["earlier_turn_indices"] == [0]
    assert len(r["earlier_violations"]) == 1
    assert r["suggested_fix"] == "Prune older tool payloads or truncate large responses."


def test_export_dropdown_and_conversation_exchange_grouping(client: TestClient) -> None:
    """Verify Web UI contains export dropdown (jsonc/markdown) and lower panel message exchange grouping."""
    # 1. index.html markup
    res_html = client.get("/")
    assert res_html.status_code == 200
    html_text = res_html.text
    assert 'class="dropdown export-dropdown"' in html_text
    assert 'id="export-dropdown"' in html_text
    assert 'id="export-dropdown-btn"' in html_text
    assert 'id="export-dropdown-menu"' in html_text
    assert 'id="export-btn"' in html_text
    assert "JSONC (.jsonc)" in html_text
    assert 'id="export-md-btn"' in html_text
    assert "Markdown Report (.md)" in html_text

    # 2. styles.css rules
    res_css = client.get("/css/styles.css")
    assert res_css.status_code == 200
    css_text = res_css.text
    assert ".export-dropdown" in css_text
    assert ".dropdown-menu" in css_text
    assert ".exchange-group-header" in css_text
    assert ".exchange-item-row" in css_text
    assert ".user-msg-row" in css_text
    assert ".assistant-msg-row" in css_text
    assert ".msg-role-pill" in css_text
    assert ".msg-snippet-box" in css_text

    # 3. app.js logic
    res_js = client.get("/js/app.js")
    assert res_js.status_code == 200
    js_text = res_js.text
    assert "toggleExportDropdown" in js_text
    assert "openExportDropdown" in js_text
    assert "closeExportDropdown" in js_text
    assert "_isMessageBlock" in js_text
    assert "_getMessageRole" in js_text
    assert "_extractBlockSnippet" in js_text
    assert "exchange-group-header" in js_text
    assert "Conversation Exchange" in js_text


def test_manual_turn_diff_and_recurring_results_optimization(client: TestClient) -> None:
    """Verify manual turn diff labels are 1-indexed and CTX-004 optimization mechanisms are present."""
    res_js = client.get("/js/app.js")
    assert res_js.status_code == 200
    js_text = res_js.text

    # 1. Manual turn diff parity: 1-indexed option labels with 0-indexed values
    assert "_populateDiffSelects()" in js_text
    assert "opt1.textContent = `Turn #${idx + 1}`;" in js_text
    assert "opt1.value = idx;" in js_text
    assert "opt2.textContent = `Turn #${idx + 1}`;" in js_text
    assert "opt2.value = idx;" in js_text

    # 2. CTX-004 recurring results optimization actions
    assert "shrink-context-btn" in js_text
    assert "new-session-btn" in js_text
    assert "Shrink Context" in js_text
    assert "New Session" in js_text
    assert "CTX004" in js_text
    assert "/compact" in js_text
    assert "/clear" in js_text


def test_inspect_culprit_uncollapses_sections(client: TestClient) -> None:
    """Verify Inspect Culprit uncollapses and reveals hidden rows in collapsed sections."""
    res_js = client.get("/js/app.js")
    assert res_js.status_code == 200
    js_text = res_js.text

    # Inspect culprit button delegates to locateAndHighlightBlock
    assert "locateAndHighlightBlock(targetBlockId, targetTurn)" in js_text
    # Section uncollapsing logic
    assert "this.collapsedSections.delete(sec)" in js_text
    assert "this.collapsedExchanges.delete(ex)" in js_text
    assert "matchingRow.classList.remove('exchange-hidden')" in js_text
    assert "secHeader.classList.remove('collapsed')" in js_text
    assert "exHeader.classList.remove('collapsed')" in js_text
    assert "highlight-culprit-row" in js_text


def test_context_capacity_section_and_usage_badge(client: TestClient) -> None:
    """Verify top context capacity section, usage % badge, and K-formatted stats."""
    # 1. HTML structure
    res_html = client.get("/")
    assert res_html.status_code == 200
    html_text = res_html.text
    assert 'id="context-capacity-section"' in html_text
    assert 'id="capacity-usage-badge"' in html_text
    assert 'id="capacity-progress-bar"' in html_text
    assert 'id="capacity-used-k"' in html_text
    assert 'id="capacity-available-k"' in html_text
    assert 'id="capacity-remaining-k"' in html_text
    assert "Context Window Capacity" in html_text

    # 2. CSS styles
    res_css = client.get("/css/styles.css")
    assert res_css.status_code == 200
    css_text = res_css.text
    assert ".context-capacity-section" in css_text
    assert ".context-usage-badge" in css_text
    assert ".capacity-progress-bar" in css_text
    assert ".capacity-metric-chip" in css_text

    # 3. JavaScript logic
    res_js = client.get("/js/app.js")
    assert res_js.status_code == 200
    js_text = res_js.text
    assert "renderContextCapacity()" in js_text
    assert "getModelCapacity(" in js_text
    assert "capacityUsageBadge" in js_text
    assert "capacityUsedK" in js_text
    assert "capacityAvailableK" in js_text
    assert "% Usage" in js_text

    # 4. REST API summary metrics
    res_api = client.get("/api/v1/sessions/sess_test_1")
    assert res_api.status_code == 200
    session_data = res_api.json()
    summary = session_data["summary"]
    assert "contextCapacityTokens" in summary
    assert "contextCapacityTokensK" in summary
    assert "contextUsagePercent" in summary
    assert summary["contextCapacityTokens"] >= 128000
