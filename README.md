# ctxins: Context Inspector & Optimizer for Agentic Harnesses

[![CI](https://img.shields.io/badge/tests-185%20passed-brightgreen.svg)](docs/development.md#2-testing-suite)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://python.org)
[![Type Checked](https://img.shields.io/badge/typecheck-mypy%20clean-blue.svg)](docs/development.md#3-quality-gates--linting)
[![Linter](https://img.shields.io/badge/lint-ruff%20clean-blue.svg)](docs/development.md#3-quality-gates--linting)
[![License](https://img.shields.io/badge/license-MIT-purple.svg)](LICENSE)

`ctxins` is an open-source context inspector and optimization engine for agentic coding harnesses (Antigravity `agy`, Claude Code, Aider, OpenCode, Pi, AutoGen, CrewAI, and custom loops). It provides real-time visibility into context composition, token consumption, context pollution, and prompt cache utilization with **zero agent code modifications** and **zero token delivery delay**.

---

## ⚡ How It Works

```mermaid
flowchart LR
    Agent["🤖 Agent Harness\n(Antigravity, Claude Code, Aider, AutoGen)"]
    Proxy["⚡ ctxins Proxy\n(mitmproxy Addon)"]
    LLM["☁️ LLM Provider API\n(Anthropic / OpenAI / Gemini)"]
    Core["🧠 ctxins Core Engine\n(Context Graph & Rule Engine)"]

    Agent <-->|"Zero-Delay Streaming Tap"| Proxy
    Proxy <-->|"HTTPS"| LLM
    Proxy -.->|"Async IPC (Unix Socket)"| Core
```

1. **Passive Stream Tap:** Runs as a transparent `mitmproxy` addon, intercepting LLM requests and streaming tokens downstream with zero buffering delay.
2. **Context DAG & Pollution Analysis:** Ships framed telemetry over a non-blocking Unix Domain Socket to the Core Engine, tracking block lineage, prompt cache invalidations, and stale tool outputs.
3. **Fail-Open Safety:** Ring buffers bound memory to ~10MB and safely drop frames under load so your agent's work is never blocked or interrupted.

---

## 📋 Prerequisites

- **Operating System:** macOS or Linux (utilizes Unix Domain Sockets for high-throughput IPC).
- **Python:** Version **3.11+**.
- **Package Manager:** [`uv`](https://docs.astral.sh/uv/) (strongly recommended) or `pip`.

---

## 🚀 Quick Start

### 1. Start the Core Engine & Proxy
Install dependencies and launch the interceptor pipeline:

```bash
git clone https://github.com/arnabkaycee/ctxins.git && cd ctxins
uv sync --extra dev

# 1. Start the Core Frame Server (in background)
CTXINS_SOCKET_PATH=/tmp/ctxins.sock uv run python -m src.core.server.uds_server &

# 2. Start the Interceptor Proxy (headless or with web/TUI dashboard)
CTXINS_SOCKET_PATH=/tmp/ctxins.sock uv run mitmdump -p 8080 -s src/interceptor/addon.py
```

### 2. Run Any Agent Harness
Execute your agent with standard proxy environment variables or using the [`with-ctxins` helper](docs/harness-guides.md#2-universal-helper-function-with-ctxins):

```bash
# Antigravity (agy)
with-ctxins agy

# Claude Code
with-ctxins claude

# Aider
with-ctxins aider
```

> **Tip:** You can also pass environment variables inline, e.g. `HTTP_PROXY="http://127.0.0.1:8080" HTTPS_PROXY="http://127.0.0.1:8080" SSL_CERT_FILE="$HOME/.mitmproxy/mitmproxy-ca-cert.pem" agy`. See [Harness Integration Guides](docs/harness-guides.md) for harness-specific details.

---

## 📊 Real-Time UIs & Captured Metrics

`ctxins` provides purpose-built real-time user interfaces to monitor context dynamics, cache performance, and optimization recommendations:

### 1. Interactive Terminal UI (TUI)
Designed to run side-by-side with your agent harness in split terminals or `tmux`:
```bash
# Launch interactive TUI attached to the active Core Engine
uv run python -m src.presentation.tui.app
```
```text
┌─────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ ctxins v0.1.0 │ Session: sess_01j7abc991 (claude-3-5-sonnet) │ Status: ● STREAMING (Turn #4) │ Q: Quit │
├─────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ TOTAL TOKENS: 84.2k  │ CACHE HIT: 73.6% (62.0k) │ SPEND: $0.284 │ WASTED: $0.092 │ POLLUTION: 14.2/100  │
├──────────────────────────────┬────────────────────────────────────────────┬─────────────────────────────┤
│ [1] TURNS & TIMELINE         │ [2] CONTEXT COMPOSITION (TURN #3)          │ [3] RECOMMENDATIONS & ALERTS│
├──────────────────────────────┼────────────────────────────────────────────┼─────────────────────────────┤
│ ▶ Turn #1 (init)      12.4k  │ System Prompt:   1,800 tok  [2.1%]   ■■     │ ⚠ CTX-001: Stale Tool Out   │
│   Turn #2 (file read) 24.1k  │ Tool Schemas:    2,400 tok  [2.8%]   ■■■    │   Turn #3: file_search res  │
│   Turn #3 (bash run)  48.2k  │ Conversation:    3,200 tok  [3.8%]   ■■■■   │   unreferenced for 3 turns. │
│ ● Turn #4 (streaming) 84.2k  │ Tool Results:   14,500 tok [17.2%]  ■■■■■■ │   Waste: $0.048 (14.5k tok) │
│                              │ Thinking Block:    420 tok  [0.5%]   ■      │   Fix: Prune old output     │
│                              │ Output Tokens:     350 tok  [0.4%]   ■      │ ─────────────────────────── │
│                              │ Cache Read:     62,000 tok [73.6%]  ■■■■■■ │ 🚨 CACHE-001: Prefix Break  │
│                              ├────────────────────────────────────────────┤   System prompt hash shifted│
│                              │ SELECTED BLOCK: tool_result (id: blk_90fa) │   Waste: $0.044             │
│                              │ Tool: run_shell ("find . -name '*.py'")    │   Fix: Move timestamp to end│
│                              │ Size: 14,500 tokens | Survived: 3 turns    │                             │
├──────────────────────────────┴────────────────────────────────────────────┴─────────────────────────────┤
│ [Tab] Switch Pane  [↑/↓] Navigate Turns  [Enter] Inspect Block  [r] Filter Warnings  [e] Export .jsonc  │
└─────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```
👉 *See [docs/design-ui-dashboards.md](docs/design-ui-dashboards.md#4-interactive-terminal-ui-tui-design) and [docs/lld-presentation.md](docs/lld-presentation.md#3-terminal-ui-tui-architecture) for complete TUI design and implementation specifications.*

### 2. Live Web Dashboard & WebSocket Stream
Launch the local web dashboard to view interactive token charts, cache invalidation heatmaps, and recommendation details:
```bash
# Start the web dashboard gateway (default: http://localhost:8484)
uv run python -m src.presentation.web.server --port 8484
```
- **Real-Time WebSocket Sync (`/ws/live`)**: Instant updates as turns complete and tokens stream.
- **Visual Context Sunburst & Treemaps**: Area-weighted breakdowns of prompt composition.
- **Prescriptive Recommendation Cards**: One-click remediation snippets and financial waste estimates for `CTX-001`..`004` and `CACHE-001`.
- **Turn-by-Turn AST Diffing**: Inspect newly injected, persisted, and pruned context blocks.

👉 *See [docs/design-ui-dashboards.md](docs/design-ui-dashboards.md#5-web-dashboard-design) and [docs/lld-presentation.md](docs/lld-presentation.md#4-web-dashboard-server--restwebsocket-apis) for complete web architecture.*

### 3. Session Timeline Exports (`.jsonc`)
The Core Engine exports complete session timelines into annotated `.jsonc` files (conforming to `https://ctxins.dev/schemas/session.v1.json`). Exports contain:
- Turn-by-turn token consumption (input, output, cache creation, cache read).
- Time-To-First-Token (TTFT) and stream durations.
- AST diffs showing added, persisted, and pruned context blocks.
- Triggered rule violations (`CTX-001`..`004`, `CACHE-001`), severity ratings, and estimated USD waste.

### 4. Low-Level Network Inspection (`mitmweb` / `mitmproxy`)
For raw HTTP/2 and SSE chunk debugging, `ctxins` supports mitmproxy's native frontends:
```bash
# Web proxy inspector (http://127.0.0.1:8081)
uv run mitmweb -p 8080 -s src/interceptor/addon.py

# Terminal proxy inspector
uv run mitmproxy -p 8080 -s src/interceptor/addon.py
```

---

## 🔌 Supported Harnesses

| Harness | Guide | Key Capabilities Inspected |
| :--- | :--- | :--- |
| **Claude Code** | [Setup Guide](docs/harness-guides.md#a-claude-code-claude) | Prompt caching hits, thinking blocks, stale `view_file` results |
| **Antigravity (`agy`)** | [Setup Guide](docs/harness-guides.md#b-antigravity-cli-agy) | Gemini SSE streams, multi-turn tool outputs, sub-agent context bloat |
| **Aider** | [Setup Guide](docs/harness-guides.md#c-aider-aider) | Multi-turn file contexts, repo-map overhead, token consumption |
| **OpenCode** | [Setup Guide](docs/harness-guides.md#d-opencode-opencode) | Workspace `.env` proxying, multi-turn diffs |
| **Pi** | [Setup Guide](docs/harness-guides.md#e-pi-pi) | Proxy mode or zero-proxy in-process telemetry hook |
| **AutoGen / AG2** | [Setup Guide](docs/harness-guides.md#f-autogen--ag2-python) | Multi-agent conversation snowballing and unused tool schemas |
| **CrewAI** | [Setup Guide](docs/harness-guides.md#g-crewai-python) | Task output accumulation across sequential and hierarchical crews |
| **LangChain / LangGraph** | [Setup Guide](docs/harness-guides.md#h-langchain--langgraph-python--typescript) | Agent state graph turn lineage and tool retry loops |
| **Custom Loops & SDKs** | [Setup Guide](docs/harness-guides.md#i-custom-agent-loops--raw-sdks) | Python (`anthropic`, `openai`), TypeScript, cURL, and Docker recipes |

---

## 🔍 Context Pollution Heuristics

`ctxins` runs algorithmic heuristics against the session DAG to quantify token waste and calculate financial savings:

- **`CTX-001` (Stale Tool Output Bloat):** Flags unreferenced tool results lingering $\ge 3$ consecutive turns.
- **`CTX-002` (Tool Schema Overweight):** Flags tool schemas consuming $> 35\%$ of context with $< 15\%$ invocation rate.
- **`CTX-003` (Error Loop Thrashing):** Detects $3+$ consecutive turns repeating failing tool executions.
- **`CACHE-001` (Dynamic Prefix Invalidation):** Flags prefix mutations in system prompts breaking prompt cache reuse.

👉 **See [docs/heuristics.md](docs/heuristics.md) for complete mathematical formulas, threshold configurations, and suggested fixes.**

---

## 📚 Documentation & Development

- 💻 **[Development, Testing & Contribution Guide](docs/development.md)**: Running test suites (`pytest`), linters (`ruff`, `mypy`), and contributing.
- 🚀 **[Harness Integration Guides](docs/harness-guides.md)**: Detailed recipes for every agent framework.
- 🔍 **[Heuristics & Pollution Catalog](docs/heuristics.md)**: Rule catalog and composite pollution scoring math.
- 🏗️ **[Detailed Architecture & LLDs](docs/README.md)**: Low-level specifications for Interceptor, Core Engine, and IPC protocol.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
