"""Standard .jsonc serializer and parser for session timeline and analysis reports."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.analyzer.scorer import PollutionScorer
from src.core.store.session_store import SessionStore
from src.schema.ast import CanonicalTurn

JSONC_SCHEMA_URI = "https://ctxins.dev/schemas/session.v1.json"


class JsoncExporter:
    """Exports session timelines and analysis reports into standard .jsonc format."""

    @staticmethod
    def strip_comments(jsonc_str: str) -> str:
        """Strip single-line (//...) and multi-line (/*...*/) comments from JSONC without affecting strings."""
        result = []
        i = 0
        n = len(jsonc_str)
        in_string = False
        escape = False

        while i < n:
            char = jsonc_str[i]

            if in_string:
                result.append(char)
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                i += 1
            else:
                if char == '"':
                    in_string = True
                    result.append(char)
                    i += 1
                elif char == "/" and i + 1 < n and jsonc_str[i + 1] == "/":
                    # Skip until newline or EOF
                    i += 2
                    while i < n and jsonc_str[i] != "\n":
                        i += 1
                elif char == "/" and i + 1 < n and jsonc_str[i + 1] == "*":
                    # Skip until */
                    i += 2
                    while i + 1 < n and not (jsonc_str[i] == "*" and jsonc_str[i + 1] == "/"):
                        i += 1
                    i += 2  # Skip */
                else:
                    result.append(char)
                    i += 1

        return "".join(result)

    @classmethod
    def parse_jsonc(cls, text: str) -> Dict[str, Any]:
        """Parse a JSONC string into a Python dict by stripping comments."""
        clean_json = cls.strip_comments(text)
        return json.loads(clean_json)

    @classmethod
    def build_session_dict(
        cls,
        turns: List[CanonicalTurn],
        session_id: Optional[str] = None,
        client_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Construct canonical dictionary representation of session adhering to schema."""
        resolved_sid = (
            session_id
            or (turns[0].session_id if turns else "sess_default")
        )

        first_turn = turns[0] if turns else None
        provider = first_turn.provider if first_turn else "unknown"
        model_name = first_turn.model if first_turn else "unknown"

        summary = PollutionScorer.calculate_summary(turns)

        client_info = client_metadata or {
            "harness": "claude-code",
            "version": "1.0.0",
            "source": "uds-interceptor",
        }

        turns_data = []
        for t in turns:
            thoughts_tokens = sum(
                b.token_count for b in t.assistant_blocks if b.metadata.get("type") == "thinking"
            )
            output_tokens = sum(
                b.token_count
                for b in t.assistant_blocks
                if b.metadata.get("type") not in ("thinking", "tool_use")
            ) or t.output_tokens

            turn_dict = {
                "turnIndex": t.turn_index,
                "correlationId": t.correlation_id,
                "timestamp": t.timestamp,
                "timing": {
                    "ttftMs": t.ttft_ms,
                    "durationMs": t.duration_ms,
                },
                "tokens": {
                    "system": sum(b.token_count for b in t.system_blocks),
                    "tools": sum(b.token_count for b in t.tool_defs),
                    "history": sum(b.token_count for b in t.conversation_history),
                    "toolResults": sum(b.token_count for b in t.tool_results),
                    "thoughts": thoughts_tokens,
                    "output": output_tokens,
                },
                "cache": {
                    "readTokens": t.cached_read_tokens,
                    "createdTokens": t.cached_created_tokens,
                },
                "cost": {
                    "turnCostUSD": t.turn_cost_usd,
                    "wastedCostUSD": t.wasted_cost_usd,
                },
                "violations": [
                    {
                        "ruleId": v.rule_id,
                        "severity": v.severity.value,
                        "title": v.title,
                        "message": v.message,
                        "estimatedWasteUSD": v.estimated_waste_usd,
                        "suggestedFix": v.suggested_fix,
                    }
                    for v in t.violations
                ],
            }
            turns_data.append(turn_dict)

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        return {
            "$schema": JSONC_SCHEMA_URI,
            "sessionId": resolved_sid,
            "version": "1.0",
            "timestamp": now_iso,
            "client": client_info,
            "model": {
                "provider": provider,
                "name": model_name,
            },
            "summary": {
                "totalTurns": summary["totalTurns"],
                "totalInputTokens": summary["totalInputTokens"],
                "totalOutputTokens": summary["totalOutputTokens"],
                "cachedInputTokens": summary["cachedInputTokens"],
                "cacheHitRatio": summary["cacheHitRatio"],
                "totalDurationMs": summary["totalDurationMs"],
                "estimatedCostUSD": summary["estimatedCostUSD"],
                "pollutionScore": summary["pollutionScore"],
                "potentialSavingsUSD": summary["potentialSavingsUSD"],
                "activeViolationsCount": summary["activeViolationsCount"],
            },
            "turns": turns_data,
        }

    @classmethod
    def export_session(
        cls,
        turns: List[CanonicalTurn],
        session_id: Optional[str] = None,
        client_metadata: Optional[Dict[str, Any]] = None,
        output_path: Optional[str] = None,
        include_comments: bool = True,
    ) -> str:
        """Export session report into .jsonc string and optionally write to file.

        Args:
            turns: List of CanonicalTurns to serialize.
            session_id: Optional session identifier.
            client_metadata: Optional client info dictionary.
            output_path: Optional file path to write the .jsonc content.
            include_comments: If True, annotates JSON with helpful explanatory comments.

        Returns:
            The exported JSONC string.
        """
        data = cls.build_session_dict(
            turns=turns,
            session_id=session_id,
            client_metadata=client_metadata,
        )

        raw_json = json.dumps(data, indent=2)

        if include_comments:
            # Annotate pollution score line with explanatory comment
            annotated = raw_json.replace(
                f'"pollutionScore": {data["summary"]["pollutionScore"]}',
                f'"pollutionScore": {data["summary"]["pollutionScore"]}, // 0 = pristine, 100 = critical bloat',
            )
        else:
            annotated = raw_json

        if output_path:
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(annotated, encoding="utf-8")

        return annotated

    @classmethod
    def export_from_store(
        cls,
        store: SessionStore,
        session_id: str,
        output_path: Optional[str] = None,
        include_comments: bool = True,
    ) -> str:
        """Export session directly from a SessionStore instance."""
        turns = store.get_session(session_id)
        if turns is None:
            raise ValueError(f"Session '{session_id}' not found in SessionStore.")
        return cls.export_session(
            turns=turns,
            session_id=session_id,
            output_path=output_path,
            include_comments=include_comments,
        )
