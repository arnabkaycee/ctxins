"""REST API routes for session metrics, turns, recommendations, diff, and export."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Response

from src.core.analyzer.scorer import PollutionScorer
from src.core.graph.diff import TurnDiffEngine
from src.core.store.jsonc_exporter import JsoncExporter
from src.core.store.session_store import SessionStore
from src.presentation.web.ws import WebSocketHub


def create_api_router(store: SessionStore, ws_hub: WebSocketHub) -> APIRouter:
    """Create configured FastAPI APIRouter for dashboard REST endpoints."""
    router = APIRouter()

    @router.get("/sessions")
    def list_sessions() -> List[Dict[str, Any]]:
        """List active sessions with summary metrics."""
        results: List[Dict[str, Any]] = []
        for session_id in store.list_sessions():
            turns = store.get_session(session_id) or []
            summary = PollutionScorer.calculate_summary(turns)
            first_turn = turns[0] if turns else None
            results.append(
                {
                    "sessionId": session_id,
                    "turnsCount": len(turns),
                    "provider": first_turn.provider if first_turn else "unknown",
                    "model": first_turn.model if first_turn else "unknown",
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
        summary = PollutionScorer.calculate_summary(turns)
        first_turn = turns[0] if turns else None
        return {
            "sessionId": id,
            "provider": first_turn.provider if first_turn else "unknown",
            "model": first_turn.model if first_turn else "unknown",
            "summary": summary,
            "turnIndices": [t.turn_index for t in turns],
            "turns": [t.to_dict() for t in turns],
            "violations": [v.to_dict() for v in store.get_violations(id)],
        }

    @router.get("/sessions/{id}/turns")
    def get_session_turns(id: str) -> List[Dict[str, Any]]:
        """Get all CanonicalTurns for a session."""
        turns = store.get_session(id)
        if turns is None:
            raise HTTPException(status_code=404, detail=f"Session '{id}' not found")
        return [t.to_dict() for t in turns]

    @router.get("/sessions/{id}/turns/{index}")
    def get_session_turn(id: str, index: int) -> Dict[str, Any]:
        """Get specific turn details, token breakdown, and AST blocks."""
        turns = store.get_session(id)
        if turns is None:
            raise HTTPException(status_code=404, detail=f"Session '{id}' not found")
        matching = [t for t in turns if t.turn_index == index]
        if not matching:
            raise HTTPException(
                status_code=404,
                detail=f"Turn index {index} not found in session '{id}'",
            )
        turn = matching[0]
        data = turn.to_dict()
        data["all_blocks"] = [b.to_dict() for b in turn.all_blocks]
        return data

    @router.get("/sessions/{id}/recommendations")
    def get_session_recommendations(id: str) -> List[Dict[str, Any]]:
        """Get all triggered RuleViolations and remediation suggestions."""
        turns = store.get_session(id)
        if turns is None:
            raise HTTPException(status_code=404, detail=f"Session '{id}' not found")
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
            "removedBlockIds": delta.removed_block_ids,
            "persistedBlockIds": delta.persisted_block_ids,
            "tokenGrowth": delta.token_growth,
        }

    @router.get("/sessions/{id}/export")
    def export_session(id: str, format: str = "jsonc") -> Response:
        """Export session adhering to canonical .jsonc or plain .json schema."""
        turns = store.get_session(id)
        if turns is None:
            raise HTTPException(status_code=404, detail=f"Session '{id}' not found")
        is_jsonc = format.lower() == "jsonc"
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
