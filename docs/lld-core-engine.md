# Low-Level Design (LLD): Core Engine

## 1. Module Structure & Package Layout

The Core Engine is an ingestion-agnostic stateful daemon responsible for normalization, graph diffing, pollution detection, and presentation streaming.

```text
core/
├── __init__.py
├── server/
│   ├── __init__.py
│   ├── uds_server.py         # Async length-prefixed UDS socket server
│   └── framer.py             # Wire unmarshaler & protocol validation
├── ast/
│   ├── __init__.py
│   ├── nodes.py              # Canonical AST Node dataclasses
│   └── normalizers/
│       ├── base.py           # Abstract Provider Normalizer
│       ├── anthropic.py      # Anthropic Messages AST converter
│       ├── openai.py         # OpenAI Chat Completion AST converter
│       └── gemini.py         # Gemini GenerateContent AST converter
├── graph/
│   ├── __init__.py
│   ├── turn_tree.py          # Session DAG & Turn lineage tracker
│   ├── hasher.py             # SHA-256 block content hasher
│   └── diff.py               # Turn-over-turn AST diffing engine
├── analyzer/
│   ├── __init__.py
│   ├── engine.py             # Rule runner orchestrator
│   ├── scorer.py             # Pollution Score (0-100) calculator
│   ├── heuristics/
│   │   ├── ctx001_stale_tool.py
│   │   ├── ctx002_schema_bloat.py
│   │   ├── ctx003_error_loop.py
│   │   ├── ctx004_file_redundancy.py
│   │   └── cache001_prefix_break.py
│   └── cost/
│       ├── pricing_table.py  # Model rate catalog ($/1k tokens)
│       └── cost_model.py     # Attribution & savings math
├── store/
│   ├── __init__.py
│   ├── session_store.py      # Thread-safe in-memory session registry
│   └── jsonc_exporter.py     # .jsonc serializer & schema validator
└── presentation/
    ├── __init__.py
    ├── broadcaster.py        # Event bus for TUI & WebSockets
    └── tui/                  # Terminal UI dashboard
```

---

## 2. Canonical AST & Data Classes

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set
import hashlib
import time

class BlockType(str, Enum):
    SYSTEM = "system"
    TOOL_DEF = "tool_def"
    USER_MSG = "user_msg"
    ASSISTANT_MSG = "assistant_msg"
    TOOL_RESULT = "tool_result"
    INJECTED_CONTEXT = "injected_context"

class ViolationSeverity(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"

@dataclass(slots=True)
class ContextBlock:
    block_id: str
    block_type: BlockType
    content_hash: str
    token_count: int
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # Lineage tracking
    first_seen_turn: int = 0
    turns_survived: int = 0

@dataclass(slots=True)
class CanonicalTurn:
    turn_id: str
    correlation_id: str
    session_id: str
    turn_index: int
    timestamp: float
    provider: str
    model: str
    
    # Context Tree
    system_blocks: List[ContextBlock]
    tool_defs: List[ContextBlock]
    conversation_history: List[ContextBlock]
    tool_results: List[ContextBlock]
    assistant_blocks: List[ContextBlock]
    
    # Raw & Normalized Usage
    input_tokens: int
    output_tokens: int
    cached_read_tokens: int
    cached_created_tokens: int
    duration_ms: float
    ttft_ms: Optional[float]

    # Analysis Results
    violations: List["RuleViolation"] = field(default_factory=list)
    turn_cost_usd: float = 0.0
    wasted_cost_usd: float = 0.0

@dataclass(slots=True)
class RuleViolation:
    rule_id: str
    severity: ViolationSeverity
    title: str
    message: str
    estimated_waste_usd: float
    suggested_fix: str
    block_ids: List[str] = field(default_factory=list)
```

---

## 3. Detailed Component Implementations

### Component 1: `UDSFrameServer` (IPC Receiver)

```python
import asyncio
import struct
import json

class UDSFrameServer:
    """Async Unix Domain Socket server reading 4-byte length-prefixed frames."""
    
    def __init__(self, socket_path: str, on_turn_callback):
        self.socket_path = socket_path
        self.on_turn_callback = on_turn_callback
        self.server: Optional[asyncio.Server] = None

    async def start(self):
        self.server = await asyncio.start_unix_server(
            self._handle_client, path=self.socket_path
        )

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        while True:
            try:
                # Read 4-byte big-endian length prefix
                header = await reader.readexactly(4)
                length = struct.unpack(">I", header)[0]
                
                # Read payload of exact length
                payload_bytes = await reader.readexactly(length)
                payload = json.loads(payload_bytes.decode("utf-8"))
                
                # Dispatch turn to Core Engine Pipeline
                await self.on_turn_callback(payload)
            except (asyncio.IncompleteReadError, ConnectionResetError):
                break
        writer.close()
        await writer.wait_closed()
```

---

### Component 2: `AnthropicASTNormalizer`

Converts Anthropic wire payloads into Canonical AST turns:

```python
class AnthropicASTNormalizer:
    
    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    def normalize(self, raw_turn: Dict[str, Any], turn_index: int) -> CanonicalTurn:
        req = raw_turn.get("requestPayload", {})
        resp = raw_turn.get("responsePayload", {})
        usage = raw_turn.get("usage", {})
        timing = raw_turn.get("timing", {})

        system_blocks = []
        raw_system = req.get("system", "")
        if isinstance(raw_system, str) and raw_system:
            system_blocks.append(ContextBlock(
                block_id=f"sys_0",
                block_type=BlockType.SYSTEM,
                content_hash=self._hash(raw_system),
                token_count=len(raw_system) // 4, # Fallback estimate if tokenizer not injected
                content=raw_system
            ))

        tool_defs = []
        for i, tool in enumerate(req.get("tools", [])):
            schema_str = json.dumps(tool, sort_keys=True)
            tool_defs.append(ContextBlock(
                block_id=f"tool_def_{tool.get('name')}",
                block_type=BlockType.TOOL_DEF,
                content_hash=self._hash(schema_str),
                token_count=len(schema_str) // 4,
                content=schema_str,
                metadata={"name": tool.get("name")}
            ))

        history = []
        tool_results = []
        for msg_idx, msg in enumerate(req.get("messages", [])):
            role = msg.get("role")
            content = msg.get("content")
            if isinstance(content, list):
                for part in content:
                    if part.get("type") == "tool_result":
                        res_str = json.dumps(part.get("content", ""))
                        tool_results.append(ContextBlock(
                            block_id=f"tool_res_{part.get('tool_use_id')}",
                            block_type=BlockType.TOOL_RESULT,
                            content_hash=self._hash(res_str),
                            token_count=len(res_str) // 4,
                            content=res_str,
                            metadata={"tool_use_id": part.get("tool_use_id"), "is_error": part.get("is_error", False)}
                        ))
                    elif part.get("type") == "text":
                        history.append(ContextBlock(
                            block_id=f"hist_{msg_idx}",
                            block_type=BlockType.USER_MSG if role == "user" else BlockType.ASSISTANT_MSG,
                            content_hash=self._hash(part.get("text", "")),
                            token_count=len(part.get("text", "")) // 4,
                            content=part.get("text", "")
                        ))

        assistant_blocks = []
        for blk in resp.get("content", []):
            assistant_blocks.append(ContextBlock(
                block_id=f"resp_{blk.get('type')}",
                block_type=BlockType.ASSISTANT_MSG,
                content_hash=self._hash(str(blk)),
                token_count=len(str(blk)) // 4,
                content=json.dumps(blk)
            ))

        return CanonicalTurn(
            turn_id=raw_turn.get("correlationId", f"turn_{turn_index}"),
            correlation_id=raw_turn.get("correlationId", ""),
            session_id=raw_turn.get("sessionId", "sess_default"),
            turn_index=turn_index,
            timestamp=raw_turn.get("timestamp", time.time()),
            provider=raw_turn.get("provider", "anthropic"),
            model=req.get("model", "unknown"),
            system_blocks=system_blocks,
            tool_defs=tool_defs,
            conversation_history=history,
            tool_results=tool_results,
            assistant_blocks=assistant_blocks,
            input_tokens=usage.get("inputTokens", 0),
            output_tokens=usage.get("outputTokens", 0),
            cached_read_tokens=usage.get("cacheReadInputTokens", 0),
            cached_created_tokens=usage.get("cacheCreationInputTokens", 0),
            duration_ms=timing.get("durationMs", 0.0),
            ttft_ms=timing.get("firstByteReceivedMs", 0) - timing.get("requestDispatchedMs", 0) if timing.get("firstByteReceivedMs") else None
        )
```

---

### Component 3: Heuristics & Pollution Detection Engine

```python
class PollutionAnalyzer:
    """Executes deterministic rules over the turn context lineage."""

    def analyze_turn(self, turn: CanonicalTurn, previous_turns: List[CanonicalTurn]) -> List[RuleViolation]:
        violations = []
        
        # Rule CTX-001: Stale Tool Output Bloat
        violations.extend(self._check_ctx_001(turn, previous_turns))
        
        # Rule CTX-002: Schema Overweight
        violations.extend(self._check_ctx_002(turn, previous_turns))
        
        # Rule CTX-003: Repetitive Error Loops
        violations.extend(self._check_ctx_003(turn, previous_turns))
        
        # Rule CACHE-001: Cache Prefix Break
        violations.extend(self._check_cache_001(turn, previous_turns))

        return violations

    def _check_ctx_001(self, turn: CanonicalTurn, previous_turns: List[CanonicalTurn]) -> List[RuleViolation]:
        """Detects tool outputs > 3,000 tokens lingering for >= 3 turns without reference."""
        violations = []
        if len(previous_turns) < 3:
            return violations

        # Check tool results carried from 3 or more turns ago
        for tool_res in turn.tool_results:
            if tool_res.token_count < 3000:
                continue

            # Check if this tool result existed 3 turns ago
            older_turn = previous_turns[-3]
            existed_in_past = any(t.content_hash == tool_res.content_hash for t in older_turn.tool_results)
            
            if existed_in_past:
                # Check if recent assistant responses referenced any identifiers from the payload
                recent_assistant_text = " ".join(
                    blk.content for prev in previous_turns[-2:] for blk in prev.assistant_blocks
                )
                tool_id = tool_res.metadata.get("tool_use_id", "")
                if tool_id not in recent_assistant_text:
                    waste_cost = (tool_res.token_count / 1000.0) * 0.003  # Approximate input cost
                    violations.append(RuleViolation(
                        rule_id="CTX-001",
                        severity=ViolationSeverity.WARN,
                        title="Stale Tool Output Bloat",
                        message=f"Tool result ({tool_res.token_count} tokens) has remained in context for >= 3 turns without reference.",
                        estimated_waste_usd=waste_cost,
                        suggested_fix="Prune older tool payloads or truncate large responses.",
                        block_ids=[tool_res.block_id]
                    ))
        return violations

    def _check_ctx_002(self, turn: CanonicalTurn, previous_turns: List[CanonicalTurn]) -> List[RuleViolation]:
        """Flags tool schemas consuming > 35% of input tokens when < 15% are called."""
        violations = []
        total_tool_def_tokens = sum(td.token_count for td in turn.tool_defs)
        if turn.input_tokens > 0 and (total_tool_def_tokens / turn.input_tokens) > 0.35:
            # Check invocation ratio across session
            called_tools = set()
            for t in previous_turns + [turn]:
                for res in t.tool_results:
                    called_tools.add(res.metadata.get("name"))
            
            total_tools = len(turn.tool_defs)
            if total_tools > 5 and (len(called_tools) / total_tools) < 0.15:
                violations.append(RuleViolation(
                    rule_id="CTX-002",
                    severity=ViolationSeverity.WARN,
                    title="Tool Schema Overweight",
                    message=f"Tool schemas occupy {total_tool_def_tokens} tokens ({round(total_tool_def_tokens*100/turn.input_tokens)}% of input), but only {len(called_tools)}/{total_tools} tools have been used.",
                    estimated_waste_usd=(total_tool_def_tokens / 1000.0) * 0.003,
                    suggested_fix="Group tools into subagents or filter tool schemas dynamically."
                ))
        return violations

    def _check_ctx_003(self, turn: CanonicalTurn, previous_turns: List[CanonicalTurn]) -> List[RuleViolation]:
        """Detects consecutive repeated errors."""
        violations = []
        if len(previous_turns) < 2:
            return violations
        
        all_recent = previous_turns[-2:] + [turn]
        error_count = sum(1 for t in all_recent if any(r.metadata.get("is_error") for r in t.tool_results))
        if error_count == 3:
            violations.append(RuleViolation(
                rule_id="CTX-003",
                severity=ViolationSeverity.CRITICAL,
                title="Agent Error Loop Detected",
                message="Tool execution errors occurred across 3 consecutive turns without resolution.",
                estimated_waste_usd=0.015,
                suggested_fix="Inject steering prompt or terminate agent execution loop."
            ))
        return violations

    def _check_cache_001(self, turn: CanonicalTurn, previous_turns: List[CanonicalTurn]) -> List[RuleViolation]:
        """Detects prompt cache invalidation due to dynamic prefixes."""
        violations = []
        if not previous_turns:
            return violations
        
        last_turn = previous_turns[-1]
        # Check if system prompt hash changed while total length remained nearly identical (e.g. timestamp shift)
        if last_turn.system_blocks and turn.system_blocks:
            last_h = last_turn.system_blocks[0].content_hash
            curr_h = turn.system_blocks[0].content_hash
            if last_h != curr_h and abs(last_turn.system_blocks[0].token_count - turn.system_blocks[0].token_count) < 10:
                violations.append(RuleViolation(
                    rule_id="CACHE-001",
                    severity=ViolationSeverity.CRITICAL,
                    title="Prompt Cache Prefix Invalidation",
                    message="System prompt prefix was modified, breaking cache reuse for all downstream tokens.",
                    estimated_waste_usd=(turn.input_tokens / 1000.0) * 0.003 * 0.9, # 90% discount missed
                    suggested_fix="Move dynamic elements (timestamps, random IDs) to the end of the prompt."
                ))
        return violations
```

---

### Component 4: Pollution Scorer & Cost Attributor

```python
class PollutionScorer:
    """Calculates normalized 0-100 score and financial loss metrics."""
    
    @staticmethod
    def calculate_score(turns: List[CanonicalTurn]) -> float:
        if not turns:
            return 0.0
        
        penalty = 0.0
        for turn in turns:
            for v in turn.violations:
                if v.severity == ViolationSeverity.INFO:
                    penalty += 2.0
                elif v.severity == ViolationSeverity.WARN:
                    penalty += 8.0
                elif v.severity == ViolationSeverity.CRITICAL:
                    penalty += 20.0
                    
        # Normalize across total turn count: 0 (clean) to 100 (critical waste)
        score = (penalty / (len(turns) * 20.0)) * 100.0
        return min(100.0, round(score, 1))
```

---

## 4. In-Memory Session Store

```python
import threading

class SessionStore:
    """Thread-safe storage for active sessions, indexing turns, metrics, and violations."""
    
    def __init__(self, max_sessions: int = 100):
        self.max_sessions = max_sessions
        self.sessions: Dict[str, List[CanonicalTurn]] = {}
        self.lock = threading.RLock()

    def append_turn(self, turn: CanonicalTurn):
        with self.lock:
            if turn.session_id not in self.sessions:
                if len(self.sessions) >= self.max_sessions:
                    # Evict oldest session
                    oldest_id = next(iter(self.sessions))
                    del self.sessions[oldest_id]
                self.sessions[turn.session_id] = []
            self.sessions[turn.session_id].append(turn)

    def get_session(self, session_id: str) -> Optional[List[CanonicalTurn]]:
        with self.lock:
            return list(self.sessions.get(session_id, []))
```

---

## 5. Presentation Layer & Real-Time Event Dispatch

The presentation layer consumes data from `SessionStore` and broadcasts real-time telemetry updates to terminal TUIs and web dashboards.

- **High-Level UI Design & Wireframes**: [docs/design-ui-dashboards.md](design-ui-dashboards.md)
- **Presentation Low-Level Design (LLD)**: [docs/lld-presentation.md](lld-presentation.md)
- **Key Modules**:
  - `src/presentation/broadcaster.py`: Thread-safe, non-blocking pub/sub event bus decoupling ingestion from rendering.
  - `src/presentation/tui/`: Textual interactive terminal dashboard (`CtxinsTUIApp`).
  - `src/presentation/web/`: FastAPI ASGI application, WebSocket live streaming hub (`/ws/live`), and embedded single-page dashboard.

