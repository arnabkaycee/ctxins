"""Unit tests for in-process session switching and multi-session capture."""

import json
from unittest.mock import MagicMock

import pytest

from src.cli import CorePipelineBridge
from src.core.store.session_store import SessionStore
from src.interceptor.addon import CtxinsAddon
from src.interceptor.detection.process_detector import AgentIdentity, ProcessDetector, ProcessInfo
from src.interceptor.egress.ring_buffer import BoundedRingBuffer
from src.presentation.broadcaster import PresentationBroadcaster
from src.presentation.events import UIEventType
from src.schema.ast import CanonicalTurn
from src.schema.wire import WireEnvelope, WireEventType


class MockClient:
    def __init__(self, peername=("127.0.0.1", 54321)):
        self.peername = peername


class MockRequest:
    def __init__(self, host="api.anthropic.com", path="/v1/messages", headers=None, content=b""):
        self.pretty_host = host
        self.host = host
        self.path = path
        self.port = 443
        self.headers = headers or {}
        self.content = content
        self.text = content.decode("utf-8", errors="replace")


class MockFlow:
    def __init__(self, request=None, peername=("127.0.0.1", 54321), flow_id="flow-1"):
        self.id = flow_id
        self.request = request or MockRequest()
        self.client_conn = MockClient(peername=peername)
        self.metadata = {}


def test_in_process_session_switching_explicit_headers():
    buffer = BoundedRingBuffer(100)
    detector = MagicMock(spec=ProcessDetector)
    agent = AgentIdentity(
        name="opencode",
        display_name="OpenCode",
        is_known=True,
        pid=9001,
        command="/usr/local/bin/opencode",
        confidence=1.0,
        detection_source="process",
        process_info=ProcessInfo(pid=9001, name="opencode", cmdline="/usr/local/bin/opencode"),
    )
    detector.identify_client.return_value = agent
    addon = CtxinsAddon(ring_buffer=buffer, process_detector=detector)

    # Request 1: session A via header
    req1 = MockRequest(
        headers={"x-session-id": "session-alpha"},
        content=json.dumps(
            {"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "turn 1"}]}
        ).encode("utf-8"),
    )
    flow1 = MockFlow(request=req1, flow_id="flow-1")
    addon.requestheaders(flow1)
    addon.request(flow1)

    assert len(buffer) == 1
    env1 = WireEnvelope.from_bytes(buffer.pop())
    assert env1.event_type == WireEventType.REQUEST_INITIATED
    assert env1.session_id == "session-alpha"

    # Request 2: from the same process (PID 9001), switching to session B
    req2 = MockRequest(
        headers={"x-session-id": "session-beta"},
        content=json.dumps(
            {
                "model": "claude-3-5-sonnet",
                "messages": [{"role": "user", "content": "turn 1 in new session"}],
            }
        ).encode("utf-8"),
    )
    flow2 = MockFlow(request=req2, flow_id="flow-2")
    addon.requestheaders(flow2)
    addon.request(flow2)

    assert len(buffer) == 1
    env2 = WireEnvelope.from_bytes(buffer.pop())
    assert env2.event_type == WireEventType.REQUEST_INITIATED
    assert env2.session_id == "session-beta"
    assert addon._pid_active_session[9001] == "session-beta"


def test_in_process_session_switching_explicit_body():
    buffer = BoundedRingBuffer(100)
    detector = MagicMock(spec=ProcessDetector)
    agent = AgentIdentity(
        name="agy",
        display_name="Antigravity",
        is_known=True,
        pid=9002,
        command="/usr/local/bin/agy",
        confidence=1.0,
        detection_source="process",
        process_info=ProcessInfo(pid=9002, name="agy", cmdline="/usr/local/bin/agy"),
    )
    detector.identify_client.return_value = agent
    addon = CtxinsAddon(ring_buffer=buffer, process_detector=detector)

    # Request 1 with sessionId in body
    payload1 = {
        "model": "gemini-1.5-pro",
        "sessionId": "chat-uuid-1",
        "contents": [{"role": "user", "parts": [{"text": "first topic"}]}],
    }
    req1 = MockRequest(
        host="generativelanguage.googleapis.com",
        path="/v1/models/gemini-1.5-pro:generateContent",
        content=json.dumps(payload1).encode("utf-8"),
    )
    flow1 = MockFlow(request=req1, flow_id="flow-1")
    addon.requestheaders(flow1)
    addon.request(flow1)

    assert len(buffer) == 1
    env1 = WireEnvelope.from_bytes(buffer.pop())
    assert env1.session_id == "chat-uuid-1"

    # Request 2 with different sessionId in body
    payload2 = {
        "model": "gemini-1.5-pro",
        "sessionId": "chat-uuid-2",
        "contents": [{"role": "user", "parts": [{"text": "second topic"}]}],
    }
    req2 = MockRequest(
        host="generativelanguage.googleapis.com",
        path="/v1/models/gemini-1.5-pro:generateContent",
        content=json.dumps(payload2).encode("utf-8"),
    )
    flow2 = MockFlow(request=req2, flow_id="flow-2")
    addon.requestheaders(flow2)
    addon.request(flow2)

    assert len(buffer) == 1
    env2 = WireEnvelope.from_bytes(buffer.pop())
    assert env2.session_id == "chat-uuid-2"


def test_in_process_session_switching_conversation_reset_auto_detection():
    """When an agent doesn't provide custom session headers, detecting /new or conversation reset."""
    buffer = BoundedRingBuffer(100)
    detector = MagicMock(spec=ProcessDetector)
    agent = AgentIdentity(
        name="claude",
        display_name="Claude Code",
        is_known=True,
        pid=9003,
        command="/usr/local/bin/claude",
        confidence=1.0,
        detection_source="process",
        process_info=ProcessInfo(pid=9003, name="claude", cmdline="/usr/local/bin/claude"),
    )
    detector.identify_client.return_value = agent
    addon = CtxinsAddon(ring_buffer=buffer, process_detector=detector)

    # Session 1 - Turn 1
    payload_t1 = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "How does python async work?"}],
    }
    req1 = MockRequest(content=json.dumps(payload_t1).encode("utf-8"))
    flow1 = MockFlow(request=req1, flow_id="flow-1")
    addon.requestheaders(flow1)
    addon.request(flow1)

    assert len(buffer) == 1
    env1 = WireEnvelope.from_bytes(buffer.pop())
    assert env1.session_id == "sess_claude_9003"

    # Session 1 - Turn 2 (conversation continuation in same session)
    payload_t2 = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {"role": "user", "content": "How does python async work?"},
            {"role": "assistant", "content": "Async in python is based on an event loop..."},
            {"role": "user", "content": "Show me an example with asyncio.gather."},
        ],
    }
    req2 = MockRequest(content=json.dumps(payload_t2).encode("utf-8"))
    flow2 = MockFlow(request=req2, flow_id="flow-2")
    addon.requestheaders(flow2)
    addon.request(flow2)

    assert len(buffer) == 1
    env2 = WireEnvelope.from_bytes(buffer.pop())
    assert env2.session_id == "sess_claude_9003"

    # Session 2 - User does /new or clears session in same process
    # The message history is reset to a single prompt with new content
    payload_new_sess = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "Switching topic: let's write Go code"}],
    }
    req3 = MockRequest(content=json.dumps(payload_new_sess).encode("utf-8"))
    flow3 = MockFlow(request=req3, flow_id="flow-3")
    addon.requestheaders(flow3)
    addon.request(flow3)

    assert len(buffer) == 1
    env3 = WireEnvelope.from_bytes(buffer.pop())
    assert env3.session_id == "sess_claude_9003_2"

    # Session 2 - Turn 2 in second session
    payload_new_sess_t2 = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {"role": "user", "content": "Switching topic: let's write Go code"},
            {"role": "assistant", "content": "Here is Go code..."},
            {"role": "user", "content": "Add error handling"},
        ],
    }
    req4 = MockRequest(content=json.dumps(payload_new_sess_t2).encode("utf-8"))
    flow4 = MockFlow(request=req4, flow_id="flow-4")
    addon.requestheaders(flow4)
    addon.request(flow4)

    assert len(buffer) == 1
    env4 = WireEnvelope.from_bytes(buffer.pop())
    assert env4.session_id == "sess_claude_9003_2"


def test_agy_wrapped_payload_in_process_session_switching():
    """Verify agy wrapped request payload with contents creates new distinct sessions without overwriting."""
    buffer = BoundedRingBuffer(100)
    detector = MagicMock(spec=ProcessDetector)
    agent = AgentIdentity(
        name="agy",
        display_name="Antigravity (agy)",
        is_known=True,
        pid=7788,
        command="/usr/local/bin/agy",
        confidence=1.0,
        detection_source="process",
        process_info=ProcessInfo(pid=7788, name="agy", cmdline="/usr/local/bin/agy"),
    )
    detector.identify_client.return_value = agent
    addon = CtxinsAddon(ring_buffer=buffer, process_detector=detector)

    # Turn 1: First session in agy window
    payload1 = {
        "model": "gemini-1.5-pro",
        "request": {
            "systemInstruction": {"parts": [{"text": "You are agy."}]},
            "contents": [{"role": "user", "parts": [{"text": "First session question"}]}],
        },
    }
    req1 = MockRequest(
        host="daily-cloudcode-pa.googleapis.com",
        path="/v1internal:streamGenerateContent?alt=sse",
        content=json.dumps(payload1).encode("utf-8"),
    )
    flow1 = MockFlow(request=req1, flow_id="flow-agy-1")
    addon.requestheaders(flow1)
    addon.request(flow1)

    assert len(buffer) == 1
    env1 = WireEnvelope.from_bytes(buffer.pop())
    assert env1.session_id == "sess_agy_7788"

    # Turn 2: Follow-up in same agy session
    payload2 = {
        "model": "gemini-1.5-pro",
        "request": {
            "systemInstruction": {"parts": [{"text": "You are agy."}]},
            "contents": [
                {"role": "user", "parts": [{"text": "First session question"}]},
                {"role": "model", "parts": [{"text": "First answer"}]},
                {"role": "user", "parts": [{"text": "First session continuation"}]},
            ],
        },
    }
    req2 = MockRequest(
        host="daily-cloudcode-pa.googleapis.com",
        path="/v1internal:streamGenerateContent?alt=sse",
        content=json.dumps(payload2).encode("utf-8"),
    )
    flow2 = MockFlow(request=req2, flow_id="flow-agy-2")
    addon.requestheaders(flow2)
    addon.request(flow2)

    assert len(buffer) == 1
    env2 = WireEnvelope.from_bytes(buffer.pop())
    assert env2.session_id == "sess_agy_7788"

    # Turn 3: User creates new session in same agy window (/clear or new conversation)
    payload3 = {
        "model": "gemini-1.5-pro",
        "request": {
            "systemInstruction": {"parts": [{"text": "You are agy."}]},
            "contents": [{"role": "user", "parts": [{"text": "New session brand new question"}]}],
        },
    }
    req3 = MockRequest(
        host="daily-cloudcode-pa.googleapis.com",
        path="/v1internal:streamGenerateContent?alt=sse",
        content=json.dumps(payload3).encode("utf-8"),
    )
    flow3 = MockFlow(request=req3, flow_id="flow-agy-3")
    addon.requestheaders(flow3)
    addon.request(flow3)

    assert len(buffer) == 1
    env3 = WireEnvelope.from_bytes(buffer.pop())
    # Crucial: Must be a new session ID sess_agy_7788_2, NOT overwriting sess_agy_7788!
    assert env3.session_id == "sess_agy_7788_2"


def test_agy_reused_explicit_session_id_switching():
    """Verify that when agy reuses the same explicit Cloud Code int64 session hash across conversations,
    ctxins detects the conversation reset and mints a distinct session ID (e.g. _2)."""
    buffer = BoundedRingBuffer(100)
    detector = MagicMock(spec=ProcessDetector)
    agent = AgentIdentity(
        name="agy",
        display_name="Antigravity (agy)",
        is_known=True,
        pid=70999,
        command="agy --dangerously-skip-permissions -c",
        confidence=1.0,
        detection_source="process",
        process_info=ProcessInfo(pid=70999, name="agy", cmdline="agy --dangerously-skip-permissions -c"),
    )
    detector.identify_client.return_value = agent
    addon = CtxinsAddon(ring_buffer=buffer, process_detector=detector)

    reused_hash = -3750763034362895579

    # Conversation 1 - Turn 1
    payload1 = {
        "model": "gemini-3.8-flash-medium",
        "sessionId": reused_hash,
        "request": {
            "contents": [{"role": "user", "parts": [{"text": "First conversation prompt"}]}],
        },
    }
    req1 = MockRequest(
        host="daily-cloudcode-pa.googleapis.com",
        path="/v1internal:streamGenerateContent?alt=sse",
        content=json.dumps(payload1).encode("utf-8"),
    )
    flow1 = MockFlow(request=req1, flow_id="flow-1")
    addon.requestheaders(flow1)
    addon.request(flow1)

    assert len(buffer) == 1
    env1 = WireEnvelope.from_bytes(buffer.pop())
    assert env1.session_id == "sess_agy_3750763034362895579"

    # Conversation 1 - Turn 2 (continuation of same conversation)
    payload2 = {
        "model": "gemini-3.8-flash-medium",
        "sessionId": reused_hash,
        "request": {
            "contents": [
                {"role": "user", "parts": [{"text": "First conversation prompt"}]},
                {"role": "model", "parts": [{"text": "Answer 1"}]},
                {"role": "user", "parts": [{"text": "Follow-up question"}]},
            ],
        },
    }
    req2 = MockRequest(
        host="daily-cloudcode-pa.googleapis.com",
        path="/v1internal:streamGenerateContent?alt=sse",
        content=json.dumps(payload2).encode("utf-8"),
    )
    flow2 = MockFlow(request=req2, flow_id="flow-2")
    addon.requestheaders(flow2)
    addon.request(flow2)

    assert len(buffer) == 1
    env2 = WireEnvelope.from_bytes(buffer.pop())
    assert env2.session_id == "sess_agy_3750763034362895579"

    # Conversation 2 - Turn 1 (New conversation started in same agy process; same sessionId hash reused!)
    payload3 = {
        "model": "gemini-3.8-flash-medium",
        "sessionId": reused_hash,
        "request": {
            "contents": [{"role": "user", "parts": [{"text": "Brand new conversation in same agy process"}]}],
        },
    }
    req3 = MockRequest(
        host="daily-cloudcode-pa.googleapis.com",
        path="/v1internal:streamGenerateContent?alt=sse",
        content=json.dumps(payload3).encode("utf-8"),
    )
    flow3 = MockFlow(request=req3, flow_id="flow-3")
    addon.requestheaders(flow3)
    addon.request(flow3)

    assert len(buffer) == 1
    env3 = WireEnvelope.from_bytes(buffer.pop())
    # Crucial: Must be recognized as a new session sess_agy_3750763034362895579_2!
    assert env3.session_id == "sess_agy_3750763034362895579_2"


def test_session_store_does_not_alias_populated_session():
    """Verify SessionStore.alias_session refuses to alias an existing session with turns."""
    store = SessionStore()
    sid_old = "sess_agy_1000"
    sid_new = "sess_agy_1000_2"

    turn = CanonicalTurn(
        turn_id="t1",
        session_id=sid_old,
        correlation_id="c1",
        turn_index=0,
        timestamp=1700000000.0,
        provider="gemini",
        model="gemini-1.5-pro",
    )
    store.register_session(sid_old)
    store.append_turn(turn)

    # Attempt to alias sid_old to sid_new (e.g. from scanner or late discovery)
    store.alias_session(sid_old, sid_new)

    # Old session must NOT be aliased or overwritten!
    assert store._resolve_session_id(sid_old) == sid_old
    turns = store.get_session(sid_old)
    assert turns is not None and len(turns) == 1
    assert turns[0].turn_id == "t1"


@pytest.mark.asyncio
async def test_core_pipeline_bridge_captures_all_switched_sessions():
    import time

    store = SessionStore()
    broadcaster = PresentationBroadcaster()
    bridge = CorePipelineBridge(store=store, broadcaster=broadcaster)

    queue = await broadcaster.subscribe()

    # Event 1: First session initiated
    env1 = WireEnvelope(
        event_type=WireEventType.REQUEST_INITIATED,
        correlation_id="corr-1",
        session_id="sess_opencode_100",
        timestamp=time.time(),
        payload={
            "model": "claude-3-5-sonnet",
            "provider": "anthropic",
            "client_metadata": {
                "agent": {"name": "opencode", "display_name": "OpenCode", "pid": 100}
            },
        },
    )
    await bridge.handle_wire_envelope(env1)

    assert "sess_opencode_100" in store.list_sessions()
    meta1 = store.get_session_metadata("sess_opencode_100")
    assert meta1 is not None
    assert meta1.get("agentHarness") == "opencode"

    # Event 2: Turn completed in first session
    turn1 = CanonicalTurn(
        turn_id="t1",
        session_id="sess_opencode_100",
        correlation_id="corr-1",
        timestamp=time.time(),
        provider="anthropic",
        turn_index=0,
        model="claude-3-5-sonnet",
    )
    env1_turn = WireEnvelope(
        event_type=WireEventType.TURN_COMPLETED,
        correlation_id="corr-1",
        session_id="sess_opencode_100",
        timestamp=time.time(),
        payload=turn1.to_dict(),
    )
    await bridge.handle_wire_envelope(env1_turn)
    assert len(store.get_session("sess_opencode_100") or []) == 1

    # Event 3: Process switches session to sess_opencode_100_2
    env2 = WireEnvelope(
        event_type=WireEventType.REQUEST_INITIATED,
        correlation_id="corr-2",
        session_id="sess_opencode_100_2",
        timestamp=time.time(),
        payload={
            "model": "claude-3-5-sonnet",
            "provider": "anthropic",
            "client_metadata": {
                "agent": {"name": "opencode", "display_name": "OpenCode", "pid": 100}
            },
        },
    )
    await bridge.handle_wire_envelope(env2)

    # Verify both sessions are registered and available
    sessions = store.list_sessions()
    assert "sess_opencode_100" in sessions
    assert "sess_opencode_100_2" in sessions

    # Event 4: Turn completed in second session
    turn2 = CanonicalTurn(
        turn_id="t2",
        session_id="sess_opencode_100_2",
        correlation_id="corr-2",
        timestamp=time.time(),
        provider="anthropic",
        turn_index=0,
        model="claude-3-5-sonnet",
    )
    env2_turn = WireEnvelope(
        event_type=WireEventType.TURN_COMPLETED,
        correlation_id="corr-2",
        session_id="sess_opencode_100_2",
        timestamp=time.time(),
        payload=turn2.to_dict(),
    )
    await bridge.handle_wire_envelope(env2_turn)

    # Check that both sessions retain their respective turns
    assert len(store.get_session("sess_opencode_100") or []) == 1
    assert len(store.get_session("sess_opencode_100_2") or []) == 1

    # Check UI events emitted
    received_events = []
    while not queue.empty():
        received_events.append(queue.get_nowait())

    created_events = [e for e in received_events if e.event_type == UIEventType.SESSION_CREATED]
    created_session_ids = [e.session_id for e in created_events]
    assert "sess_opencode_100" in created_session_ids
    assert "sess_opencode_100_2" in created_session_ids
