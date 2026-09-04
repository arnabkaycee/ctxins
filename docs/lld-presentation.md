# Low-Level Design (LLD): Presentation Layer (TUI & Web Dashboard)

This document provides the concrete low-level architectural specification, class designs, WebSocket wire protocols, and component layouts for the `ctxins` presentation layer in `src/presentation/`.

---

## 1. Package Layout

```text
src/presentation/
├── __init__.py
├── broadcaster.py               # Async pub/sub event bus & fan-out engine
├── events.py                    # Presentation UI event models & serializable payloads
├── tui/
│   ├── __init__.py
│   ├── app.py                   # Textual Application root (CtxinsTUIApp)
│   ├── theme.py                 # ANSI color schemes, styles, and typography
│   ├── state.py                 # Reactive UI state container
│   └── widgets/
│       ├── __init__.py
│       ├── header_bar.py        # Top session KPI ribbon
│       ├── turn_timeline.py     # Left turns navigator & streaming pulse
│       ├── context_breakdown.py # Center context block composition & AST tree
│       ├── recommendations.py   # Right real-time rule violations & fixes
│       └── footer_bar.py        # Bottom hotkey navigation reference
└── web/
    ├── __init__.py
    ├── server.py                # FastAPI / Starlette ASGI application factory
    ├── api.py                   # REST endpoints (/api/v1/sessions, /turns, /export)
    ├── ws.py                    # WebSocket hub & client connection manager
    └── static/
        ├── index.html           # Embedded single-page dashboard HTML
        ├── css/
        │   └── styles.css       # Tailwind utility classes & theme overrides
        └── js/
            ├── app.js           # Vue / Alpine / Vanilla reactive client
            ├── charts.js        # Chart.js / ECharts visualizers
            └── ws_client.js     # Auto-reconnecting WebSocket consumer
```

---

## 2. Event Bus & Messaging Protocols

### 2.1 UI Event Models (`src/presentation/events.py`)

All presentation updates are mediated by strongly typed event envelopes.

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
import time

class UIEventType(str, Enum):
    SESSION_CREATED = "session_created"
    TURN_STARTED = "turn_started"
    TURN_STREAMING = "turn_streaming"
    TURN_COMPLETED = "turn_completed"
    VIOLATION_DETECTED = "violation_detected"
    SESSION_SUMMARY_UPDATED = "session_summary_updated"
    SESSION_ENDED = "session_ended"

@dataclass(slots=True)
class UIEvent:
    event_type: UIEventType
    session_id: str
    timestamp: float = field(default_factory=time.time)
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.event_type.value,
            "sessionId": self.session_id,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }
```

### 2.2 Event Broadcaster (`src/presentation/broadcaster.py`)

The `PresentationBroadcaster` decouples telemetry ingestion from presentation rendering:

```python
import asyncio
import logging
from typing import Set

logger = logging.getLogger(__name__)

class PresentationBroadcaster:
    """Thread-safe, non-blocking pub/sub event bus with bounded subscriber queues."""

    def __init__(self, queue_capacity: int = 100) -> None:
        self.queue_capacity = queue_capacity
        self._subscribers: Set[asyncio.Queue[UIEvent]] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[UIEvent]:
        queue: asyncio.Queue[UIEvent] = asyncio.Queue(maxsize=self.queue_capacity)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[UIEvent]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    def publish_nowait(self, event: UIEvent) -> None:
        """Non-blocking publish. Drops events for slow subscribers to preserve fail-open safety."""
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Drop frame for laggy subscriber to prevent Core Engine backpressure
                logger.debug("Subscriber queue full; dropping presentation event %s", event.event_type)
```

---

## 3. Terminal UI (TUI) Architecture

### 3.1 Textual Application Structure (`src/presentation/tui/app.py`)

```python
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Footer, Header

from src.presentation.tui.state import TUIState
from src.presentation.tui.widgets.header_bar import HeaderBarWidget
from src.presentation.tui.widgets.turn_timeline import TurnTimelineWidget
from src.presentation.tui.widgets.context_breakdown import ContextBreakdownWidget
from src.presentation.tui.widgets.recommendations import RecommendationsWidget

class CtxinsTUIApp(App):
    """Primary Textual application for interactive terminal context inspection."""

    CSS = """
    Screen {
        layout: vertical;
        background: #0d1117;
        color: #c9d1d9;
    }
    #main-container {
        height: 1fr;
        layout: horizontal;
    }
    #timeline-pane {
        width: 25%;
        border-right: solid #30363d;
    }
    #breakdown-pane {
        width: 45%;
        border-right: solid #30363d;
    }
    #recommendations-pane {
        width: 30%;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("tab", "focus_next", "Next Pane"),
        ("shift+tab", "focus_previous", "Prev Pane"),
        ("r", "toggle_rule_filter", "Filter Violations"),
        ("e", "export_jsonc", "Export .jsonc"),
    ]

    selected_turn_index: reactive[int] = reactive(0)

    def __init__(self, state: TUIState, broadcaster: PresentationBroadcaster) -> None:
        super().__init__()
        self.state = state
        self.broadcaster = broadcaster

    def compose(self) -> ComposeResult:
        yield HeaderBarWidget(self.state)
        with Horizontal(id="main-container"):
            yield TurnTimelineWidget(self.state, id="timeline-pane")
            yield ContextBreakdownWidget(self.state, id="breakdown-pane")
            yield RecommendationsWidget(self.state, id="recommendations-pane")
        yield Footer()

    async def on_mount(self) -> None:
        """Start listening for real-time events on mount."""
        self.run_worker(self._listen_events(), exclusive=False)

    async def _listen_events(self) -> None:
        queue = await self.broadcaster.subscribe()
        try:
            while True:
                event = await queue.get()
                self._handle_ui_event(event)
                queue.task_done()
        finally:
            await self.broadcaster.unsubscribe(queue)

    def _handle_ui_event(self, event: UIEvent) -> None:
        self.state.apply_event(event)
        self.refresh()
```

### 3.2 TUI Widgets Implementation Specs

1. **`HeaderBarWidget`**:
   - Renders a 3-row compact summary:
     - Row 1: `Session ID`, `Agent Harness`, `LLM Provider & Model`, `Active Turn Status (● Idle / Streaming)`.
     - Row 2: Aggregate Metrics: Total Tokens, Prompt Cache Read % (`cacheHitRatio`), Total Spend ($), Wasted Spend ($).
     - Row 3: Visual Pollution Meter (0 to 100 colored bar using ANSI 256 colors: Green `<20`, Yellow `20-50`, Red `>50`).

2. **`TurnTimelineWidget`**:
   - Displays a scrollable list of turns using Textual's `ListView` or `DataTable`.
   - Turn status flags: `● Turn #4 [streaming 1.2s]`, `✓ Turn #3 (48.2k tok, $0.048)`, `⚠ Turn #2 (2 violations)`.
   - Emits `TurnSelected(turn_index)` message when navigation keys (`j`, `k`, `Up`, `Down`) are pressed.

3. **`ContextBreakdownWidget`**:
   - Updates reactively when `selected_turn_index` changes.
   - Renders an ASCII proportional stacked horizontal bar:
     `[System: 2.1%] [Tools: 2.8%] [History: 3.8%] [Results: 17.2%] [Cache: 73.6%]`
   - Renders a tree of context blocks categorized by `BlockType` (`system`, `tool_def`, `user_msg`, `tool_result`, `assistant_msg`).
   - Detailed inspector for highlighted block: Block ID, Content Hash, Token Count, Survival Turns, and payload preview.

4. **`RecommendationsWidget`**:
   - Displays all active `RuleViolation` instances associated with the selected turn and cumulative session.
   - Severity badges: `[CRITICAL]` (Red), `[WARN]` (Yellow), `[INFO]` (Blue).
   - Card contents:
     - Title: e.g. `CTX-001: Stale Tool Output Bloat`
     - Impact: `14,500 tokens ($0.0435 waste)`
     - Block Reference: `blk_tool_res_392 (run_shell)`
     - Suggested Fix: `Prune tool outputs older than 2 turns from conversation history.`

---

## 4. Web Dashboard Server & REST/WebSocket APIs

### 4.1 FastAPI Application Factory (`src/presentation/web/server.py`)

```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from src.core.store.session_store import SessionStore
from src.presentation.broadcaster import PresentationBroadcaster
from src.presentation.web.api import create_api_router
from src.presentation.web.ws import WebSocketHub

def create_app(store: SessionStore, broadcaster: PresentationBroadcaster) -> FastAPI:
    app = FastAPI(title="ctxins Dashboard", version="0.1.0")
    
    # WebSocket Connection Hub
    ws_hub = WebSocketHub(broadcaster=broadcaster)
    
    # Register REST API router
    api_router = create_api_router(store=store, ws_hub=ws_hub)
    app.include_router(api_router, prefix="/api/v1")
    
    # Mount WebSocket endpoint
    @app.websocket("/ws/live")
    async def live_websocket(websocket: WebSocket):
        await ws_hub.handle_client(websocket)

    # Mount static dashboard assets
    static_dir = Path(__file__).parent / "static"
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app
```

### 4.2 REST API Endpoints Contract (`src/presentation/web/api.py`)

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/v1/sessions` | List all active sessions with summary metrics |
| `GET` | `/api/v1/sessions/{id}` | Get complete session details, summary, and turn indexes |
| `GET` | `/api/v1/sessions/{id}/turns` | Get all `CanonicalTurn` objects for a session |
| `GET` | `/api/v1/sessions/{id}/turns/{index}` | Get single turn details and AST blocks |
| `GET` | `/api/v1/sessions/{id}/recommendations` | Get all triggered `RuleViolation` items and suggested fixes |
| `GET` | `/api/v1/sessions/{id}/diff/{t1}/{t2}` | Compute AST block diff and token drift between two turns |
| `GET` | `/api/v1/sessions/{id}/export` | Export session adhering to canonical `.jsonc` schema |

### 4.3 WebSocket Streaming Protocol (`/ws/live`)

Clients connect via `ws://localhost:8484/ws/live?session_id=sess_01j7abc991`.

Upon connection, the server sends an initial snapshot:
```jsonc
{
  "type": "SNAPSHOT",
  "sessionId": "sess_01j7abc991",
  "summary": { ... },
  "turns": [ ... ],
  "violations": [ ... ]
}
```

Subsequent live events are pushed in real-time as JSON envelopes:
- `TURN_STREAMING`: Live generation pulse (`deltaTokens`, `ttftMs`, `streamDurationMs`).
- `TURN_COMPLETED`: New turn added with complete token breakdown, delta AST, and cost calculations.
- `VIOLATION_DETECTED`: New recommendation alert triggered by heuristics.

---

## 5. Web Dashboard Frontend Architecture

### 5.1 Zero-Build Static Dashboard (`src/presentation/web/static/`)
The web frontend is delivered as a zero-build Single Page Application to ensure instant startup without requiring `node`, `npm`, or compilation steps.

1. **`index.html`**:
   - Clean, modern layout structured into 4 primary views:
     - **Header**: Session Switcher, Live Connection Badge, JSONC Export Button.
     - **KPI Banner**: Metric Cards (Tokens, Cache Hit %, Total Spend, Avoidable Spend, Pollution Score Gauge).
     - **Visualization Grid**:
       - Left: Turn-by-turn Stacked Token Composition & Cache Performance (Chart.js / ECharts).
       - Right: Live Recommendations & Optimization Checklist.
     - **Turn Inspector**: Block-level AST breakdown, token weights, and turn diff viewer.
2. **`js/ws_client.js`**:
   - Manages WebSocket lifecycle with auto-reconnect (`1s`, `2s`, `5s` exponential backoff).
   - Re-syncs session snapshot via REST API `/api/v1/sessions/{id}` if connection drops.
3. **`js/charts.js`**:
   - Responsive Chart.js configurations:
     - Stacked Bar Chart: Token composition over turns (System, Tools, Chat History, Tool Results, Thinking, Output).
     - Cache Hit Ratio Line Chart with cache invalidation break markers.
     - Interactive Click Handler: Clicking a bar in the chart automatically selects that turn in the Turn Inspector.

---

## 6. Testing Strategy for Presentation Layer

| Test Category | Target Component | Test Verification |
| :--- | :--- | :--- |
| **Unit Tests** | `PresentationBroadcaster` | Verify async subscribe, fan-out dispatch, and non-blocking drop behavior under queue saturation. |
| **Unit Tests** | `UIEvent` & Serialization | Verify conversion between `CanonicalTurn`, `RuleViolation`, and JSON wire dictionaries. |
| **TUI Component Tests** | `CtxinsTUIApp` & Widgets | Use Textual's async `run_test()` pilot to verify keybindings, tab switching, and turn navigation. |
| **API Integration Tests** | `FastAPI` REST Endpoints | Use `httpx.AsyncClient` to test `/api/v1/sessions`, `/turns`, and `.jsonc` export endpoints. |
| **WebSocket Tests** | `/ws/live` Endpoint | Verify initial `SNAPSHOT` delivery and sequential `TURN_COMPLETED` broadcasts over test websockets. |
