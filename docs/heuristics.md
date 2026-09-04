# Context Pollution Heuristics & Rules Catalog

`ctxins` runs deterministic context analysis algorithms against the canonical `ContextGraph` to identify token bloat, cache invalidations, runaway loops, and financial waste.

---

## Heuristics Matrix

| Rule ID | Name | Severity | Default Threshold | Target Resource |
| :--- | :--- | :--- | :--- | :--- |
| **`CTX-001`** | **Stale Tool Output Bloat** | `WARN` | Unreferenced for $\ge 3$ turns | Input Tokens / Cost |
| **`CTX-002`** | **Tool Schema Overweight** | `WARN` | $> 35\%$ context, $< 15\%$ invocation | Tool Def Tokens |
| **`CTX-003`** | **Error Loop Thrashing** | `CRITICAL` | $\ge 3$ consecutive turn errors | Cost / Latency / Runaway |
| **`CTX-004`** | **Redundant File Ingestion** | `INFO` | Repeated identical file contents | Input Tokens |
| **`CACHE-001`** | **Dynamic Prefix Invalidation** | `WARN` | Prefix mutation in System Prompt | Prompt Cache Hit Ratio |

---

## Detailed Rule Specifications

### 1. `CTX-001`: Stale Tool Output Bloat

* **Problem**: An agent tool (e.g. `view_file`, `bash`, `web_search`) executes and dumps several thousand tokens into the conversational history. While useful on turn $N$, the agent solves that sub-task and moves on to another problem. The tool output remains in the prompt for every subsequent turn, silently re-billing input tokens on every API call.
* **Trigger Condition**:
  A `ToolResultBlock` with $\text{token\_count} \ge 1,000$ remains present in the context for $\ge 3$ consecutive turns without any of its content keywords or identifiers referenced in assistant output messages.
* **Suggested Fix**:
  Truncate, summarize, or replace the stale tool result block with an abbreviated reference `[File auth.py read at turn 2: 120 lines]`.

### 2. `CTX-002`: Tool Schema Overweight

* **Problem**: Harnesses register dozens of rich JSON schema tool definitions for every subagent. If a harness registers 30 tools taking 6,000 tokens per turn, but the current agent task only uses 1 tool, up to 90% of the tool schema tokens are completely wasted.
* **Trigger Condition**:
  Tool definitions exceed $35\%$ of total input tokens, while $< 15\%$ of available tools are invoked across the last 5 turns.
* **Suggested Fix**:
  Implement dynamic tool loading (registering tools on demand based on current task phase or routing via a dispatcher agent).

### 3. `CTX-003`: Error Loop Thrashing

* **Problem**: An agent attempts a command that fails (syntax error, missing file, test failure). The model retries with minor variations that fail repeatedly for 3+ turns, consuming thousands of tokens without progress.
* **Trigger Condition**:
  $\ge 3$ consecutive turns contain `ToolResultBlock` with `is_error == True` or matching error message signatures.
* **Suggested Fix**:
  Interrupt the autonomous loop, trigger a human-in-the-loop prompt, or enforce exponential backoff/fallback strategies.

### 4. `CACHE-001`: Dynamic Prefix Invalidation

* **Problem**: Providers like Anthropic and OpenAI offer prompt caching (up to 90% discount on cached input tokens) for matching static prefixes (minimum 1,024 or 2,048 tokens). If a dynamic value (current timestamp, ephemeral session UUID, or variable turn counter) is injected near the beginning of the system prompt, the entire downstream prompt cache is busted.
* **Trigger Condition**:
  A mutation is detected in the first 20% of `SystemPromptBlock` tokens between sequential turns, breaking prefix matching.
* **Suggested Fix**:
  Move static instructions and tool definitions to the top of the prompt. Place dynamic variables (timestamps, workspace state) at the end of the context or inside the latest user message.

---

## Composite Pollution Score (0–100)

`ctxins` computes a normalized **Pollution Score** from 0 (pristine, highly optimized context) to 100 (heavily polluted, runaway waste):

$$\text{Pollution Score} = \min\left(100, \sum_{i} w_i \cdot \text{severity\_penalty}_i + \frac{\text{Wasted Tokens}}{\text{Total Input Tokens}} \times 50\right)$$

- **0–20 (Green)**: Optimal context hygiene. Cache hit ratio $> 80\%$.
- **21–50 (Yellow)**: Moderate context bloat. Lingering tool results detected.
- **51–100 (Red)**: Severe context degradation or broken caching. Urgent compaction recommended.
