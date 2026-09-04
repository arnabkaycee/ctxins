# Design: In-Harness Hooks & Plugin Adapters

## 1. Overview

The **In-Harness Hooks** approach integrates `ctxins` directly into the agent runtime via native plugin APIs, middleware, or SDK callbacks (such as Claude Code plugins, OpenCode extensions, Pi middleware, LangChain callbacks, or Vercel AI SDK telemetry).

Rather than intercepting TLS network packets, the hook taps the agent's internal state machine right as messages are generated or received, and emits structured events directly to the `ctxins` daemon.

```mermaid
flowchart LR
    subgraph HarnessHost ["Agent Process (Claude Code / OpenCode / Pi)"]
        HarnessCore["Agent State Machine & Planner"]
        HookPlugin["ctxins Native Hook / Plugin\n(@ctxins/claude-hook, ctxins-opencode)"]
    end

    subgraph CoreEngine ["ctxins Core Process"]
        IPC["Local IPC Socket / Named Pipe / Local HTTP"]
        Normalizer["Canonical Event Normalizer"]
        Analyzer["Context Analysis & Metric Engine"]
    end

    subgraph LLM ["Upstream LLM Provider"]
        API["LLM Provider API\n(Direct HTTPS)"]
    end

    HarnessCore <-->|Direct TLS (No Proxy)| API
    HarnessCore -->|Turn Lifecycle Events| HookPlugin
    HookPlugin -->|Async Non-blocking Push| IPC
    IPC --> Normalizer --> Analyzer
```

---

## 2. Key Capabilities & Advantages

1. **Zero TLS / Proxy Configuration**:
   No root certificates, no CA bundle injection, no proxy environment variables required. Works smoothly in locked-down enterprise networks, sandboxed containers, or environments where modifying system TLS trust stores is restricted.

2. **High-Level Semantic Metadata**:
   Hooks have direct access to the harness's internal context that wire packets cannot easily reveal:
   - **Internal Agent Phases**: Distinguishes between planning, tool invocation, reflection, and sub-agent handoffs.
   - **Tool Execution Context**: Exact tool names, local environment state, execution status codes, and error tracebacks.
   - **User Session Metadata**: User intent prompts, slash command triggers (e.g. `/commit`, `/review`), and harness configuration flags.

3. **Pre-Flight Inspection**:
   Hooks can inspect the constructed context *before* the request is sent over the network, opening possibilities for active context trimming or guardrails.

---

## 3. Harness Integration Strategies

### A. Claude Code Hook Integration
Claude Code supports extension hooks and lifecycle scripts.
* **Hook Trigger**: Intercepts `pre_tool_execution`, `post_tool_execution`, and `post_model_response` hooks.
* **Extraction**: Reads the loaded system prompt, conversational history array, tool definitions, and token metrics reported in the Claude Code session log.
* **Dispatch**: Emits non-blocking JSON-RPC payloads to `~/.ctxins/ctxins.sock`.

### B. OpenCode / Pi Middleware
OpenCode and Pi provide modular runtime extensions.
* **Extension Packaging**: A lightweight npm / Python module (e.g. `@ctxins/opencode-extension`).
* **Tap Point**: Intercepts prompt construction pipelines and stream response chunk handlers.
* **Metadata Attachment**: Tags events with workspace paths, active file buffers, and model configurations.

### C. Standard SDK Middlewares (LangChain, LlamaIndex, Vercel AI SDK)
* **LangChain / LangGraph**: Custom `AsyncCallbackHandler` that overrides `on_llm_start`, `on_llm_end`, `on_tool_start`, and `on_tool_end`.
* **Vercel AI SDK**: Custom `experimental_telemetry` or fetch middleware forwarding LLM calls to `ctxins`.

---

## 4. Hook Event Protocol Specification

Hooks send structured events to the `ctxins` daemon over a local Unix domain socket (`~/.ctxins/ctxins.sock`) or HTTP endpoint (`http://127.0.0.1:8942/api/v1/events`).

### Event Schema
```json
{
  "specVersion": "1.0",
  "source": "claude-code-hook",
  "sessionId": "session_claude_991823",
  "turnId": "turn_04",
  "timestamp": 1725000000450,
  "harness": {
    "name": "claude-code",
    "version": "1.0.4",
    "workspace": "/Users/arnab/Documents/code/my-project"
  },
  "model": {
    "provider": "anthropic",
    "name": "claude-3-5-sonnet-20241022"
  },
  "turnContext": {
    "phase": "tool_execution",
    "activeCommand": "/review",
    "systemPrompt": "You are Claude Code...",
    "messages": [
      { "role": "user", "content": "Fix the bug in auth.py" },
      { "role": "assistant", "content": "I will read auth.py", "toolCalls": [{ "name": "view_file", "args": { "path": "auth.py" } }] }
    ],
    "toolDefinitionsCount": 14
  },
  "usage": {
    "inputTokens": 18450,
    "outputTokens": 320,
    "cacheCreationInputTokens": 14000,
    "cacheReadInputTokens": 4000
  },
  "latency": {
    "totalDurationMs": 1820
  }
}
```

---

## 5. Non-Blocking Design & Fault Tolerance

To ensure that `ctxins` never degrades the speed or reliability of the agent:
* **Asynchronous Fire-and-Forget**: Hooks push events to IPC non-blockingly using in-memory circular buffers.
* **Fail-Open Behavior**: If the `ctxins` daemon is not running or the socket is unreachable, the hook drops the telemetry gracefully without interrupting the agent's work.
* **Zero External Dependencies**: Hook libraries are kept extremely small (< 15KB) with zero heavy external runtime dependencies.

---

## 6. Trade-offs & Mitigations

| Challenge | Impact | Mitigation Strategy |
| :--- | :--- | :--- |
| **Harness API Fragmentation** | Different hook APIs for each tool (Claude Code vs OpenCode vs Pi). | Maintain a clean abstraction layer and standard SDK adapter package. |
| **API Version Drift** | Breaking changes in harness plugin APIs. | Semantic versioning in hook adapters and graceful fallback to MITM proxy mode if hooks are unsupported. |
| **Missing Raw Wire Headers** | Some harness APIs strip low-level HTTP headers. | Extract token counts from response objects directly; use provider tokenizers for estimations when headers are absent. |
