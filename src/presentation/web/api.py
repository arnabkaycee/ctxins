"""REST API routes for session metrics, turns, recommendations, diff, and export."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Response

from src.core.analyzer.scorer import PollutionScorer
from src.core.graph.diff import TurnDiffEngine
from src.core.store.jsonc_exporter import JsoncExporter
from src.core.store.markdown_exporter import MarkdownExporter
from src.core.store.session_store import SessionStore
from src.presentation.web.turn_serializer import (
    annotate_blocks_lifecycle,
    serialize_turn_with_delta,
)
from src.presentation.web.ws import WebSocketHub


def create_api_router(
    store: SessionStore, ws_hub: WebSocketHub, granularity: str = "step"
) -> APIRouter:
    """Create configured FastAPI APIRouter for dashboard REST endpoints."""
    router = APIRouter()
    eff_granularity = (granularity or getattr(store, "granularity", "step") or "step").lower()

    @router.get("/config")
    def get_server_config() -> Dict[str, Any]:
        """Return server configuration metadata including turn counting granularity."""
        return {
            "granularity": getattr(store, "granularity", eff_granularity),
            "version": "0.1.0",
        }

    @router.get("/sessions")
    def list_sessions() -> List[Dict[str, Any]]:
        """List active sessions with summary metrics and detected agent harness."""
        results: List[Dict[str, Any]] = []
        for session_id in store.list_sessions():
            turns = store.get_session(session_id) or []
            meta = store.get_session_metadata(session_id) or {}
            summary = (
                PollutionScorer.calculate_summary(turns)
                if turns
                else {
                    "totalTurns": 0,
                    "totalInputTokens": 0,
                    "totalOutputTokens": 0,
                    "cachedInputTokens": 0,
                    "cacheHitRatio": 0.0,
                    "totalDurationMs": 0.0,
                    "estimatedCostUSD": 0.0,
                    "pollutionScore": 0.0,
                    "potentialSavingsUSD": 0.0,
                    "activeViolationsCount": 0,
                }
            )
            first_turn = turns[0] if turns else None
            results.append(
                {
                    "sessionId": session_id,
                    "turnsCount": len(turns),
                    "provider": first_turn.provider
                    if first_turn
                    else meta.get("provider", "unknown"),
                    "model": first_turn.model if first_turn else meta.get("model", "unknown"),
                    "harness": meta.get("harness") or meta.get("agentHarness", "unknown"),
                    "agentHarness": meta.get("agentHarness") or meta.get("harness", "unknown"),
                    "agent": meta.get("agent"),
                    "status": meta.get("status", "active"),
                    "granularity": meta.get("granularity", getattr(store, "granularity", eff_granularity)),
                    "summary": summary,
                }
            )
        return results

    @router.get("/sessions/{id}")
    def get_session(id: str) -> Dict[str, Any]:
        """Get complete session details, summary metrics, turn indices, and violations."""
        turns = store.get_session(id)
        if turns is None:
            raise HTTPException(status_code=404, detail=f"Session '{id}' not found")
        meta = store.get_session_metadata(id) or {}
        summary = (
            PollutionScorer.calculate_summary(turns)
            if turns
            else {
                "totalTurns": 0,
                "totalInputTokens": 0,
                "totalOutputTokens": 0,
                "cachedInputTokens": 0,
                "cacheHitRatio": 0.0,
                "totalDurationMs": 0.0,
                "estimatedCostUSD": 0.0,
                "pollutionScore": 0.0,
                "potentialSavingsUSD": 0.0,
                "activeViolationsCount": 0,
            }
        )
        first_turn = turns[0] if turns else None
        session_turns: List[Dict[str, Any]] = []
        for i, t in enumerate(turns or []):
            prev = turns[i - 1] if i > 0 else None
            session_turns.append(serialize_turn_with_delta(t, prev))

        return {
            "sessionId": id,
            "provider": first_turn.provider if first_turn else meta.get("provider", "unknown"),
            "model": first_turn.model if first_turn else meta.get("model", "unknown"),
            "harness": meta.get("harness") or meta.get("agentHarness", "unknown"),
            "agentHarness": meta.get("agentHarness") or meta.get("harness", "unknown"),
            "agent": meta.get("agent"),
            "status": meta.get("status", "active"),
            "granularity": meta.get("granularity", getattr(store, "granularity", eff_granularity)),
            "summary": summary,
            "turnIndices": [t.turn_index for t in turns],
            "turns": session_turns,
            "violations": [v.to_dict() for v in store.get_violations(id)],
        }

    @router.get("/sessions/{id}/turns")
    def get_session_turns(id: str) -> List[Dict[str, Any]]:
        """Get all CanonicalTurns for a session with TurnDelta and category breakdowns."""
        turns = store.get_session(id)
        if turns is None:
            raise HTTPException(status_code=404, detail=f"Session '{id}' not found")
        result: List[Dict[str, Any]] = []
        for i, t in enumerate(turns):
            prev = turns[i - 1] if i > 0 else None
            result.append(serialize_turn_with_delta(t, prev))
        return result

    @router.get("/sessions/{id}/turns/{index}")
    def get_session_turn(id: str, index: int) -> Dict[str, Any]:
        """Get specific turn details, category breakdowns, TurnDelta, and AST blocks."""
        turns = store.get_session(id)
        if turns is None:
            raise HTTPException(status_code=404, detail=f"Session '{id}' not found")
        matching = [(i, t) for i, t in enumerate(turns) if t.turn_index == index]
        if not matching:
            raise HTTPException(
                status_code=404,
                detail=f"Turn index {index} not found in session '{id}'",
            )
        pos, turn = matching[0]
        prev = turns[pos - 1] if pos > 0 else None
        data = serialize_turn_with_delta(turn, prev)
        data["all_blocks"] = [b.to_dict() for b in turn.all_blocks]
        return data

    @router.get("/sessions/{id}/turns/{index}/blocks")
    def get_session_turn_blocks(id: str, index: int) -> List[Dict[str, Any]]:
        """Get context blocks for a turn annotated with lifecycle status (added, persisted, mutated, evicted)."""
        turns = store.get_session(id)
        if turns is None:
            raise HTTPException(status_code=404, detail=f"Session '{id}' not found")
        matching = [(i, t) for i, t in enumerate(turns) if t.turn_index == index]
        if not matching:
            raise HTTPException(
                status_code=404,
                detail=f"Turn index {index} not found in session '{id}'",
            )
        pos, turn = matching[0]
        prev = turns[pos - 1] if pos > 0 else None
        return annotate_blocks_lifecycle(turn, prev)

    @router.get("/sessions/{id}/recommendations")
    def get_session_recommendations(id: str, grouped: bool = True) -> List[Dict[str, Any]]:
        """Get triggered RuleViolations and remediation suggestions, grouped across turns."""
        turns = store.get_session(id)
        if turns is None:
            raise HTTPException(status_code=404, detail=f"Session '{id}' not found")
        if grouped:
            return store.get_grouped_violations(id)
        violations = store.get_violations(id)
        return [v.to_dict() for v in violations]

    @router.get("/sessions/{id}/diff/{t1}/{t2}")
    def get_session_diff(id: str, t1: int, t2: int) -> Dict[str, Any]:
        """Compute AST diff and token drift between two turns."""
        turns = store.get_session(id)
        if turns is None:
            raise HTTPException(status_code=404, detail=f"Session '{id}' not found")
        turn_map = {t.turn_index: t for t in turns}
        turn_1 = turn_map.get(t1)
        turn_2 = turn_map.get(t2)
        if turn_1 is None:
            raise HTTPException(
                status_code=404,
                detail=f"Turn index {t1} not found in session '{id}'",
            )
        if turn_2 is None:
            raise HTTPException(
                status_code=404,
                detail=f"Turn index {t2} not found in session '{id}'",
            )
        delta = TurnDiffEngine.compute_delta(turn_1, turn_2)
        return {
            "sessionId": id,
            "fromTurnIndex": t1,
            "toTurnIndex": t2,
            "delta": delta.to_dict(),
            "addedBlockIds": delta.added_block_ids,
            "mutatedBlockIds": delta.mutated_block_ids,
            "removedBlockIds": delta.removed_block_ids,
            "persistedBlockIds": delta.persisted_block_ids,
            "cacheBreakpointBlockId": delta.cache_breakpoint_block_id,
            "tokenGrowth": delta.token_growth,
        }

    @router.get("/sessions/{id}/export")
    def export_session(id: str, format: str = "jsonc") -> Response:
        """Export session adhering to canonical .jsonc, plain .json, or actionable .md format."""
        turns = store.get_session(id)
        if turns is None:
            raise HTTPException(status_code=404, detail=f"Session '{id}' not found")
        fmt = format.lower()
        if fmt in ("md", "markdown"):
            try:
                content = MarkdownExporter.export_from_store(store, id)
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"Export failed: {exc}") from exc
            return Response(
                content=content,
                media_type="text/markdown",
                headers={
                    "Content-Disposition": f'attachment; filename="{id}_optimization_report.md"'
                },
            )

        is_jsonc = fmt == "jsonc"
        try:
            content = JsoncExporter.export_from_store(store, id, include_comments=is_jsonc)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Export failed: {exc}") from exc

        media_type = "application/jsonc" if is_jsonc else "application/json"
        ext = "jsonc" if is_jsonc else "json"
        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{id}.{ext}"'},
        )

    return router
