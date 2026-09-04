"""Thread-safe in-memory session registry and query engine."""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Set

from src.core.graph.turn_tree import ContextGraph
from src.schema.ast import CanonicalTurn, RuleViolation, TurnDelta


class SessionStore:
    """Thread-safe storage for active sessions, indexing turns, metrics, and violations."""

    def __init__(self, max_sessions: int = 100) -> None:
        """Initialize SessionStore with bounded capacity.

        Args:
            max_sessions: Maximum number of active sessions before evicting oldest.
        """
        self.max_sessions = max_sessions
        # OrderedDict maintains session insertion/activity order for FIFO eviction
        self.sessions: OrderedDict[str, List[CanonicalTurn]] = OrderedDict()
        self.graphs: Dict[str, ContextGraph] = {}
        self.lock = threading.RLock()

        # Secondary indexes
        self._model_to_sessions: Dict[str, Set[str]] = {}
        self._violation_to_sessions: Dict[str, Set[str]] = {}

    def append_turn(
        self,
        turn: CanonicalTurn,
        parent_turn_id: Optional[str] = None,
    ) -> TurnDelta:
        """Append a canonical turn to its session, updating ContextGraph and indexes.

        Args:
            turn: CanonicalTurn to insert.
            parent_turn_id: Optional parent turn ID for DAG branching.

        Returns:
            The computed TurnDelta between preceding turn and this turn.
        """
        with self.lock:
            session_id = turn.session_id

            if session_id not in self.sessions:
                # Enforce capacity
                if len(self.sessions) >= self.max_sessions:
                    oldest_session_id = next(iter(self.sessions))
                    self._evict_session(oldest_session_id)

                self.sessions[session_id] = []
                self.graphs[session_id] = ContextGraph(session_id=session_id)

            # Move to end to mark recently active
            self.sessions.move_to_end(session_id)

            # Add to ContextGraph and retrieve TurnDelta
            graph = self.graphs[session_id]
            delta = graph.add_turn(turn, parent_turn_id=parent_turn_id)

            self.sessions[session_id].append(turn)

            # Update secondary index by model
            if turn.model:
                norm_model = turn.model.lower()
                if norm_model not in self._model_to_sessions:
                    self._model_to_sessions[norm_model] = set()
                self._model_to_sessions[norm_model].add(session_id)

            # Update secondary index by rule violations
            for v in turn.violations:
                rule_id = v.rule_id
                if rule_id not in self._violation_to_sessions:
                    self._violation_to_sessions[rule_id] = set()
                self._violation_to_sessions[rule_id].add(session_id)

            return delta

    def get_session(self, session_id: str) -> Optional[List[CanonicalTurn]]:
        """Retrieve copy of all CanonicalTurns for a session."""
        with self.lock:
            turns = self.sessions.get(session_id)
            return list(turns) if turns is not None else None

    def get_graph(self, session_id: str) -> Optional[ContextGraph]:
        """Retrieve ContextGraph instance for a session."""
        with self.lock:
            return self.graphs.get(session_id)

    def get_violations(
        self,
        session_id: str,
        rule_id: Optional[str] = None,
    ) -> List[RuleViolation]:
        """Retrieve all violations detected in a session, optionally filtered by rule_id."""
        with self.lock:
            turns = self.sessions.get(session_id, [])
            violations: List[RuleViolation] = []
            for t in turns:
                for v in t.violations:
                    if rule_id is None or v.rule_id == rule_id:
                        violations.append(v)
            return violations

    def get_timeline(self, session_id: str) -> List[Dict[str, Any]]:
        """Retrieve turn-by-turn timeline metrics for visualization and analysis."""
        with self.lock:
            turns = self.sessions.get(session_id, [])
            timeline = []
            for t in turns:
                timeline.append(
                    {
                        "turnIndex": t.turn_index,
                        "turnId": t.turn_id,
                        "correlationId": t.correlation_id,
                        "timestamp": t.timestamp,
                        "durationMs": t.duration_ms,
                        "ttftMs": t.ttft_ms,
                        "inputTokens": t.input_tokens,
                        "outputTokens": t.output_tokens,
                        "cachedReadTokens": t.cached_read_tokens,
                        "cachedCreatedTokens": t.cached_created_tokens,
                        "turnCostUSD": t.turn_cost_usd,
                        "wastedCostUSD": t.wasted_cost_usd,
                        "violations": [v.to_dict() for v in t.violations],
                        "tokenBreakdown": {
                            "system": sum(b.token_count for b in t.system_blocks),
                            "tools": sum(b.token_count for b in t.tool_defs),
                            "history": sum(b.token_count for b in t.conversation_history),
                            "toolResults": sum(b.token_count for b in t.tool_results),
                            "assistant": sum(b.token_count for b in t.assistant_blocks),
                        },
                    }
                )
            return timeline

    def find_sessions_by_model(self, model: str) -> List[str]:
        """Find session IDs that used a specified model name."""
        with self.lock:
            norm_model = model.lower()
            return sorted(list(self._model_to_sessions.get(norm_model, set())))

    def find_sessions_by_violation(self, rule_id: str) -> List[str]:
        """Find session IDs that triggered a specific rule violation."""
        with self.lock:
            return sorted(list(self._violation_to_sessions.get(rule_id, set())))

    def list_sessions(self) -> List[str]:
        """List all active session identifiers."""
        with self.lock:
            return list(self.sessions.keys())

    def get_session_count(self) -> int:
        """Return number of active sessions in memory."""
        with self.lock:
            return len(self.sessions)

    def delete_session(self, session_id: str) -> bool:
        """Explicitly delete a session from store and indexes."""
        with self.lock:
            if session_id in self.sessions:
                self._evict_session(session_id)
                return True
            return False

    def clear(self) -> None:
        """Clear all sessions, graphs, and indexes."""
        with self.lock:
            self.sessions.clear()
            self.graphs.clear()
            self._model_to_sessions.clear()
            self._violation_to_sessions.clear()

    def _evict_session(self, session_id: str) -> None:
        """Internal helper to remove a session and purge its index references."""
        if session_id in self.sessions:
            del self.sessions[session_id]
        if session_id in self.graphs:
            del self.graphs[session_id]

        for s_set in self._model_to_sessions.values():
            s_set.discard(session_id)

        for s_set in self._violation_to_sessions.values():
            s_set.discard(session_id)
