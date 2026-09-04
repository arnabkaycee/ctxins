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

    def _find_or_create_turn(self, turn_index: int) -> Dict[str, Any]:
        """Find existing turn dict by turnIndex or insert a new one."""
        for turn in self.turns:
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
        self.turns.append(new_turn)
        self.turns.sort(key=lambda t: int(t.get("turnIndex", 0)))
        return new_turn

    def apply_event(self, event: UIEvent) -> None:
        """Update reactive state according to the incoming UIEvent."""
        if not self.session_id and event.session_id:
            self.session_id = event.session_id

        etype = event.event_type
        payload = event.payload or {}

        if etype == UIEventType.SESSION_CREATED:
            self.session_id = event.session_id or payload.get("sessionId", self.session_id)
            self.model = payload.get("model", self.model)
            self.provider = payload.get("provider", self.provider)
            self.agent_harness = payload.get(
                "agentHarness",
                payload.get("agent_harness", payload.get("harness", self.agent_harness)),
            )
            self.status = "Idle"

        elif etype == UIEventType.TURN_STARTED:
            self.status = "Streaming"
            turn_idx = payload.get("turnIndex", payload.get("turn_index", len(self.turns)))
            turn = self._find_or_create_turn(turn_idx)
            turn["status"] = "streaming"
            turn["correlationId"] = payload.get(
                "correlationId", payload.get("correlation_id", turn.get("correlationId", ""))
            )
            if "model" in payload:
                self.model = payload["model"]
            if "provider" in payload:
                self.provider = payload["provider"]

            self.selected_turn_index = turn_idx

        elif etype == UIEventType.TURN_STREAMING:
            self.status = "Streaming"
            turn_idx = payload.get(
                "turnIndex",
                payload.get("turn_index", self.selected_turn_index),
            )
            turn = self._find_or_create_turn(turn_idx)
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
            self.status = "Idle"
            t_data = payload.get("turn", payload)
            turn_idx = t_data.get(
                "turnIndex",
                t_data.get("turn_index", self.selected_turn_index),
            )
            turn = self._find_or_create_turn(turn_idx)
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

            total_tok = t_data.get(
                "tokens",
                t_data.get(
                    "totalTokens",
                    t_data.get("total_tokens", turn["inputTokens"] + turn["outputTokens"]),
                ),
            )
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
            for v in raw_violations:
                v_dict = self._normalize_violation(v, turn_idx)
                normalized_violations.append(v_dict)
                self._add_to_cumulative_violations(v_dict)

            turn["violations"] = normalized_violations

            # Update session aggregates if not explicitly pushed
            self._recalculate_aggregates()

        elif etype == UIEventType.VIOLATION_DETECTED:
            raw_v = payload.get("violation", payload)
            turn_idx = payload.get("turnIndex", payload.get("turn_index", self.selected_turn_index))
            v_dict = self._normalize_violation(raw_v, turn_idx)
            self._add_to_cumulative_violations(v_dict)

            if turn_idx is not None and self.turns:
                turn = self._find_or_create_turn(turn_idx)
                turn_viols = turn.setdefault("violations", [])
                if not any(x.get("ruleId") == v_dict.get("ruleId") for x in turn_viols):
                    turn_viols.append(v_dict)

            if "estimatedWasteUSD" in v_dict:
                self.wasted_spend_usd = sum(
                    float(v.get("estimatedWasteUSD", 0.0)) for v in self.cumulative_violations
                )

        elif etype == UIEventType.SESSION_SUMMARY_UPDATED:
            if "totalTokens" in payload or "total_tokens" in payload:
                self.total_tokens = int(payload.get("totalTokens", payload.get("total_tokens", self.total_tokens)))
            if "cacheHitRatio" in payload or "cache_hit_ratio" in payload:
                self.cache_hit_ratio = float(
                    payload.get("cacheHitRatio", payload.get("cache_hit_ratio", self.cache_hit_ratio))
                )
            if "cachedReadTokens" in payload or "cached_read_tokens" in payload:
                self.cached_read_tokens = int(
                    payload.get(
                        "cachedReadTokens",
                        payload.get("cached_read_tokens", self.cached_read_tokens),
                    )
                )
            if "totalCostUSD" in payload or "total_cost_usd" in payload:
                self.total_spend_usd = float(
                    payload.get("totalCostUSD", payload.get("total_cost_usd", self.total_spend_usd))
                )
            if "wastedCostUSD" in payload or "wasted_cost_usd" in payload:
                self.wasted_spend_usd = float(
                    payload.get("wastedCostUSD", payload.get("wasted_cost_usd", self.wasted_spend_usd))
                )
            if "pollutionScore" in payload or "pollution_score" in payload:
                self.pollution_score = float(
                    payload.get("pollutionScore", payload.get("pollution_score", self.pollution_score))
                )

        elif etype == UIEventType.SESSION_ENDED:
            self.status = "Ended"

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
        return out_path
