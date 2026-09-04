# Development, Testing & Contribution Guide

This document provides setup, testing, and contribution instructions for developers working on `ctxins`.

---

## 1. Prerequisites & Environment Setup

`ctxins` requires **Python 3.11+** and uses [`uv`](https://docs.astral.sh/uv/) for deterministic dependency management.

```bash
# 1. Clone the repository
git clone https://github.com/arnabkaycee/ctxins.git
cd ctxins

# 2. Create virtual environment and install all dependencies (including dev tools)
uv sync --extra dev
```

---

## 2. Testing Suite

The `ctxins` test suite verifies network interception, streaming token reconstruction, Unix Domain Socket (UDS) IPC transport, and context analysis algorithms without connecting to external LLM APIs.

### Running Tests

```bash
# Run all tests (221 tests)
uv run pytest

# Run with verbose output and short failure traces
uv run pytest -v -ra

# Run specific test suites:
uv run pytest tests/unit/interceptor/        # Interceptor unit tests (router, sanitizer, tap, accumulators)
uv run pytest tests/unit/core/               # Core engine unit tests (normalizers, heuristics, graph)
uv run pytest tests/presentation/           # Presentation tests (broadcaster, TUI, Web API, E2E UI)
uv run pytest tests/integration/            # UDS IPC transport & socket resilience tests
uv run pytest tests/e2e/                    # End-to-end multi-turn agent simulation tests
```

### Mock LLM Streaming Server
Integration and E2E tests utilize the lightweight mock LLM streaming server in [`tests/mocks/mock_llm_server.py`](../tests/mocks/mock_llm_server.py). It serves deterministic Server-Sent Events (SSE) for Anthropic, OpenAI, and Google Gemini endpoints without external network requests or API keys.

---

## 3. Quality Gates & Linting

Before submitting code, ensure all linters and typecheckers pass cleanly:

```bash
# Check code style with ruff
uv run ruff check .

# Automatically apply formatting and import fixes
uv run ruff check --fix .
uv run ruff format .

# Strict static type checking
uv run mypy src tests
```

---

## 4. Project Layout

```text
ctxins/
├── src/
│   ├── cli.py               # Unified CLI runner (tui, web, live, run)
│   ├── schema/              # Shared wire envelopes and canonical AST dataclasses
│   │   ├── wire.py          # Provider enums, WireEnvelope, TimingMetrics, UsageMetrics
│   │   └── ast.py           # CanonicalTurn, ContextBlock, RuleViolation, TurnDelta
│   │
│   ├── interceptor/         # Network interception (mitmproxy addon)
│   │   ├── addon.py         # CtxinsAddon entrypoint and mitmproxy lifecycle hooks
│   │   ├── filter/          # ProviderRouter and Header/Payload Sanitizers
│   │   ├── stream/          # Zero-delay StreamPassthrough, SSE parser, and accumulators
│   │   ├── correlation/     # ActiveTurnTracker and TTL reaper
│   │   └── egress/          # BoundedRingBuffer, 4-byte framer, and non-blocking UDSClient
│   │
│   ├── core/                # Core context analysis and rule engine
│   │   ├── server/          # Asynchronous UDSFrameServer and FrameDecoder
│   │   ├── ast/             # Canonical AST normalizers (Anthropic, OpenAI, Gemini)
│   │   ├── graph/           # ContextGraph DAG, SHA-256 Hasher, and TurnDiffEngine
│   │   ├── analyzer/        # PollutionAnalyzer, PollutionScorer, and heuristics (CTX-001..003, CACHE-001)
│   │   └── store/           # Thread-safe SessionStore and JsoncExporter
│   │
│   └── presentation/        # Real-time presentation subsystem
│       ├── broadcaster.py   # Async pub/sub event bus & fan-out engine
│       ├── events.py        # Presentation UIEvent models & serializable payloads
│       ├── tui/             # Textual terminal UI application, state, and widgets
│       └── web/             # FastAPI REST API, WebSocket hub, and zero-build SPA
│
├── tests/
│   ├── unit/                # Component-level unit tests
│   ├── presentation/        # Presentation broadcaster, TUI, and Web API tests
│   ├── integration/         # IPC socket stress tests, disconnect/reconnect tests
│   ├── e2e/                 # Full multi-turn agent pipeline tests
│   └── mocks/               # In-process mock LLM streaming server
│
└── docs/                    # Architecture designs, LLDs, and integration guides
```

---

## 5. Issue Tracking & Workflow with `bd` (Beads)

This project uses **`bd` (Beads)** for distributed issue tracking.

```bash
# Check for unblocked ready work
bd ready --json

# Create a new issue
bd create "Issue title" --description="Details" -t bug|feature|task -p 1 --json

# Claim work atomically
bd update <id> --claim --json

# Complete work
bd close <id> --reason "Completed" --json
```

---

## 6. Contribution & Landing Procedure

When completing a task or PR:
1. Ensure all unit, integration, and E2E tests pass (`uv run pytest`).
2. Verify zero linter and typecheck issues (`uv run ruff check .` and `uv run mypy src tests`).
3. Update relevant documentation in [`docs/`](../docs/).
4. Rebase against `origin/main` and verify a clean working tree (`git status`).
