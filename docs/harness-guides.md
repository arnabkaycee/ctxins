# Harness Integration Guides: Using `ctxins` with Agentic Harnesses

`ctxins` provides context inspection, token bloat analysis, and prompt cache optimization for any agentic coding harness or LLM application.

> **Note on Execution:** `ctxins` is not a standalone compiled binary. It runs as a **mitmproxy addon** (`src/interceptor/addon.py`) communicating with the **Core Engine** over a local Unix Domain Socket (UDS).

---

## 1. Starting the `ctxins` Pipeline

Before running your agent harness, start the proxy and the IPC socket.

### Step 1: Start the Core Engine (UDS Receiver)
The Core Engine listens on a Unix Domain Socket (e.g. `/tmp/ctxins.sock`):

```bash
# Start the Core Frame Server in a background terminal or daemon
CTXINS_SOCKET_PATH=/tmp/ctxins.sock uv run python -m src.core.server.uds_server
```

### Step 2: Start the Interceptor Proxy
Run `mitmdump` (headless) or `mitmproxy` (interactive TUI) loading `src/interceptor/addon.py`:

```bash
# Option A: Headless proxy (recommended for scripts & automation)
CTXINS_SOCKET_PATH=/tmp/ctxins.sock uv run mitmdump -p 8080 -s src/interceptor/addon.py

# Option B: Interactive terminal dashboard
CTXINS_SOCKET_PATH=/tmp/ctxins.sock uv run mitmproxy -p 8080 -s src/interceptor/addon.py

# Option C: Web dashboard interface
CTXINS_SOCKET_PATH=/tmp/ctxins.sock uv run mitmweb -p 8080 -s src/interceptor/addon.py
```

> **First Run Note:** When `mitmproxy` starts for the first time, it automatically generates a local CA certificate at `~/.mitmproxy/mitmproxy-ca-cert.pem`.

---

## 2. Universal Helper Function (`with-ctxins`)

To avoid repeatedly typing proxy and certificate environment variables, add this lightweight wrapper function to your `~/.bashrc` or `~/.zshrc`:

```bash
with-ctxins() {
  HTTP_PROXY="http://127.0.0.1:8080" \
  HTTPS_PROXY="http://127.0.0.1:8080" \
  ALL_PROXY="http://127.0.0.1:8080" \
  NODE_EXTRA_CA_CERTS="${HOME}/.mitmproxy/mitmproxy-ca-cert.pem" \
  SSL_CERT_FILE="${HOME}/.mitmproxy/mitmproxy-ca-cert.pem" \
  REQUESTS_CA_BUNDLE="${HOME}/.mitmproxy/mitmproxy-ca-cert.pem" \
  "$@"
}
```

Once added, you can wrap any command or CLI tool:
```bash
with-ctxins agy
with-ctxins claude
with-ctxins aider
with-ctxins opencode
with-ctxins python my_agent.py
```

---

## 3. Harness-Specific Instructions

### A. Claude Code (`claude`)

Claude Code runs on Node.js and connects directly to the Anthropic Messages API (`api.anthropic.com/v1/messages`).

#### Running with the Helper:
```bash
with-ctxins claude
```

#### Running Directly with Inline Environment Variables:
```bash
HTTP_PROXY="http://127.0.0.1:8080" \
HTTPS_PROXY="http://127.0.0.1:8080" \
NODE_EXTRA_CA_CERTS="$HOME/.mitmproxy/mitmproxy-ca-cert.pem" \
claude
```

> **What `ctxins` inspects for Claude Code:**
> - System prompt persistence and dynamic prefix breaks (`CACHE-001`).
> - Cache creation (`cache_creation_input_tokens`) vs cache read hits (`cache_read_input_tokens`).
> - Stale file read tool results (`view_file`, `grep`) lingering across turns (`CTX-001`).
> - Extended thinking token consumption and tool call JSON parsing.

---

### B. Antigravity CLI (`agy`)

Google Antigravity (`agy`) is an agentic coding assistant CLI supporting multi-turn conversations, tool invocations (`view_file`, `run_command`, `replace_file_content`), and sub-agent task orchestration across Gemini and Anthropic backends.

#### Running with the Helper:
```bash
with-ctxins agy
```

#### Running Directly with Inline Environment Variables:
```bash
HTTP_PROXY="http://127.0.0.1:8080" \
HTTPS_PROXY="http://127.0.0.1:8080" \
ALL_PROXY="http://127.0.0.1:8080" \
NODE_EXTRA_CA_CERTS="$HOME/.mitmproxy/mitmproxy-ca-cert.pem" \
SSL_CERT_FILE="$HOME/.mitmproxy/mitmproxy-ca-cert.pem" \
REQUESTS_CA_BUNDLE="$HOME/.mitmproxy/mitmproxy-ca-cert.pem" \
agy
```

#### Persistent Proxy Configuration:
If you prefer not setting environment variables on every launch, export them in your shell profile (`~/.zshrc` / `~/.bashrc`):
```bash
export HTTP_PROXY="http://127.0.0.1:8080"
export HTTPS_PROXY="http://127.0.0.1:8080"
export NODE_EXTRA_CA_CERTS="$HOME/.mitmproxy/mitmproxy-ca-cert.pem"
export SSL_CERT_FILE="$HOME/.mitmproxy/mitmproxy-ca-cert.pem"
```

> **What `ctxins` inspects for Antigravity:**
> - **Multi-Turn Context Accumulation:** Tracks token growth across planning, code edits, and sub-agent task delegations.
> - **Stale Tool Outputs (`CTX-001`):** Flags large file reads, command execution outputs, or search results lingering across turns without subsequent reference.
> - **Google Gemini SSE Ingestion:** Normalizes Gemini `streamGenerateContent` SSE response chunks and usage metadata (`promptTokenCount`, `candidatesTokenCount`).
> - **Prompt Cache Optimization (`CACHE-001`):** Monitors cache prefix stability across sub-agent dispatches and system prompt changes.
> - **Tool Schema Overhead (`CTX-002`):** Quantifies token weight of loaded tools, skills, and sub-agent declarations against invocation rates.

---

### C. Aider (`aider`)

Aider is a Python CLI pairing tool communicating with OpenAI, Anthropic, or OpenRouter via `httpx` and `litellm`.

#### Running with the Helper:
```bash
with-ctxins aider --model claude-3-5-sonnet-20241022
```

#### Running Directly with Inline Environment Variables:
```bash
HTTP_PROXY="http://127.0.0.1:8080" \
HTTPS_PROXY="http://127.0.0.1:8080" \
SSL_CERT_FILE="$HOME/.mitmproxy/mitmproxy-ca-cert.pem" \
REQUESTS_CA_BUNDLE="$HOME/.mitmproxy/mitmproxy-ca-cert.pem" \
aider --model anthropic/claude-3-5-sonnet-20241022
```

---

### D. OpenCode (`opencode`)

OpenCode is a terminal coding agent built on TypeScript/Node.js.

#### Running with the Helper:
```bash
with-ctxins opencode
```

#### Running with Workspace `.env`:
In your OpenCode workspace root, create or update `.env`:
```env
HTTP_PROXY=http://127.0.0.1:8080
HTTPS_PROXY=http://127.0.0.1:8080
NODE_EXTRA_CA_CERTS=/Users/your-username/.mitmproxy/mitmproxy-ca-cert.pem
```
Then launch `opencode` normally.

---

### E. Pi (`pi`)

Pi is an extensible personal AI coding agent.

#### Running with the Helper:
```bash
with-ctxins pi
```

#### In-Process Extension Hook (Zero-Proxy Alternative):
In environments where TLS proxying is restricted, configure the telemetry hook directly in `pi.config.ts`:
```typescript
import { defineConfig } from '@pi/core';
import { ctxinsHook } from '@ctxins/pi-hook';

export default defineConfig({
  telemetry: [
    ctxinsHook({
      socketPath: process.env.CTXINS_SOCKET_PATH || '/tmp/ctxins.sock',
      failOpen: true,
    }),
  ],
});
```

---

### F. AutoGen / AG2 (Python)

AutoGen multi-agent systems often produce turn-over-turn context bloat as conversation history is repeatedly appended across agent roles.

#### Running with the Helper:
```bash
with-ctxins python autogen_workflow.py
```

#### Running with In-Code Configuration:
```python
import os

os.environ["HTTP_PROXY"] = "http://127.0.0.1:8080"
os.environ["HTTPS_PROXY"] = "http://127.0.0.1:8080"
os.environ["SSL_CERT_FILE"] = os.path.expanduser("~/.mitmproxy/mitmproxy-ca-cert.pem")
os.environ["REQUESTS_CA_BUNDLE"] = os.path.expanduser("~/.mitmproxy/mitmproxy-ca-cert.pem")

# AutoGen setup continues...
config_list = [{"model": "gpt-4o", "api_key": os.environ["OPENAI_API_KEY"]}]
```

> **What `ctxins` tracks for AutoGen:**
> - Chat snowballing: conversation history repeated across agents without compaction.
> - Tool schema overweight: unused tool definitions registered on assistant agents (`CTX-002`).
> - Consecutive agent error loops (`CTX-003`).

---

### G. CrewAI (Python)

CrewAI coordinates specialized agents executing sequential or hierarchical tasks.

#### Running with the Helper:
```bash
with-ctxins python crew.py
```

#### Running with In-Code Configuration:
Place this block at the top of your `crew.py` before importing agent dependencies:
```python
import os

os.environ["HTTP_PROXY"] = "http://127.0.0.1:8080"
os.environ["HTTPS_PROXY"] = "http://127.0.0.1:8080"
os.environ["SSL_CERT_FILE"] = os.path.expanduser("~/.mitmproxy/mitmproxy-ca-cert.pem")
os.environ["REQUESTS_CA_BUNDLE"] = os.path.expanduser("~/.mitmproxy/mitmproxy-ca-cert.pem")

from crewai import Agent, Crew, Task
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(model="gpt-4o")
```

---

### H. LangChain & LangGraph (Python / TypeScript)

#### Running with the Helper:
```bash
# Python
with-ctxins python langgraph_agent.py

# TypeScript / Node.js
with-ctxins npx tsx agent.ts
```

#### In-Code Client Configuration (Python):
```python
import os
import httpx
from langchain_anthropic import ChatAnthropic

cert = os.path.expanduser("~/.mitmproxy/mitmproxy-ca-cert.pem")
http_client = httpx.Client(proxy="http://127.0.0.1:8080", verify=cert)

model = ChatAnthropic(
    model="claude-3-5-sonnet-20241022",
    http_client=http_client,
)
```

---

### I. Custom Agent Loops & Raw SDKs

#### Python: `anthropic` SDK
```python
import os
import httpx
from anthropic import Anthropic

cert = os.path.expanduser("~/.mitmproxy/mitmproxy-ca-cert.pem")
client = Anthropic(
    api_key=os.environ["ANTHROPIC_API_KEY"],
    http_client=httpx.Client(proxy="http://127.0.0.1:8080", verify=cert),
)

response = client.messages.create(
    model="claude-3-5-sonnet-20241022",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Analyze codebase"}],
)
```

#### Python: `openai` SDK
```python
import os
import httpx
from openai import OpenAI

cert = os.path.expanduser("~/.mitmproxy/mitmproxy-ca-cert.pem")
client = OpenAI(
    api_key=os.environ["OPENAI_API_KEY"],
    http_client=httpx.Client(proxy="http://127.0.0.1:8080", verify=cert),
)

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Analyze codebase"}],
)
```

#### Node.js / TypeScript: `@anthropic-ai/sdk`
```typescript
import Anthropic from '@anthropic-ai/sdk';
import { ProxyAgent } from 'undici';

const proxyAgent = new ProxyAgent('http://127.0.0.1:8080');
const client = new Anthropic({
  apiKey: process.env.ANTHROPIC_API_KEY,
  fetchOptions: { dispatcher: proxyAgent },
});
```

#### Raw cURL
```bash
curl -x http://127.0.0.1:8080 \
  --cacert ~/.mitmproxy/mitmproxy-ca-cert.pem \
  https://api.anthropic.com/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{
    "model": "claude-3-5-sonnet-20241022",
    "max_tokens": 100,
    "messages": [{"role": "user", "content": "Ping"}]
  }'
```

---

## 4. Docker & Containerized Agents

When running agent harnesses inside Docker containers:

```bash
# Pass proxy and certificate paths into the container
docker run --rm -it \
  -e HTTP_PROXY="http://host.docker.internal:8080" \
  -e HTTPS_PROXY="http://host.docker.internal:8080" \
  -e SSL_CERT_FILE="/etc/ssl/certs/mitmproxy-ca-cert.pem" \
  -e NODE_EXTRA_CA_CERTS="/etc/ssl/certs/mitmproxy-ca-cert.pem" \
  -e REQUESTS_CA_BUNDLE="/etc/ssl/certs/mitmproxy-ca-cert.pem" \
  -v ~/.mitmproxy/mitmproxy-ca-cert.pem:/etc/ssl/certs/mitmproxy-ca-cert.pem:ro \
  my-agent-image
```

---

## 5. Troubleshooting & Verification

### How to verify traffic is intercepted?
When your proxy is running with `mitmdump -p 8080 -s src/interceptor/addon.py`:
- You will see intercept logs printed:
  ```text
  [INFO] REQUEST_INITIATED | corr_01j7... | provider=anthropic model=claude-3-5-sonnet
  [INFO] TURN_COMPLETED    | corr_01j7... | tokens=14,200 (cache_read: 8,400) duration=840ms
  ```
- If you use `mitmproxy -p 8080 -s src/interceptor/addon.py`, the interactive TUI displays intercepted requests and real-time response flows.

### SSL / Certificate Verification Error
- **Node.js**: Ensure `NODE_EXTRA_CA_CERTS` points to `$HOME/.mitmproxy/mitmproxy-ca-cert.pem`.
- **Python**: Ensure `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` point to `$HOME/.mitmproxy/mitmproxy-ca-cert.pem`.
- **System Keychain (Optional)**:
  ```bash
  # macOS
  sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ~/.mitmproxy/mitmproxy-ca-cert.pem

  # Linux (Debian/Ubuntu)
  sudo cp ~/.mitmproxy/mitmproxy-ca-cert.pem /usr/local/share/ca-certificates/mitmproxy.crt
  sudo update-ca-certificates
  ```
