# ctxins: Context Inspector & Optimizer for Agentic Harnesses

[![CI](https://img.shields.io/badge/tests-367%20passed-brightgreen.svg)](docs/development.md#2-testing-suite)
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
    Proxy["⚡ ctxins Proxy\n(mitmproxy Addon on port 8080)"]
    LLM["☁️ LLM Provider API\n(Anthropic / OpenAI / Gemini)"]
    Core["🧠 ctxins Core Engine\n(Context Graph & Rule Engine)"]
    UI["💻 Web Dashboard & TUI\n(Live WebSocket Sync & Charts)"]

    Agent <-->|"Zero-Delay Streaming Tap"| Proxy
    Proxy <-->|"HTTPS"| LLM
    Proxy -.->|"Async IPC (Unix Socket)"| Core
    Core -.->|"Event Broadcaster"| UI
```

1. **Passive Stream Tap:** Runs as a transparent `mitmproxy` addon, intercepting LLM requests and streaming tokens downstream with zero buffering delay.
2. **Context DAG & Pollution Analysis:** Ships framed telemetry over a non-blocking Unix Domain Socket to the Core Engine, tracking block lineage, prompt cache invalidations, and stale tool outputs.
3. **Automated Interceptor Lifecycle:** `ctxins run`, `ctxins web`, and `ctxins live` automatically spawn and manage `mitmdump` on the configured proxy port (default: `8080`).
4. **Fail-Open Safety:** Ring buffers bound memory to ~10MB and safely drop frames under load so your agent's work is never blocked or interrupted.

---

## 📋 Prerequisites

- **Operating System:** macOS or Linux (utilizes Unix Domain Sockets for high-throughput IPC).
- **Python:** Version **3.11+**.
- **Package Manager:** [`uv`](https://docs.astral.sh/uv/) (strongly recommended) or `pip`.

---

## 🚀 Quick Start

### Option A: Zero-Config Cockpit (`ctxins`)
Launch the interactive Terminal UI and background Web Dashboard together with a single command:

```bash
git clone https://github.com/arnabkaycee/ctxins.git && cd ctxins
uv sync --extra dev

# Starts TUI cockpit in terminal & Web Dashboard on http://localhost:8484
uv run ctxins

# In your agent's terminal, route traffic to ctxins:
eval $(uv run ctxins env)
agy

# When ctxins is stopped or inspection is complete, unset proxy variables:
eval $(uv run ctxins env --unset)
```

> [!NOTE]
> **First-Run Certificate Generation:** When `mitmproxy` starts for the first time, it automatically generates its local CA certificate at `~/.mitmproxy/mitmproxy-ca-cert.pem`. Launch `ctxins` once before running `eval $(uv run ctxins env)` in your agent terminal so the certificate path is detected and exported.

> **Keybindings in TUI:**
> - `[h]`: Open interactive Hook Guide for CLI agents, local ports, and SDKs.
> - `[c]`: Copy proxy environment export command to clipboard (`export HTTP_PROXY=...`).
> - `[u]`: Copy proxy environment unset command to clipboard (`unset HTTP_PROXY ...`).
> - `[w]`: Open the Web Dashboard in your default browser.

### Option B: Local Models & Port Gateway (`--target-port`)
Hook any local agent or local model (Ollama, vLLM, LM Studio) running on any local port:

```bash
# Hook an agent or local LLM server running on port 8000
uv run ctxins --target-port 8000

# In your agent terminal, route base URL to the ctxins proxy:
export OPENAI_BASE_URL="http://127.0.0.1:8080/v1"
# Or for Ollama:
export OLLAMA_HOST="http://127.0.0.1:8080"
```

### Option C: Harness Subprocess Runner (`ctxins run`)
Execute your agent harness wrapped with an automatic interceptor proxy without terminal conflicts or shell environment mutation:

```bash
# Run agent in foreground with live web dashboard in background
uv run ctxins run -- agy
uv run ctxins run -- claude
```

> [!TIP]
> **Recommended:** `ctxins run -- <agent>` automatically configures proxy and TLS certificates *only* inside that child process. When the agent exits, your terminal environment remains 100% clean—no `eval` or `unset` required.

---

### 🌐 Proxy Environment Configuration (Setting & Unsetting)

When running agents in a separate terminal or shell session alongside `ctxins`, use the following commands to configure or clear proxy routing:

#### 1. Setting Proxy Environment Variables
Route outbound agent traffic through the `ctxins` interceptor proxy and trust the local CA cert:

```bash
# Evaluate export commands in your current shell
eval $(uv run ctxins env)

# Or copy from TUI with [c], or copy & paste directly:
export HTTP_PROXY="http://127.0.0.1:8080" HTTPS_PROXY="http://127.0.0.1:8080" ALL_PROXY="http://127.0.0.1:8080" http_proxy="http://127.0.0.1:8080" https_proxy="http://127.0.0.1:8080" all_proxy="http://127.0.0.1:8080" SSL_CERT_FILE="$HOME/.mitmproxy/mitmproxy-ca-cert.pem" REQUESTS_CA_BUNDLE="$HOME/.mitmproxy/mitmproxy-ca-cert.pem" NODE_EXTRA_CA_CERTS="$HOME/.mitmproxy/mitmproxy-ca-cert.pem"
```

#### 2. Unsetting Proxy Environment Variables
> [!IMPORTANT]
> If proxy variables remain set when `ctxins` is stopped, agents will fail with `connection refused` because port 8080 is closed. Always unset when finished!

```bash
# Evaluate unset commands in your current shell
eval $(uv run ctxins env --unset)
# Or use the dedicated unset-env alias:
eval $(uv run ctxins unset-env)

# Or copy from TUI with [u], or copy & paste directly:
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY GRPC_PROXY http_proxy https_proxy all_proxy grpc_proxy NO_PROXY no_proxy SSL_CERT_FILE SSL_CERT_DIR REQUESTS_CA_BUNDLE NODE_EXTRA_CA_CERTS CTXINS_TARGET
```

---

## 🛠️ CLI Reference

| Subcommand | Description | Key Options |
| :--- | :--- | :--- |
| `ctxins` / `ctxins tui` | Launch single-window TUI cockpit with concurrent background Web Dashboard | `--proxy-port PORT` (8080), `--web-port PORT` (8484), `--no-web`, `--target-port PORT`, `--target URL` |
| `ctxins env` | Output shell export commands (`eval $(ctxins env)`) or unset commands (`--unset` / `-u`) | `--proxy-port PORT` (8080), `--unset` / `-u`, `--json` |
| `ctxins unset-env` | Output shell unset commands to remove proxy & cert variables (`eval $(ctxins unset-env)`) | `--json` |
| `ctxins run` | Spawn proxy and execute agent harness subprocess with auto-configured environment | `--web`, `--tui`, `--port PORT`, `--proxy-port PORT`, `--target-port PORT`, `-- COMMAND...` |
| `ctxins web` | Launch standalone Web Dashboard server and auto-spawned mitmproxy interceptor | `--port PORT` (8484), `--host HOST`, `--proxy-port PORT` (8080), `--target-port PORT` |
| `ctxins live` | Start Core Engine + selected UI mode (`web` or `tui`) | `--web`, `--tui`, `--port PORT`, `--proxy-port PORT`, `--target-port PORT` |

#### Global Options
The following options apply to all `ctxins` subcommands:
- `--debug`, `-d`: Enable verbose debug logging (sets level to `DEBUG` and writes to log file).
- `--log-level`: Explicit logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`; default: `WARNING`).
- `--log-file PATH`: Path to write log output (default: `~/.ctxins/ctxins.log`).

---

## 📊 Real-Time UIs & Captured Metrics

`ctxins` provides purpose-built real-time user interfaces to monitor context dynamics, cache performance, and optimization recommendations:

### 1. Interactive Terminal UI (TUI)
Designed to run side-by-side with your agent harness in split terminals or `tmux`:
```bash
# Launch interactive TUI attached to active Core Engine
uv run ctxins tui --socket /tmp/ctxins.sock
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

### 2. Live Web Dashboard & Formatted JSON Inspector
Launch the local web dashboard to view interactive token charts, cache invalidation heatmaps, and recommendation details:
```bash
# Start the web dashboard gateway (default: http://localhost:8484)
uv run ctxins web --port 8484 --proxy-port 8080
```
- **Real-Time WebSocket Streaming (`/ws/live`)**: Instant session discovery, streaming status updates, and turn completion without page refreshes.
- **Token Composition & Cache Hit Rate Graphs**: Interactive stacked bar charts showing exact system, tool definition, conversation history, tool result, thinking, and output token distributions per turn.
- **Interactive, Collapsible JSON Inspector**:
  - **Hierarchical Node Folding**: Expand/collapse objects and arrays with visual carets (`▼`/`▶`).
  - **Collapsed Summary Pills**: Displays compact item/key counts (e.g. `{ 8 keys }`, `[ 12 items ]`) when folded.
  - **Syntax Coloring**: Syntax-highlighted keys, strings, numbers, booleans, and nulls.
  - **Global Controls**: Instant "Expand All" and "Collapse All" for complex multi-thousand token contexts.
  - **Live Filter & Search**: Search keys or values in real time with keyword highlighting (`<mark>`) and automatic ancestor expansion.
  - **Raw / Tree View Toggle & One-Click Copy**: Switch between interactive tree and raw formatted JSON with one-click clipboard copying.
- **Prescriptive Recommendation Cards**: One-click remediation snippets and financial waste estimates for `CTX-001`..`004` and `CACHE-001`.
- **Turn-to-Turn AST Diffing**: Inspect newly injected, persisted, and pruned context blocks between any two turns in a session.

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
- **`CTX-004` (Recurring Execution Results Exceeded):** Flags duplicate tool results accumulating across turns ($\ge 2,000$ tokens or $> 20\%$ of context window).
- **`CACHE-001` (Dynamic Prefix Invalidation):** Flags prefix mutations in system prompts breaking prompt cache reuse.

👉 **See [docs/heuristics.md](docs/heuristics.md) for complete mathematical formulas, threshold configurations, and suggested fixes.**

---

## ❓ Troubleshooting & FAQs

- **Agent fails with `Connection Refused` on port 8080:**
  Proxy environment variables (`HTTP_PROXY`, etc.) are still set in your shell after `ctxins` has stopped. Run `eval $(uv run ctxins env --unset)` or `eval $(ctxins unset-env)` to clear them.
- **TLS Certificate Verification Error (`certificate verify failed`):**
  Ensure `~/.mitmproxy/mitmproxy-ca-cert.pem` exists. Run `ctxins` once to auto-generate the certificate, then run `eval $(uv run ctxins env)`. For Node.js agents, confirm `NODE_EXTRA_CA_CERTS` is set; for Python, check `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE`.
- **Port 8080 or 8484 already in use:**
  Specify alternate ports using `--proxy-port <PORT>` and `--web-port <PORT>`.
- **Preventing Shell Pollution entirely:**
  Use `ctxins run -- <agent>` (e.g., `uv run ctxins run -- agy`). It configures proxy and certificate settings strictly within the child process and leaves your parent shell completely unmodified.

---

## 📚 Documentation & Development

- 💻 **[Development, Testing & Contribution Guide](docs/development.md)**: Running test suites (`pytest`), linters (`ruff`, `mypy`), and contributing.
- 🚀 **[Harness Integration Guides](docs/harness-guides.md)**: Detailed recipes for every agent framework.
- 🔍 **[Heuristics & Pollution Catalog](docs/heuristics.md)**: Rule catalog and composite pollution scoring math.
- 🏗️ **[Detailed Architecture & LLDs](docs/README.md)**: Low-level specifications for Interceptor, Core Engine, and IPC protocol.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).

