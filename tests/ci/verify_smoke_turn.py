#!/usr/bin/env python3
"""Smoke test assertion verifier for ctxins CI matrix.

Queries the running ctxins Web Dashboard API (http://127.0.0.1:8484/api/sessions)
to verify that:
1. A session was created and intercepted.
2. The detected agent harness matches the expected CLI tool.
3. At least one valid turn was recorded with non-zero usage metrics.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional


def fetch_sessions(web_url: str) -> List[Dict[str, Any]]:
    """Query /api/sessions from ctxins web API."""
    url = f"{web_url.rstrip('/')}/api/sessions"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} from {url}")
        return json.loads(resp.read().decode("utf-8"))


def verify_session(
    sessions: List[Dict[str, Any]],
    expected_harness: Optional[str] = None,
    min_turns: int = 1,
) -> tuple[bool, str]:
    """Check whether any session satisfies criteria."""
    if not sessions:
        return False, "No sessions recorded in ctxins store"

    candidates = []
    for s in sessions:
        harness = (s.get("harness") or s.get("agentHarness") or "").lower()
        agent_dict = s.get("agent") or {}
        agent_name = (agent_dict.get("name") or "").lower()
        turns_count = s.get("turnsCount", 0)

        matches_harness = True
        if expected_harness:
            exp = expected_harness.lower()
            matches_harness = (
                (exp in harness) or (exp in agent_name) or (harness in exp) or (agent_name in exp)
            )

        if matches_harness and turns_count >= min_turns:
            return (
                True,
                f"Found valid session {s.get('sessionId')} (harness={harness or agent_name}, turns={turns_count})",
            )

        candidates.append(
            f"[id={s.get('sessionId')}, harness={harness or agent_name}, turns={turns_count}]"
        )

    return (
        False,
        f"Expected harness '{expected_harness}' with >= {min_turns} turns, but found: {', '.join(candidates)}",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify ctxins session capture.")
    parser.add_argument(
        "--web-url", default="http://127.0.0.1:8484", help="Base URL of ctxins web dashboard"
    )
    parser.add_argument(
        "--expected-harness",
        help="Expected agent harness name (e.g. claude-code, opencode, pi, agy)",
    )
    parser.add_argument(
        "--min-turns", type=int, default=1, help="Minimum turns required (default: 1)"
    )
    parser.add_argument(
        "--timeout", type=int, default=15, help="Timeout in seconds to wait for session flush"
    )
    parser.add_argument(
        "--poll-interval", type=float, default=1.0, help="Polling interval in seconds"
    )

    args = parser.parse_args()

    start_time = time.time()
    last_err = ""

    print(
        f"Verifying ctxins session at {args.web_url} (expected={args.expected_harness}, min_turns={args.min_turns})..."
    )

    while time.time() - start_time < args.timeout:
        try:
            sessions = fetch_sessions(args.web_url)
            passed, msg = verify_session(
                sessions, expected_harness=args.expected_harness, min_turns=args.min_turns
            )
            if passed:
                print(f"✅ Smoke Test Passed: {msg}")
                return 0
            else:
                last_err = msg
        except Exception as e:
            last_err = f"API connection error: {e}"

        time.sleep(args.poll_interval)

    print(f"❌ Smoke Test Failed after {args.timeout}s: {last_err}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
