"""Reactive TUI state container and presentation event processor."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from src.presentation.events import UIEvent, UIEventType


@dataclass
class TUIState:
    """Reactive state container holding live session data for the TUI."""

    # Active session metadata
    session_id: str = ""
    agent_harness: str = "custom"
    model: str = ""
    provider: str = ""
    status: str = "Idle"

    # Aggregated metrics
    total_tokens: int = 0
    cache_hit_ratio: float = 0.0
    cached_read_tokens: int = 0
    cached_created_tokens: int = 0
    total_spend_usd: float = 0.0
    wasted_spend_usd: float = 0.0
    pollution_score: float = 0.0

    # Turn history & selection
    turns: List[Dict[str, Any]] = field(default_factory=list)
    selected_turn_index: int = 0
    show_all_violations: bool = False
    cumulative_violations: List[Dict[str, Any]] = field(default_factory=list)

    # Context block inspection selection
    selected_block_index: int = 0
    is_exported: bool = False

    # Multi-session tracking
    available_sessions: List[str] = field(default_factory=list)
    sessions_metadata: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    sessions_turns: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    sessions_violations: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)

    def _find_or_create_turn(self, turn_index: int, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Find existing turn dict by turnIndex or insert a new one for session."""
        sid = session_id or self.session_id
        turns_list = self.sessions_turns.setdefault(sid, []) if sid else self.turns
        for turn in turns_list:
            if turn.get("turnIndex") == turn_index:
                return turn

        new_turn: Dict[str, Any] = {
            "turnIndex": turn_index,
            "turnId": f"turn_{turn_index}",
            "correlationId": "",
            "status": "idle",
            "tokens": 0,
            "inputTokens": 0,
            "outputTokens": 0,
            "cachedReadTokens": 0,
            "cachedCreatedTokens": 0,
            "cost": 0.0,
            "wastedCost": 0.0,
            "durationMs": 0.0,
            "ttftMs": None,
            "violations": [],
            "tokenBreakdown": {
                "system": 0,
                "tools": 0,
                "history": 0,
                "toolResults": 0,
                "assistant": 0,
                "cache": 0,
            },
            "blocks": [],
        }
        turns_list.append(new_turn)
        turns_list.sort(key=lambda t: int(t.get("turnIndex", 0)))
        if sid == self.session_id:
            self.turns = turns_list
        return new_turn

    def apply_event(self, event: UIEvent) -> None:
        """Update reactive state according to the incoming UIEvent."""
        sid = event.session_id or (event.payload.get("sessionId") if event.payload else None)
        if sid and sid not in self.available_sessions:
            self.available_sessions.append(sid)

        if not self.session_id and sid:
            self.session_id = sid

        etype = event.event_type
        payload = event.payload or {}

        if etype == UIEventType.SESSION_CREATED:
            target_sid = sid or self.session_id
            if target_sid and target_sid not in self.available_sessions:
                self.available_sessions.append(target_sid)
            if target_sid:
                self.sessions_metadata[target_sid] = dict(payload)

            if not self.session_id or self.session_id == "sess_default" or self.session_id == target_sid:
                self.session_id = target_sid
                self.model = payload.get("model", self.model)
                self.provider = payload.get("provider", self.provider)
                self.agent_harness = payload.get(
                    "agentHarness",
                    payload.get("agent_harness", payload.get("harness", self.agent_harness)),
                )
                self.status = payload.get("status", "Idle")

        elif etype == UIEventType.TURN_STARTED:
            target_sid = sid or self.session_id
            if target_sid == self.session_id:
                self.status = "Streaming"
            sess_turns = self.sessions_turns.get(target_sid, [])
            turn_idx = payload.get("turnIndex", payload.get("turn_index", len(sess_turns)))
            turn = self._find_or_create_turn(turn_idx, session_id=target_sid)
            turn["status"] = "streaming"
            turn["correlationId"] = payload.get(
                "correlationId", payload.get("correlation_id", turn.get("correlationId", ""))
            )
            if "model" in payload:
                turn["model"] = payload["model"]
                if target_sid == self.session_id:
                    self.model = payload["model"]
            if "provider" in payload:
                turn["provider"] = payload["provider"]
                if target_sid == self.session_id:
                    self.provider = payload["provider"]

            if target_sid == self.session_id:
                self.selected_turn_index = turn_idx

        elif etype == UIEventType.TURN_STREAMING:
            target_sid = sid or self.session_id
            if target_sid == self.session_id:
                self.status = "Streaming"
            turn_idx = payload.get(
                "turnIndex",
                payload.get("turn_index", self.selected_turn_index),
            )
            turn = self._find_or_create_turn(turn_idx, session_id=target_sid)
            turn["status"] = "streaming"

            if "deltaTokens" in payload or "delta_tokens" in payload:
                delta = payload.get("deltaTokens", payload.get("delta_tokens", 0))
                turn["outputTokens"] = turn.get("outputTokens", 0) + delta
                turn["tokens"] = turn.get("inputTokens", 0) + turn["outputTokens"]
            elif "outputTokens" in payload:
                turn["outputTokens"] = payload["outputTokens"]
                turn["tokens"] = turn.get("inputTokens", 0) + turn["outputTokens"]

            if "tokens" in payload:
                turn["tokens"] = payload["tokens"]

            if "ttftMs" in payload or "ttft_ms" in payload:
                turn["ttftMs"] = payload.get("ttftMs", payload.get("ttft_ms"))

            if "streamDurationMs" in payload:
                turn["durationMs"] = float(payload["streamDurationMs"])
            elif "durationMs" in payload or "duration_ms" in payload:
                turn["durationMs"] = float(payload.get("durationMs", payload.get("duration_ms", 0.0)))

        elif etype == UIEventType.TURN_COMPLETED:
            target_sid = sid or self.session_id
            if target_sid == self.session_id:
                self.status = "Idle"
            t_data = payload.get("turn", payload)
            turn_idx = t_data.get(
                "turnIndex",
                t_data.get("turn_index", self.selected_turn_index),
            )
            turn = self._find_or_create_turn(turn_idx, session_id=target_sid)
            turn["status"] = "completed"

            # Parse metrics
            turn["turnId"] = t_data.get("turnId", t_data.get("turn_id", turn["turnId"]))
            turn["correlationId"] = t_data.get(
                "correlationId", t_data.get("correlation_id", turn["correlationId"])
            )
            turn["inputTokens"] = t_data.get(
                "inputTokens", t_data.get("input_tokens", turn["inputTokens"])
            )
            turn["outputTokens"] = t_data.get(
                "outputTokens", t_data.get("output_tokens", turn["outputTokens"])
            )
            turn["cachedReadTokens"] = t_data.get(
                "cachedReadTokens", t_data.get("cached_read_tokens", turn["cachedReadTokens"])
            )
            turn["cachedCreatedTokens"] = t_data.get(
                "cachedCreatedTokens",
                t_data.get("cached_created_tokens", turn["cachedCreatedTokens"]),
            )

            raw_tok = t_data.get(
                "total_tokens",
                t_data.get(
                    "totalTokens",
                    t_data.get("tokens", turn["inputTokens"] + turn["outputTokens"]),
                ),
            )
            if isinstance(raw_tok, dict):
                total_tok = sum(int(v) for v in raw_tok.values() if isinstance(v, (int, float)))
            elif isinstance(raw_tok, (int, float)):
                total_tok = int(raw_tok)
            else:
                total_tok = int(turn["inputTokens"] + turn["outputTokens"])
            turn["tokens"] = total_tok

            turn["durationMs"] = float(
                t_data.get("durationMs", t_data.get("duration_ms", turn["durationMs"]))
            )
            if "ttftMs" in t_data or "ttft_ms" in t_data:
                turn["ttftMs"] = t_data.get("ttftMs", t_data.get("ttft_ms"))

            turn["cost"] = float(
                t_data.get("cost", t_data.get("turnCostUSD", t_data.get("turn_cost_usd", turn["cost"])))
            )
            turn["wastedCost"] = float(
                t_data.get(
                    "wastedCost",
                    t_data.get("wastedCostUSD", t_data.get("wasted_cost_usd", turn["wastedCost"])),
                )
            )

            # Token breakdown
            if "tokenBreakdown" in t_data:
                turn["tokenBreakdown"] = dict(t_data["tokenBreakdown"])
            elif "tokens" in t_data and isinstance(t_data["tokens"], dict):
                turn["tokenBreakdown"] = dict(t_data["tokens"])

            # Ensure cache key is present
            if "cache" not in turn["tokenBreakdown"]:
                turn["tokenBreakdown"]["cache"] = turn["cachedReadTokens"]

            # Blocks
            if "blocks" in t_data:
                turn["blocks"] = list(t_data["blocks"])

            # Violations
            raw_violations = t_data.get("violations", [])
            normalized_violations: List[Dict[str, Any]] = []
            session_viols = self.sessions_violations.setdefault(target_sid, [])
            for v in raw_violations:
                v_dict = self._normalize_violation(v, turn_idx)
                normalized_violations.append(v_dict)
                if not any(x.get("ruleId") == v_dict.get("ruleId") and x.get("turnIndex") == v_dict.get("turnIndex") for x in session_viols):
                    session_viols.append(v_dict)
                if target_sid == self.session_id:
                    self._add_to_cumulative_violations(v_dict)

            turn["violations"] = normalized_violations

            # Update session aggregates if active session
            if target_sid == self.session_id:
                self._recalculate_aggregates()

        elif etype == UIEventType.VIOLATION_DETECTED:
            target_sid = sid or self.session_id
            raw_v = payload.get("violation", payload)
            turn_idx = payload.get("turnIndex", payload.get("turn_index", self.selected_turn_index))
            v_dict = self._normalize_violation(raw_v, turn_idx)

            session_viols = self.sessions_violations.setdefault(target_sid, [])
            if not any(x.get("ruleId") == v_dict.get("ruleId") and x.get("turnIndex") == v_dict.get("turnIndex") for x in session_viols):
                session_viols.append(v_dict)
            if target_sid == self.session_id:
                self._add_to_cumulative_violations(v_dict)

            if turn_idx is not None:
                turn = self._find_or_create_turn(turn_idx, session_id=target_sid)
                turn_viols = turn.setdefault("violations", [])
                if not any(x.get("ruleId") == v_dict.get("ruleId") for x in turn_viols):
                    turn_viols.append(v_dict)

            if target_sid == self.session_id and "estimatedWasteUSD" in v_dict:
                self.wasted_spend_usd = sum(
                    float(v.get("estimatedWasteUSD", 0.0)) for v in self.cumulative_violations
                )

        elif etype == UIEventType.SESSION_SUMMARY_UPDATED:
            sum_payload: Dict[str, Any] = payload if isinstance(payload, dict) else {}
            sum_raw = sum_payload.get("summary")
            sum_dict: Dict[str, Any] = sum_raw if isinstance(sum_raw, dict) else sum_payload

            tot_tok = sum_dict.get("totalTokens", sum_dict.get("total_tokens"))
            if tot_tok is not None:
                self.total_tokens = int(tot_tok)
            elif "totalInputTokens" in sum_dict or "total_input_tokens" in sum_dict:
                inp = int(sum_dict.get("totalInputTokens", sum_dict.get("total_input_tokens", 0)) or 0)
                outp = int(sum_dict.get("totalOutputTokens", sum_dict.get("total_output_tokens", 0)) or 0)
                self.total_tokens = inp + outp

            chr_val = sum_dict.get("cacheHitRatio", sum_dict.get("cache_hit_ratio"))
            if chr_val is not None:
                self.cache_hit_ratio = float(chr_val)

            crt_val = sum_dict.get("cachedReadTokens", sum_dict.get("cached_read_tokens", sum_dict.get("cachedInputTokens")))
            if crt_val is not None:
                self.cached_read_tokens = int(crt_val)

            cost_val = sum_dict.get("totalCostUSD", sum_dict.get("total_cost_usd", sum_dict.get("estimatedCostUSD")))
            if cost_val is not None:
                self.total_spend_usd = float(cost_val)

            waste_val = sum_dict.get("wastedCostUSD", sum_dict.get("wasted_cost_usd", sum_dict.get("potentialSavingsUSD")))
            if waste_val is not None:
                self.wasted_spend_usd = float(waste_val)

            pol_val = sum_dict.get("pollutionScore", sum_dict.get("pollution_score"))
            if pol_val is not None:
                self.pollution_score = float(pol_val)

        elif etype == UIEventType.SESSION_ENDED:
            self.status = "Ended"

        elif etype == UIEventType.SESSION_ERASED:
            sid = event.session_id
            if sid and sid in self.available_sessions:
                self.available_sessions.remove(sid)
            if sid:
                self.sessions_metadata.pop(sid, None)
                self.sessions_turns.pop(sid, None)
                self.sessions_violations.pop(sid, None)

            if sid == self.session_id or not self.session_id:
                self.turns.clear()
                self.cumulative_violations.clear()
                self.total_tokens = 0
                self.input_tokens = 0
                self.output_tokens = 0
                self.cached_read_tokens = 0
                self.cached_created_tokens = 0
                self.cache_hit_ratio = 0.0
                self.total_spend_usd = 0.0
                self.wasted_spend_usd = 0.0
                self.pollution_score = 0.0
                self.selected_turn_index = 0
                self.selected_block_id = None
                self.is_exported = False
                if self.available_sessions:
                    self.switch_session(self.available_sessions[0])
                else:
                    self.session_id = "sess_default"
                    self.status = "Erased (Unexported)"
                    self.agent_harness = "unknown"
                    self.model = "unknown"
                    self.provider = "unknown"

        elif etype == UIEventType.SESSION_DISCONNECTED:
            sid = event.session_id
            if sid and sid in self.sessions_metadata:
                self.sessions_metadata[sid]["status"] = "disconnected"
            if sid == self.session_id or not self.session_id:
                self.status = "Disconnected (Exported)"

    def switch_session(
        self, target_session_id: Optional[str] = None, store: Optional[Any] = None
    ) -> Optional[str]:
        """Switch active TUI session to target_session_id or cycle to next available session."""
        if not self.available_sessions:
            return None
        if target_session_id and target_session_id in self.available_sessions:
            next_sid = target_session_id
        else:
            try:
                curr_idx = self.available_sessions.index(self.session_id)
                next_sid = self.available_sessions[(curr_idx + 1) % len(self.available_sessions)]
            except (ValueError, IndexError):
                next_sid = self.available_sessions[0]

        self.session_id = next_sid
        meta = self.sessions_metadata.get(next_sid, {})
        self.agent_harness = meta.get("agentHarness", meta.get("harness", self.agent_harness))
        self.model = meta.get("model", self.model)
        self.provider = meta.get("provider", self.provider)
        self.status = meta.get("status", "Active")

        if store is not None:
            self._load_turns_from_store(store, next_sid)
        else:
            self.turns = self.sessions_turns.get(next_sid, [])
            self.cumulative_violations = self.sessions_violations.get(next_sid, [])
            self._recalculate_aggregates()

        self.selected_turn_index = max(0, len(self.turns) - 1)
        return next_sid

    def _load_turns_from_store(self, store: Any, session_id: str) -> None:
        """Load and convert turns for session_id from SessionStore into reactive state."""
        raw_turns = store.get_session(session_id) or []
        converted: List[Dict[str, Any]] = []
        for t in raw_turns:
            if hasattr(t, "to_dict"):
                d = t.to_dict()
            elif isinstance(t, dict):
                d = dict(t)
            else:
                continue

            t_idx = d.get("turnIndex", d.get("turn_index", len(converted)))
            inp_tokens = int(d.get("inputTokens", d.get("input_tokens", getattr(t, "input_tokens", 0))))
            out_tokens = int(d.get("outputTokens", d.get("output_tokens", getattr(t, "output_tokens", 0))))
            cached_read = int(d.get("cachedReadTokens", d.get("cached_read_tokens", getattr(t, "cached_read_tokens", 0))))
            cached_created = int(d.get("cachedCreatedTokens", d.get("cached_created_tokens", getattr(t, "cached_created_tokens", 0))))
            cost_val = float(d.get("cost", d.get("turnCostUSD", d.get("turn_cost_usd", getattr(t, "turn_cost_usd", 0.0)))))
            wasted_val = float(d.get("wastedCost", d.get("wastedCostUSD", d.get("wasted_cost_usd", getattr(t, "wasted_cost_usd", 0.0)))))

            raw_tokens = d.get("tokens")
            if isinstance(raw_tokens, dict):
                tot_tokens = sum(int(v) for v in raw_tokens.values() if isinstance(v, (int, float)))
            elif isinstance(raw_tokens, (int, float)) and raw_tokens > 0:
                tot_tokens = int(raw_tokens)
            else:
                tot_tokens = int(d.get("total_tokens", d.get("totalTokens", 0)))
            if tot_tokens == 0:
                tot_tokens = inp_tokens + out_tokens

            def _extract_blocks(attr_name: str) -> List[Dict[str, Any]]:
                raw_list = getattr(t, attr_name, d.get(attr_name, []))
                res = []
                for b in (raw_list or []):
                    if hasattr(b, "to_dict"):
                        res.append(b.to_dict())
                    elif isinstance(b, dict):
                        res.append(dict(b))
                return res

            sys_b = _extract_blocks("system_blocks")
            tool_b = _extract_blocks("tool_defs")
            hist_b = _extract_blocks("conversation_history")
            res_b = _extract_blocks("tool_results")
            asst_b = _extract_blocks("assistant_blocks")
            all_b = _extract_blocks("all_blocks") or (sys_b + tool_b + hist_b + res_b + asst_b)

            tok_bd = d.get("tokenBreakdown", d.get("token_breakdown", {}))
            if not tok_bd or not isinstance(tok_bd, dict) or all(v == 0 for v in tok_bd.values()):
                tok_bd = {
                    "system": sum(int(b.get("token_count", b.get("tokenCount", 0))) for b in sys_b),
                    "tools": sum(int(b.get("token_count", b.get("tokenCount", 0))) for b in tool_b),
                    "history": sum(int(b.get("token_count", b.get("tokenCount", 0))) for b in hist_b),
                    "toolResults": sum(int(b.get("token_count", b.get("tokenCount", 0))) for b in res_b),
                    "assistant": sum(int(b.get("token_count", b.get("tokenCount", 0))) for b in asst_b),
                    "cache": cached_read,
                }

            viols = d.get("violations", [])
            if hasattr(t, "violations") and not viols:
                viols = [v.to_dict() if hasattr(v, "to_dict") else v for v in getattr(t, "violations", [])]

            turn_dict = {
                "turnIndex": t_idx,
                "turnId": d.get("turnId", d.get("turn_id", f"turn_{t_idx}")),
                "correlationId": d.get("correlationId", d.get("correlation_id", "")),
                "status": d.get("status", "completed"),
                "model": d.get("model", getattr(t, "model", self.model)),
                "provider": d.get("provider", getattr(t, "provider", self.provider)),
                "timestamp": d.get("timestamp", getattr(t, "timestamp", 0.0)),
                "durationMs": float(d.get("durationMs", d.get("duration_ms", getattr(t, "duration_ms", 0.0)))),
                "ttftMs": d.get("ttftMs", d.get("ttft_ms", getattr(t, "ttft_ms", None))),
                "tokens": tot_tokens,
                "inputTokens": inp_tokens,
                "outputTokens": out_tokens,
                "cachedReadTokens": cached_read,
                "cachedCreatedTokens": cached_created,
                "cost": cost_val,
                "wastedCost": wasted_val,
                "violations": viols,
                "tokenBreakdown": tok_bd,
                "blocks": all_b,
                "system_blocks": sys_b,
                "tool_defs": tool_b,
                "conversation_history": hist_b,
                "tool_results": res_b,
                "assistant_blocks": asst_b,
                "all_blocks": all_b,
            }
            converted.append(turn_dict)

        self.sessions_turns[session_id] = converted
        self.turns = converted
        all_viols = [v for t in converted for v in t.get("violations", [])]
        self.sessions_violations[session_id] = all_viols
        self.cumulative_violations = all_viols
        self._recalculate_aggregates()
        self.selected_turn_index = max(0, len(self.turns) - 1)

    def _normalize_violation(self, raw_v: Any, turn_idx: int) -> Dict[str, Any]:
        """Normalize raw violation object/dict into standard dictionary."""
        if hasattr(raw_v, "to_dict"):
            d = raw_v.to_dict()
        elif isinstance(raw_v, dict):
            d = dict(raw_v)
        else:
            d = {"message": str(raw_v)}

        rule_id = d.get("ruleId", d.get("rule_id", "CTX-000"))
        severity = d.get("severity", "WARN")
        if hasattr(severity, "value"):
            severity = severity.value
        title = d.get("title", rule_id)
        msg = d.get("message", "")
        waste = float(d.get("estimatedWasteUSD", d.get("estimated_waste_usd", 0.0)))
        fix = d.get("suggestedFix", d.get("suggested_fix", ""))
        block_ids = d.get("blockIds", d.get("block_ids", []))

        return {
            "ruleId": rule_id,
            "severity": str(severity).upper(),
            "title": title,
            "message": msg,
            "estimatedWasteUSD": waste,
            "suggestedFix": fix,
            "blockIds": block_ids,
            "turnIndex": turn_idx,
        }

    def _add_to_cumulative_violations(self, v_dict: Dict[str, Any]) -> None:
        """Add violation if not already present in cumulative list."""
        for existing in self.cumulative_violations:
            if (
                existing.get("ruleId") == v_dict.get("ruleId")
                and existing.get("turnIndex") == v_dict.get("turnIndex")
            ):
                return
        self.cumulative_violations.append(v_dict)

    def _recalculate_aggregates(self) -> None:
        """Recalculate running totals across turns."""
        self.total_tokens = sum(int(t.get("tokens", 0)) for t in self.turns)
        self.total_spend_usd = sum(float(t.get("cost", 0.0)) for t in self.turns)
        self.wasted_spend_usd = sum(float(t.get("wastedCost", 0.0)) for t in self.turns)
        self.cached_read_tokens = sum(int(t.get("cachedReadTokens", 0)) for t in self.turns)

        total_input = sum(int(t.get("inputTokens", 0)) for t in self.turns)
        if total_input > 0:
            self.cache_hit_ratio = round(self.cached_read_tokens / total_input, 3)

        if self.turns:
            try:
                from src.core.analyzer.scorer import PollutionScorer
                from src.schema.ast import CanonicalTurn
                canonical_turns = []
                for t in self.turns:
                    try:
                        canonical_turns.append(CanonicalTurn.from_dict(t))
                    except Exception:
                        pass
                if canonical_turns:
                    summary = PollutionScorer.calculate_summary(canonical_turns)
                    self.pollution_score = float(summary.get("pollutionScore", 0.0))
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # Helper Getters
    # -------------------------------------------------------------------------

    def get_selected_turn(self) -> Optional[Dict[str, Any]]:
        """Retrieve active selected turn dictionary."""
        if not self.turns:
            return None
        for t in self.turns:
            if t.get("turnIndex") == self.selected_turn_index:
                return t
        # Fallback to closest or last turn
        if 0 <= self.selected_turn_index < len(self.turns):
            return self.turns[self.selected_turn_index]
        return self.turns[-1]

    def get_summary(self) -> Dict[str, Any]:
        """Retrieve high-level KPI and session metrics dictionary."""
        return {
            "sessionId": self.session_id,
            "agentHarness": self.agent_harness,
            "model": self.model,
            "provider": self.provider,
            "status": self.status,
            "totalTokens": self.total_tokens,
            "cacheHitRatio": self.cache_hit_ratio,
            "cachedReadTokens": self.cached_read_tokens,
            "cachedCreatedTokens": self.cached_created_tokens,
            "totalCostUSD": self.total_spend_usd,
            "wastedCostUSD": self.wasted_spend_usd,
            "pollutionScore": self.pollution_score,
            "turnCount": len(self.turns),
        }

    def get_violations_for_selected_turn(self) -> List[Dict[str, Any]]:
        """Return violations for either selected turn or cumulative session."""
        if self.show_all_violations:
            return list(self.cumulative_violations)
        turn = self.get_selected_turn()
        if turn and "violations" in turn:
            return list(turn["violations"])
        return []

    def get_context_breakdown_for_selected_turn(self) -> Dict[str, int]:
        """Return token breakdown for active selected turn."""
        turn = self.get_selected_turn()
        if not turn:
            return {
                "system": 0,
                "tools": 0,
                "history": 0,
                "toolResults": 0,
                "assistant": 0,
                "cache": 0,
            }
        breakdown = dict(turn.get("tokenBreakdown", {}))
        breakdown.setdefault("system", 0)
        breakdown.setdefault("tools", 0)
        breakdown.setdefault("history", 0)
        breakdown.setdefault("toolResults", 0)
        breakdown.setdefault("assistant", 0)
        breakdown.setdefault("cache", turn.get("cachedReadTokens", 0))
        return breakdown

    def get_blocks_for_selected_turn(self) -> List[Dict[str, Any]]:
        """Return AST context blocks belonging to the active selected turn."""
        turn = self.get_selected_turn()
        if not turn:
            return []
        return list(turn.get("blocks", []))

    def export_to_jsonc(self, filepath: Optional[Union[str, Path]] = None) -> Path:
        """Serialize current state into canonical .jsonc schema and write to file."""
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        sid = self.session_id or "sess_default"

        data = {
            "$schema": "https://ctxins.dev/schemas/session.v1.json",
            "sessionId": sid,
            "version": "1.0",
            "timestamp": now_iso,
            "client": {
                "harness": self.agent_harness,
                "version": "1.0.0",
                "source": "ctxins-tui",
            },
            "model": {
                "provider": self.provider or "unknown",
                "name": self.model or "unknown",
            },
            "summary": self.get_summary(),
            "turns": self.turns,
            "violations": self.cumulative_violations,
        }

        jsonc_header = (
            f"// ctxins Session Export\n"
            f"// Session ID: {sid}\n"
            f"// Exported At: {now_iso}\n"
            f"// Provider: {self.provider} | Model: {self.model}\n\n"
        )
        content = jsonc_header + json.dumps(data, indent=2)

        if filepath is None:
            out_path = Path(f"session_{sid}_{int(datetime.now().timestamp())}.jsonc")
        else:
            out_path = Path(filepath)

        out_path.write_text(content, encoding="utf-8")
        self.is_exported = True
        return out_path
