"""Tests for CI matrix resolver and verification utilities."""

from __future__ import annotations

from scripts.ci.resolve_matrix import _parse_semver, resolve_tool_versions
from tests.ci.verify_smoke_turn import verify_session


def test_parse_semver():
    assert _parse_semver("1.2.3") == (1, 2, 3)
    assert _parse_semver("0.15.30") == (0, 15, 30)
    assert _parse_semver("2.1.278") == (2, 1, 278)


def test_resolve_tool_versions_mock():
    # agy uses system fallback
    entries = resolve_tool_versions("agy", depth=2)
    assert len(entries) >= 1
    assert entries[0]["tool"] == "agy"
    assert entries[0]["binary"] == "agy"


def test_verify_session():
    mock_sessions = [
        {
            "sessionId": "sess_123",
            "turnsCount": 2,
            "harness": "claude-code",
            "agent": {"name": "claude-code", "displayName": "Claude Code"},
        }
    ]

    # Matching harness
    passed, msg = verify_session(mock_sessions, expected_harness="claude-code", min_turns=1)
    assert passed
    assert "sess_123" in msg

    # Minimum turns check
    passed, msg = verify_session(mock_sessions, expected_harness="claude-code", min_turns=5)
    assert not passed

    # Mismatched harness
    passed, msg = verify_session(mock_sessions, expected_harness="opencode", min_turns=1)
    assert not passed
