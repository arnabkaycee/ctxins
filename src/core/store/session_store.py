"""Thread-safe in-memory session registry and query engine."""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Set

from src.core.graph.turn_tree import ContextGraph
from src.schema.ast import CanonicalTurn, RuleViolation, TurnDelta


class SessionStore:
    """Thread-safe storage for active sessions, indexing turns, metrics, and violations."""

    def __init__(self, max_sessions: int = 100, granularity: str = "step") -> None:
        """Initialize SessionStore with bounded capacity.

        Args:
            max_sessions: Maximum number of active sessions before evicting oldest.
            granularity: Turn granularity ('step' or 'human').
        """
        self.max_sessions = max_sessions
        self.granularity = (granularity or "step").lower()
        # OrderedDict maintains session insertion/activity order for FIFO eviction
        self.sessions: OrderedDict[str, List[CanonicalTurn]] = OrderedDict()
        self.graphs: Dict[str, ContextGraph] = {}
        self.lock = threading.RLock()

        # Secondary indexes
        self._model_to_sessions: Dict[str, Set[str]] = {}
        self._violation_to_sessions: Dict[str, Set[str]] = {}
        self._exported_sessions: Set[str] = set()
        self.session_metadata: Dict[str, Dict[str, Any]] = {}
        self._session_aliases: Dict[str, str] = {}

    def alias_session(self, alias_id: str, target_session_id: str) -> None:
        """Alias a placeholder or scanner session ID to an active session ID."""
        with self.lock:
            # Prevent overwriting or hijacking a session that already has recorded turns
            existing = self.sessions.get(alias_id)
            if existing and len(existing) > 0:
                return
            self._session_aliases[alias_id] = target_session_id

    def _resolve_session_id(self, session_id: str) -> str:
        """Resolve session ID through aliases if defined."""
        return self._session_aliases.get(session_id, session_id)

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

    def update_turn(self, turn: CanonicalTurn) -> None:
        """Update an existing canonical turn in-place in its session."""
        with self.lock:
            session_id = turn.session_id
            if session_id not in self.sessions or not self.sessions[session_id]:
                return
            turns = self.sessions[session_id]
            idx = turn.turn_index
            if 0 <= idx < len(turns):
                turns[idx] = turn
            else:
                turns[-1] = turn

            if session_id in self.graphs:
                self.graphs[session_id].update_turn(turn)

            if turn.model:
                norm_model = turn.model.lower()
                if norm_model not in self._model_to_sessions:
                    self._model_to_sessions[norm_model] = set()
                self._model_to_sessions[norm_model].add(session_id)

            for v in turn.violations:
                rule_id = v.rule_id
                if rule_id not in self._violation_to_sessions:
                    self._violation_to_sessions[rule_id] = set()
                self._violation_to_sessions[rule_id].add(session_id)

    def register_session(
        self,
        session_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Register an active or auto-detected session with optional metadata."""
        with self.lock:
            if session_id not in self.sessions:
                if len(self.sessions) >= self.max_sessions:
                    oldest_session_id = next(iter(self.sessions))
                    self._evict_session(oldest_session_id)
                self.sessions[session_id] = []
                self.graphs[session_id] = ContextGraph(session_id=session_id)
            if metadata is not None:
                meta = dict(metadata)
                meta.setdefault("granularity", self.granularity)
                self.session_metadata[session_id] = meta
            elif session_id not in self.session_metadata:
                self.session_metadata[session_id] = {"granularity": self.granularity}
            self.sessions.move_to_end(session_id)

    def get_session_metadata(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve stored metadata for a session."""
        with self.lock:
            resolved = self._resolve_session_id(session_id)
            meta = self.session_metadata.get(resolved) or self.session_metadata.get(session_id)
            return dict(meta) if meta is not None else None

    def get_session(self, session_id: str) -> Optional[List[CanonicalTurn]]:
        """Retrieve copy of all CanonicalTurns for a session."""
        with self.lock:
            resolved = self._resolve_session_id(session_id)
            turns = self.sessions.get(resolved)
            if turns is None and resolved != session_id:
                turns = self.sessions.get(session_id)
            return list(turns) if turns is not None else None

    def get_graph(self, session_id: str) -> Optional[ContextGraph]:
        """Retrieve ContextGraph instance for a session."""
        with self.lock:
            resolved = self._resolve_session_id(session_id)
            g = self.graphs.get(resolved)
            return g if g is not None else self.graphs.get(session_id)

    def get_violations(
        self,
        session_id: str,
        rule_id: Optional[str] = None,
    ) -> List[RuleViolation]:
        """Retrieve all violations detected in a session, optionally filtered by rule_id."""
        with self.lock:
            resolved = self._resolve_session_id(session_id)
            turns = self.sessions.get(resolved) or self.sessions.get(session_id, [])
            violations: List[RuleViolation] = []
            for t in turns:
                for v in t.violations:
                    if getattr(v, "turn_index", None) is None:
                        v.turn_index = t.turn_index
                    if rule_id is None or v.rule_id == rule_id:
                        violations.append(v)
            return violations

    def get_grouped_violations(
        self,
        session_id: str,
        rule_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve violations grouped by warning across turns, showing count and turn breakdown."""
        with self.lock:
            resolved = self._resolve_session_id(session_id)
            turns = self.sessions.get(resolved) or self.sessions.get(session_id, [])
            current_turn_idx = turns[-1].turn_index if turns else 0

            raw_violations = self.get_violations(session_id, rule_id=rule_id)
            if not raw_violations:
                return []

            groups_map: Dict[Any, List[RuleViolation]] = {}
            for v in raw_violations:
                key = v.rule_id if v.rule_id else (v.suggested_fix or v.title)
                groups_map.setdefault(key, []).append(v)

            p_order = {"CRITICAL": 0, "WARN": 1, "INFO": 2}
            grouped: List[Dict[str, Any]] = []

            for viols in groups_map.values():
                sorted_viols = sorted(
                    viols, key=lambda x: x.turn_index if x.turn_index is not None else 0
                )
                first = sorted_viols[0]
                r_id = first.rule_id
                title = first.title

                severities = [x.severity.value for x in sorted_viols]
                if "CRITICAL" in severities:
                    sev = "CRITICAL"
                elif "WARN" in severities:
                    sev = "WARN"
                else:
                    sev = "INFO"

                earlier_viols = [
                    x
                    for x in sorted_viols
                    if x.turn_index is not None and x.turn_index < current_turn_idx
                ]
                current_viols = [
                    x
                    for x in sorted_viols
                    if x.turn_index is not None and x.turn_index == current_turn_idx
                ]

                cur_viol = current_viols[0] if current_viols else None
                fix = (
                    cur_viol.suggested_fix if cur_viol and cur_viol.suggested_fix else ""
                ) or next((x.suggested_fix for x in reversed(sorted_viols) if x.suggested_fix), "")
                total_waste = sum(x.estimated_waste_usd for x in sorted_viols)
                earlier_waste = sum(x.estimated_waste_usd for x in earlier_viols)
                current_waste = sum(x.estimated_waste_usd for x in current_viols)

                turn_indices = sorted(
                    list({x.turn_index for x in sorted_viols if x.turn_index is not None})
                )
                earlier_turns = sorted(
                    list({x.turn_index for x in earlier_viols if x.turn_index is not None})
                )

                all_bids: List[str] = []
                seen_b = set()
                for x in sorted_viols:
                    for b in x.block_ids:
                        if b not in seen_b:
                            seen_b.add(b)
                            all_bids.append(b)

                grouped.append(
                    {
                        "rule_id": r_id,
                        "ruleId": r_id,
                        "severity": sev,
                        "title": title,
                        "message": cur_viol.message if cur_viol else sorted_viols[-1].message,
                        "suggested_fix": fix,
                        "suggestedFix": fix,
                        "total_occurrences": len(sorted_viols),
                        "totalOccurrences": len(sorted_viols),
                        "turn_count": len(turn_indices),
                        "turnCount": len(turn_indices),
                        "turn_indices": turn_indices,
                        "turnIndices": turn_indices,
                        "current_turn_index": current_turn_idx,
                        "currentTurnIndex": current_turn_idx,
                        "current_violation": cur_viol.to_dict() if cur_viol else None,
                        "currentTurnViolation": cur_viol.to_dict() if cur_viol else None,
                        "earlier_violations": [x.to_dict() for x in earlier_viols],
                        "earlierViolations": [x.to_dict() for x in earlier_viols],
                        "earlier_turn_indices": earlier_turns,
                        "earlierTurnIndices": earlier_turns,
                        "total_waste_usd": total_waste,
                        "totalWasteUSD": total_waste,
                        "estimated_waste_usd": total_waste,
                        "estimatedWasteUSD": total_waste,
                        "earlier_waste_usd": earlier_waste,
                        "earlierWasteUSD": earlier_waste,
                        "current_waste_usd": current_waste,
                        "currentWasteUSD": current_waste,
                        "block_ids": all_bids,
                        "blockIds": all_bids,
                        "all_violations": [x.to_dict() for x in sorted_viols],
                        "allViolations": [x.to_dict() for x in sorted_viols],
                    }
                )

            grouped.sort(key=lambda g: (p_order.get(g["severity"], 3), -g["total_waste_usd"]))
            return grouped

    def get_timeline(self, session_id: str) -> List[Dict[str, Any]]:
        """Retrieve turn-by-turn timeline metrics for visualization and analysis."""
        with self.lock:
            resolved = self._resolve_session_id(session_id)
            turns = self.sessions.get(resolved) or self.sessions.get(session_id, [])
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

    def mark_exported(self, session_id: str) -> None:
        """Mark a session as exported via JSONC."""
        with self.lock:
            self._exported_sessions.add(session_id)

    def is_exported(self, session_id: str) -> bool:
        """Check if a session was exported via JSONC."""
        with self.lock:
            return session_id in self._exported_sessions

    def handle_disconnect(self, session_id: str, erase_unexported: bool = False) -> bool:
        """Handle client disconnection.

        As long as ctxins is open, session data is preserved in memory.
        If erase_unexported is explicitly True and session was not exported, deletes the session.
        Returns True if erased, False if preserved.
        """
        with self.lock:
            if erase_unexported and not self.is_exported(session_id):
                self.delete_session(session_id)
                return True
            return False

    def clear(self) -> None:
        """Clear all sessions, graphs, indexes, and export records."""
        with self.lock:
            self.sessions.clear()
            self.graphs.clear()
            self._model_to_sessions.clear()
            self._violation_to_sessions.clear()
            self._exported_sessions.clear()
            self.session_metadata.clear()

    def _evict_session(self, session_id: str) -> None:
        """Internal helper to remove a session and purge its index references."""
        if session_id in self.sessions:
            del self.sessions[session_id]
        if session_id in self.graphs:
            del self.graphs[session_id]
        if session_id in self.session_metadata:
            del self.session_metadata[session_id]

        for s_set in self._model_to_sessions.values():
            s_set.discard(session_id)

        for s_set in self._violation_to_sessions.values():
            s_set.discard(session_id)
