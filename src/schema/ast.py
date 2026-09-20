"""Canonical AST data classes, block definitions, and violation types."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class BlockType(str, Enum):
    SYSTEM = "system"
    TOOL_DEF = "tool_def"
    USER_MSG = "user_msg"
    ASSISTANT_MSG = "assistant_msg"
    TOOL_RESULT = "tool_result"
    INJECTED_CONTEXT = "injected_context"
    SKILL = "skill"
    THOUGHT = "thought"
    INJECTED_STATE = "injected_state"


class ViolationSeverity(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


@dataclass(slots=True)
class ContextBlock:
    """Canonical representation of an atomic context segment."""

    block_id: str
    block_type: BlockType
    content_hash: str
    token_count: int
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    identity_key: str = ""

    # Lineage tracking
    first_seen_turn: int = 0
    turns_survived: int = 0

    # Tool call/result correlation ID — populated by normalizers for all providers.
    # Links a tool_call block (in conversation_history or assistant_blocks) to its
    # corresponding tool_result block via a shared provider-assigned ID.
    call_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["block_type"] = self.block_type.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ContextBlock:
        return cls(
            block_id=data["block_id"],
            block_type=BlockType(data["block_type"]),
            content_hash=data["content_hash"],
            token_count=data["token_count"],
            content=data["content"],
            metadata=data.get("metadata", {}),
            identity_key=data.get("identity_key", ""),
            first_seen_turn=data.get("first_seen_turn", 0),
            turns_survived=data.get("turns_survived", 0),
            call_id=data.get("call_id", ""),
        )


@dataclass(slots=True)
class ToolInvocation:
    """Represents a correlated tool call + result pair within a single turn.

    Both fields are optional: an orphaned call (result not yet present) or an
    orphaned result (call came from a prior turn) are valid states.
    """

    call_id: str
    tool_name: str
    call_block_id: Optional[str]
    result_block_id: Optional[str]
    is_error: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "call_block_id": self.call_block_id,
            "result_block_id": self.result_block_id,
            "is_error": self.is_error,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolInvocation":
        return cls(
            call_id=data.get("call_id", ""),
            tool_name=data.get("tool_name", ""),
            call_block_id=data.get("call_block_id"),
            result_block_id=data.get("result_block_id"),
            is_error=bool(data.get("is_error", False)),
        )


@dataclass(slots=True)
class RuleViolation:
    """Heuristic rule trigger outcome detailing detected context pollution."""

    rule_id: str
    severity: ViolationSeverity
    title: str
    message: str
    estimated_waste_usd: float
    suggested_fix: str
    block_ids: List[str] = field(default_factory=list)
    turn_index: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["severity"] = self.severity.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RuleViolation:
        return cls(
            rule_id=data["rule_id"],
            severity=ViolationSeverity(data["severity"]),
            title=data["title"],
            message=data["message"],
            estimated_waste_usd=float(data.get("estimated_waste_usd", 0.0)),
            suggested_fix=data.get("suggested_fix", ""),
            block_ids=data.get("block_ids", []),
            turn_index=data.get("turn_index", data.get("turnIndex")),
        )


@dataclass(slots=True)
class CanonicalTurn:
    """Normalized, provider-agnostic representation of a single agent-LLM turn."""

    turn_id: str
    correlation_id: str
    session_id: str
    turn_index: int
    timestamp: float
    provider: str
    model: str

    # Context Tree Blocks
    system_blocks: List[ContextBlock] = field(default_factory=list)
    tool_defs: List[ContextBlock] = field(default_factory=list)
    conversation_history: List[ContextBlock] = field(default_factory=list)
    tool_results: List[ContextBlock] = field(default_factory=list)
    assistant_blocks: List[ContextBlock] = field(default_factory=list)

    # Usage and Timing Metrics
    input_tokens: int = 0
    output_tokens: int = 0
    cached_read_tokens: int = 0
    cached_created_tokens: int = 0
    duration_ms: float = 0.0
    ttft_ms: Optional[float] = None

    # Analysis Results
    violations: List[RuleViolation] = field(default_factory=list)
    turn_cost_usd: float = 0.0
    wasted_cost_usd: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def all_blocks(self) -> List[ContextBlock]:
        return (
            self.system_blocks
            + self.tool_defs
            + self.conversation_history
            + self.tool_results
            + self.assistant_blocks
        )

    @property
    def tool_invocations(self) -> "List[ToolInvocation]":
        """Return correlated tool call → result pairs for this turn.

        Matching priority:
        1. Exact ``call_id`` match (provider-assigned ID on both blocks).
        2. Tool-name match for any remaining unpaired blocks (best-effort).
        3. Orphaned calls (no result yet) and orphaned results (result from a
           prior turn's call) are included with their counterpart set to None.
        """
        # Gather all call blocks — both in-flight (assistant_blocks) and historical
        call_blocks = [
            b
            for b in (self.conversation_history + self.assistant_blocks)
            if b.call_id or b.metadata.get("type") in ("tool_use", "tool_call", "function_call")
        ]
        result_blocks = list(self.tool_results)

        invocations: List[ToolInvocation] = []
        used_call_ids: set = set()
        used_result_ids: set = set()

        def _tool_name(block: ContextBlock) -> str:
            meta = block.metadata
            return meta.get("name") or meta.get("tool_name") or meta.get("tool") or ""

        def _is_error(block: ContextBlock) -> bool:
            return bool(
                block.metadata.get("is_error")
                or block.metadata.get("error")
                or "error" in block.content[:50].lower()
            )

        # Pass 1: exact call_id matching
        for call in call_blocks:
            cid = call.call_id
            if not cid:
                continue
            match = next(
                (
                    r
                    for r in result_blocks
                    if r.call_id == cid and r.block_id not in used_result_ids
                ),
                None,
            )
            if match:
                used_call_ids.add(call.block_id)
                used_result_ids.add(match.block_id)
                invocations.append(
                    ToolInvocation(
                        call_id=cid,
                        tool_name=_tool_name(call) or _tool_name(match),
                        call_block_id=call.block_id,
                        result_block_id=match.block_id,
                        is_error=_is_error(match),
                    )
                )

        # Pass 2: tool-name matching for remaining unpaired blocks
        for call in call_blocks:
            if call.block_id in used_call_ids:
                continue
            call_name = _tool_name(call)
            match = next(
                (
                    r
                    for r in result_blocks
                    if r.block_id not in used_result_ids
                    and _tool_name(r) == call_name
                    and call_name
                ),
                None,
            )
            cid = call.call_id or f"name:{call_name}"
            if match:
                used_call_ids.add(call.block_id)
                used_result_ids.add(match.block_id)
                invocations.append(
                    ToolInvocation(
                        call_id=cid,
                        tool_name=call_name or _tool_name(match),
                        call_block_id=call.block_id,
                        result_block_id=match.block_id,
                        is_error=_is_error(match),
                    )
                )
            else:
                # Orphaned call — result not present in this turn
                used_call_ids.add(call.block_id)
                invocations.append(
                    ToolInvocation(
                        call_id=cid,
                        tool_name=call_name,
                        call_block_id=call.block_id,
                        result_block_id=None,
                        is_error=False,
                    )
                )

        # Pass 3: orphaned results (no matching call found — from prior turn)
        for result in result_blocks:
            if result.block_id in used_result_ids:
                continue
            cid = result.call_id or f"orphan:{result.block_id}"
            invocations.append(
                ToolInvocation(
                    call_id=cid,
                    tool_name=_tool_name(result),
                    call_block_id=None,
                    result_block_id=result.block_id,
                    is_error=_is_error(result),
                )
            )

        return invocations

    def to_dict(self) -> Dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "correlation_id": self.correlation_id,
            "session_id": self.session_id,
            "turn_index": self.turn_index,
            "timestamp": self.timestamp,
            "provider": self.provider,
            "model": self.model,
            "system_blocks": [b.to_dict() for b in self.system_blocks],
            "tool_defs": [b.to_dict() for b in self.tool_defs],
            "conversation_history": [b.to_dict() for b in self.conversation_history],
            "tool_results": [b.to_dict() for b in self.tool_results],
            "assistant_blocks": [b.to_dict() for b in self.assistant_blocks],
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_read_tokens": self.cached_read_tokens,
            "cached_created_tokens": self.cached_created_tokens,
            "duration_ms": self.duration_ms,
            "ttft_ms": self.ttft_ms,
            "violations": [v.to_dict() for v in self.violations],
            "turn_cost_usd": self.turn_cost_usd,
            "wasted_cost_usd": self.wasted_cost_usd,
            "metadata": dict(self.metadata),
            "tool_invocations": [inv.to_dict() for inv in self.tool_invocations],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CanonicalTurn:
        return cls(
            turn_id=data["turn_id"],
            correlation_id=data["correlation_id"],
            session_id=data["session_id"],
            turn_index=data["turn_index"],
            timestamp=float(data["timestamp"]),
            provider=data["provider"],
            model=data["model"],
            system_blocks=[ContextBlock.from_dict(b) for b in data.get("system_blocks", [])],
            tool_defs=[ContextBlock.from_dict(b) for b in data.get("tool_defs", [])],
            conversation_history=[
                ContextBlock.from_dict(b) for b in data.get("conversation_history", [])
            ],
            tool_results=[ContextBlock.from_dict(b) for b in data.get("tool_results", [])],
            assistant_blocks=[ContextBlock.from_dict(b) for b in data.get("assistant_blocks", [])],
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            cached_read_tokens=data.get("cached_read_tokens", 0),
            cached_created_tokens=data.get("cached_created_tokens", 0),
            duration_ms=float(data.get("duration_ms", 0.0)),
            ttft_ms=float(data["ttft_ms"]) if data.get("ttft_ms") is not None else None,
            violations=[RuleViolation.from_dict(v) for v in data.get("violations", [])],
            turn_cost_usd=float(data.get("turn_cost_usd", 0.0)),
            wasted_cost_usd=float(data.get("wasted_cost_usd", 0.0)),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(slots=True)
class TurnDelta:
    """Delta analysis between sequential turns."""

    turn_index: int
    added_block_ids: List[str] = field(default_factory=list)
    removed_block_ids: List[str] = field(default_factory=list)
    persisted_block_ids: List[str] = field(default_factory=list)
    mutated_block_ids: List[str] = field(default_factory=list)
    cache_breakpoint_block_id: Optional[str] = None
    token_growth: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TurnDelta:
        return cls(
            turn_index=data["turn_index"],
            added_block_ids=data.get("added_block_ids", []),
            removed_block_ids=data.get("removed_block_ids", []),
            persisted_block_ids=data.get("persisted_block_ids", []),
            mutated_block_ids=data.get("mutated_block_ids", []),
            cache_breakpoint_block_id=data.get("cache_breakpoint_block_id"),
            token_growth=data.get("token_growth", 0),
        )


def normalize_session_id(val: Any, harness: Optional[str] = None) -> str:
    """Normalize session identifiers to ensure canonical formatting without leading minus signs.

    Google Cloud Code / Gemini and other protobuf backends serialize 64-bit random session
    hashes as signed int64s (e.g. -3750763034362895579), resulting in negative decimal values.
    This helper strips leading hyphens, converts negative ints to absolute value, and ensures
    canonical 'sess_' prefixing so identifiers are clean and never parsed as CLI flags.
    """
    if val is None:
        return "sess_default"

    if isinstance(val, int):
        clean = str(abs(val))
        prefix = f"sess_{harness}_" if harness and harness != "unknown" else "sess_"
        return f"{prefix}{clean}"

    s = str(val).strip()
    if not s:
        return "sess_default"

    # If starts with a minus sign (e.g. stringified signed int64 "-3750...")
    if s.startswith("-"):
        clean = s.lstrip("-")
        if clean.isdigit():
            prefix = f"sess_{harness}_" if harness and harness != "unknown" else "sess_"
            return f"{prefix}{clean}"
        return f"sess_{clean}"

    # If purely numeric without prefix (e.g. "123456")
    if s.isdigit():
        prefix = f"sess_{harness}_" if harness and harness != "unknown" else "sess_"
        return f"{prefix}{s}"

    return s
