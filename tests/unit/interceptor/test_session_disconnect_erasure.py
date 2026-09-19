"""Unit tests verifying process identification before interception and session erasure on disconnect."""

import time
from unittest.mock import MagicMock

import pytest

from src.cli import CorePipelineBridge
from src.core.store.jsonc_exporter import JsoncExporter
from src.core.store.session_store import SessionStore
from src.interceptor.addon import CtxinsAddon
from src.interceptor.detection.process_detector import AgentIdentity, ProcessDetector, ProcessInfo
from src.presentation.broadcaster import PresentationBroadcaster
from src.presentation.events import UIEventType
from src.schema.ast import CanonicalTurn
from src.schema.wire import WireEnvelope, WireEventType


class DummyClient:
    def __init__(self, peername=("127.0.0.1", 54321)):
        self.peername = peername


class DummyRequest:
    def __init__(self, host="api.anthropic.com", path="/v1/messages", headers=None, content=b""):
        self.pretty_host = host
        self.host = host
        self.path = path
        self.port = 443
        self.headers = headers or {}
        self.content = content
        self.text = content.decode("utf-8", errors="replace")


class DummyFlow:
    def __init__(self, request=None, peername=("127.0.0.1", 54321)):
        self.id = "flow-test-1"
        self.request = request or DummyRequest()
        self.client_conn = DummyClient(peername=peername)
        self.metadata = {}


def test_process_identified_first_before_interception():
    detector = MagicMock(spec=ProcessDetector)
    detected_agy = AgentIdentity(
        name="agy",
        display_name="Antigravity (agy)",
        is_known=True,
        pid=9988,
        command="/usr/local/bin/agy",
        confidence=1.0,
        detection_source="process",
        process_info=ProcessInfo(pid=9988, name="agy", cmdline="/usr/local/bin/agy"),
    )
    detector.identify_client.return_value = detected_agy

    addon = CtxinsAddon(process_detector=detector)
    flow = DummyFlow(peername=("127.0.0.1", 55443))

    # Trigger requestheaders hook
    addon.requestheaders(flow)

    # 1. Verify process was identified before intercepting
    detector.identify_client.assert_called_once_with(
        "127.0.0.1", 55443, headers=flow.request.headers
    )

    # 2. Verify flow metadata has detected agent
    assert flow.metadata.get("ctxins_intercepted") is True
    assert flow.metadata.get("ctxins_agent") == detected_agy

    # 3. Verify turn in tracker has detected harness and agent
    corr_id = flow.metadata["ctxins_correlation_id"]
    turn = addon.tracker.get(corr_id)
    assert turn is not None
    assert turn.client_metadata["harness"] == "agy"
    assert turn.client_metadata["agent"]["displayName"] == "Antigravity (agy)"
    assert turn.client_metadata["process"]["pid"] == 9988

    # 4. Verify session_id was formatted with detected agent and pid
    assert "sess_agy_9988" in turn.session_id


def test_client_disconnect_emits_session_disconnected():
    detector = MagicMock(spec=ProcessDetector)
    detector.identify_client.return_value = AgentIdentity(
        name="opencode",
        display_name="OpenCode",
        is_known=True,
        pid=1234,
        command="opencode",
    )

    emitted_envelopes = []
    addon = CtxinsAddon(process_detector=detector)
    addon.emit_envelope = lambda env: emitted_envelopes.append(env)

    flow = DummyFlow(peername=("127.0.0.1", 60123))
    addon.requestheaders(flow)

    corr_id = flow.metadata["ctxins_correlation_id"]
    turn = addon.tracker.get(corr_id)
    session_id = turn.session_id

    # Simulate client disconnect
    client = DummyClient(peername=("127.0.0.1", 60123))
    addon.client_disconnected(client)

    # Verify SESSION_DISCONNECTED wire envelope was emitted
    disc_envelopes = [
        e for e in emitted_envelopes if e.event_type == WireEventType.SESSION_DISCONNECTED
    ]
    assert len(disc_envelopes) == 1
    assert disc_envelopes[0].session_id == session_id
    assert disc_envelopes[0].payload["agent"]["name"] == "opencode"


@pytest.mark.asyncio
async def test_session_preserved_on_disconnect_while_ctxins_open():
    store = SessionStore()
    broadcaster = PresentationBroadcaster()
    bridge = CorePipelineBridge(store=store, broadcaster=broadcaster)

    event_queue = await broadcaster.subscribe()

    sid = "sess_test_unexported_99"
    turn = CanonicalTurn(
        turn_index=0,
        turn_id="t_0",
        session_id=sid,
        correlation_id="c_0",
        timestamp=time.time(),
        provider="anthropic",
        model="claude-3-5-sonnet",
        input_tokens=500,
        output_tokens=100,
    )
    store.append_turn(turn)
    assert store.get_session(sid) is not None

    # Handle SESSION_DISCONNECTED envelope
    disc_envelope = WireEnvelope(
        event_type=WireEventType.SESSION_DISCONNECTED,
        correlation_id=f"disc-{sid}",
        session_id=sid,
        timestamp=time.time(),
        payload={"sessionId": sid, "reason": "client_disconnected"},
    )
    await bridge.handle_wire_envelope(disc_envelope)

    # 1. Session must NOT be erased from store while ctxins is open
    assert store.get_session(sid) is not None
    assert len(store.get_session(sid)) == 1

    # 2. UIEvent SESSION_DISCONNECTED (with preserved: True) must be broadcast
    disc_event = event_queue.get_nowait()
    assert disc_event.event_type == UIEventType.SESSION_DISCONNECTED
    assert disc_event.session_id == sid
    assert disc_event.payload["preserved"] is True


@pytest.mark.asyncio
async def test_session_preserved_on_disconnect_if_exported_via_jsonc():
    store = SessionStore()
    broadcaster = PresentationBroadcaster()
    bridge = CorePipelineBridge(store=store, broadcaster=broadcaster)

    event_queue = await broadcaster.subscribe()

    sid = "sess_test_exported_42"
    turn = CanonicalTurn(
        turn_index=0,
        turn_id="t_0",
        session_id=sid,
        correlation_id="c_0",
        timestamp=time.time(),
        provider="anthropic",
        model="claude-3-5-sonnet",
        input_tokens=500,
        output_tokens=100,
    )
    store.append_turn(turn)

    # Export via JsoncExporter (which marks store as exported)
    _ = JsoncExporter.export_from_store(store, sid)
    assert store.is_exported(sid) is True

    # Handle SESSION_DISCONNECTED envelope
    disc_envelope = WireEnvelope(
        event_type=WireEventType.SESSION_DISCONNECTED,
        correlation_id=f"disc-{sid}",
        session_id=sid,
        timestamp=time.time(),
        payload={"sessionId": sid, "reason": "client_disconnected"},
    )
    await bridge.handle_wire_envelope(disc_envelope)

    # 1. Session must NOT be erased from store because it was exported via JSONC
    assert store.get_session(sid) is not None
    assert len(store.get_session(sid)) == 1

    # 2. UIEvent SESSION_DISCONNECTED (with preserved: True) must be broadcast
    disc_event = event_queue.get_nowait()
    assert disc_event.event_type == UIEventType.SESSION_DISCONNECTED
    assert disc_event.payload["preserved"] is True
