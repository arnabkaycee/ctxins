"""Unit tests for thread-safe SessionStore in-memory registry."""

import threading
from concurrent.futures import ThreadPoolExecutor

from src.core.store.session_store import SessionStore
from src.schema.ast import (
    BlockType,
    CanonicalTurn,
    ContextBlock,
    RuleViolation,
    ViolationSeverity,
)


def _make_sample_turn(
    session_id: str,
    turn_index: int,
    model: str = "claude-3-5-sonnet",
    violations: list[RuleViolation] | None = None,
) -> CanonicalTurn:
    return CanonicalTurn(
        turn_id=f"{session_id}_t{turn_index}",
        correlation_id=f"corr_{session_id}_{turn_index}",
        session_id=session_id,
        turn_index=turn_index,
        timestamp=1710000000.0 + turn_index,
        provider="anthropic",
        model=model,
        system_blocks=[
            ContextBlock(
                block_id=f"sys_{turn_index}",
                block_type=BlockType.SYSTEM,
                content_hash=f"hash_sys_{turn_index}",
                token_count=100,
                content="System prompt",
            )
        ],
        input_tokens=1000,
        output_tokens=150,
        cached_read_tokens=400,
        duration_ms=500.0,
        violations=violations or [],
        turn_cost_usd=0.015,
        wasted_cost_usd=0.005 if violations else 0.0,
    )


def test_session_store_append_and_get():
    store = SessionStore(max_sessions=10)
    turn_0 = _make_sample_turn("sess-1", 0)
    delta_0 = store.append_turn(turn_0)

    assert delta_0 is not None
    assert store.get_session_count() == 1
    assert store.list_sessions() == ["sess-1"]

    turns = store.get_session("sess-1")
    assert turns is not None
    assert len(turns) == 1
    assert turns[0].turn_id == "sess-1_t0"

    # ContextGraph was also initialized
    graph = store.get_graph("sess-1")
    assert graph is not None
    assert len(graph.turns) == 1


def test_session_store_timeline():
    store = SessionStore(max_sessions=10)
    store.append_turn(_make_sample_turn("sess-timeline", 0))
    store.append_turn(_make_sample_turn("sess-timeline", 1))

    timeline = store.get_timeline("sess-timeline")
    assert len(timeline) == 2
    assert timeline[0]["turnIndex"] == 0
    assert timeline[1]["turnIndex"] == 1
    assert timeline[0]["tokenBreakdown"]["system"] == 100


def test_session_store_violations_and_filtering():
    store = SessionStore(max_sessions=10)
    v1 = RuleViolation("CTX-001", ViolationSeverity.WARN, "t1", "m1", 0.01, "f1")
    v2 = RuleViolation("CACHE-001", ViolationSeverity.CRITICAL, "t2", "m2", 0.02, "f2")

    turn = _make_sample_turn("sess-viols", 0, violations=[v1, v2])
    store.append_turn(turn)

    all_v = store.get_violations("sess-viols")
    assert len(all_v) == 2

    filtered_ctx = store.get_violations("sess-viols", rule_id="CTX-001")
    assert len(filtered_ctx) == 1
    assert filtered_ctx[0].rule_id == "CTX-001"

    filtered_cache = store.get_violations("sess-viols", rule_id="CACHE-001")
    assert len(filtered_cache) == 1
    assert filtered_cache[0].rule_id == "CACHE-001"


def test_session_store_secondary_indexing():
    store = SessionStore(max_sessions=10)
    v_crit = RuleViolation("CTX-003", ViolationSeverity.CRITICAL, "t", "m", 0.01, "f")

    store.append_turn(_make_sample_turn("sess-a", 0, model="gpt-4o", violations=[v_crit]))
    store.append_turn(_make_sample_turn("sess-b", 0, model="claude-3-5-sonnet"))

    assert store.find_sessions_by_model("gpt-4o") == ["sess-a"]
    assert store.find_sessions_by_model("claude-3-5-sonnet") == ["sess-b"]
    assert store.find_sessions_by_violation("CTX-003") == ["sess-a"]
    assert store.find_sessions_by_violation("CACHE-001") == []


def test_session_store_capacity_eviction():
    store = SessionStore(max_sessions=3)

    store.append_turn(_make_sample_turn("sess-1", 0))
    store.append_turn(_make_sample_turn("sess-2", 0))
    store.append_turn(_make_sample_turn("sess-3", 0))
    assert store.get_session_count() == 3

    # Adding 4th session should evict oldest (sess-1)
    store.append_turn(_make_sample_turn("sess-4", 0))
    assert store.get_session_count() == 3
    assert store.get_session("sess-1") is None
    assert store.get_session("sess-2") is not None
    assert store.get_session("sess-4") is not None
    assert "sess-1" not in store.list_sessions()


def test_session_store_delete_and_clear():
    store = SessionStore(max_sessions=5)
    store.append_turn(_make_sample_turn("sess-1", 0))
    store.append_turn(_make_sample_turn("sess-2", 0))

    assert store.delete_session("sess-1") is True
    assert store.delete_session("sess-nonexistent") is False
    assert store.get_session_count() == 1

    store.clear()
    assert store.get_session_count() == 0
    assert store.list_sessions() == []


def test_session_store_concurrency_thread_safety():
    store = SessionStore(max_sessions=50)
    num_threads = 8
    turns_per_thread = 20

    barrier = threading.Barrier(num_threads)

    def worker(thread_idx: int):
        barrier.wait()
        for t_idx in range(turns_per_thread):
            turn = _make_sample_turn(f"sess-{thread_idx}", t_idx)
            store.append_turn(turn)

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker, i) for i in range(num_threads)]
        for f in futures:
            f.result()

    assert store.get_session_count() == num_threads
    for i in range(num_threads):
        turns = store.get_session(f"sess-{i}")
        assert turns is not None
        assert len(turns) == turns_per_thread
