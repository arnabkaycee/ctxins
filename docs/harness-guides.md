# Harness Integration Guides: Using `ctxins` with Agentic Harnesses

`ctxins` provides context inspection, token bloat analysis, and prompt cache optimization for any agentic coding harness or LLM application.

There are two primary ways to connect your agent to `ctxins`:
1. **Universal Network Interception (Recommended)**: Wraps any CLI, script, or binary with zero code modifications using a local proxy tap.
2. **In-Harness Native Hooks / SDK Callbacks**: Uses in-process plugins or SDK callbacks in locked-down environments where TLS interception is restricted.

---

## Quick Reference: Universal Wrapper

For all CLI-based agent tools, the fastest method is the `ctxins run` wrapper, which automatically injects proxy variables and trusted CA certificates into the target process:

```bash
# General syntax
ctxins run -- <your-agent-command>

# Examples:
ctxins run -- claude
ctxins run -- aider
ctxins run -- opencode
ctxins run -- python my_agent.py
ctxins run -- npm run agent
```

---

## 1. Claude Code (`claude`)

Claude Code communicates with the Anthropic Messages API (`api.anthropic.com/v1/messages`) via Node.js, utilizing streaming and prompt caching.

### Option A: Universal CLI Wrapper (Fastest)

Run Claude Code wrapped with `ctxins`:

```bash
ctxins run -- claude
```

### Option B: Manual Environment Variables

If you prefer to start the `ctxins` proxy daemon separately (`ctxins proxy --port 8080`):

```bash
# 1. Start ctxins daemon in a background terminal
ctxins proxy --port 8080

# 2. In your working terminal, configure proxy and Node CA certificates
export HTTP_PROXY="http://127.0.0.1:8080"
export HTTPS_PROXY="http://127.0.0.1:8080"
export NODE_EXTRA_CA_CERTS="$HOME/.ctxins/certs/mitmproxy-ca-cert.pem"

# 3. Launch Claude Code
claude
```

> **What `ctxins` tracks for Claude Code:**
> - System prompt persistence and dynamic prefix breaks (`CACHE-001`).
> - Cache creation (`cache_creation_input_tokens`) vs cache reads (`cache_read_input_tokens`).
> - Stale file read tool results (`view_file`, `grep`) lingering across turns (`CTX-001`).
> - Extended thinking token consumption.

---

## 2. Aider (`aider`)

Aider is a Python-based terminal pair programming tool communicating with OpenAI, Anthropic, or OpenRouter via `litellm` and `httpx`.

### Option A: Universal CLI Wrapper

```bash
ctxins run -- aider --model claude-3-5-sonnet-20241022
```

### Option B: Manual Shell Environment

```bash
# 1. Export proxy endpoints
export HTTP_PROXY="http://127.0.0.1:8080"
export HTTPS_PROXY="http://127.0.0.1:8080"

# 2. Export Python SSL CA bundle for requests & httpx
export SSL_CERT_FILE="$HOME/.ctxins/certs/mitmproxy-ca-cert.pem"
export REQUESTS_CA_BUNDLE="$HOME/.ctxins/certs/mitmproxy-ca-cert.pem"

# 3. Launch aider
aider --model anthropic/claude-3-5-sonnet-20241022
```

---

## 3. OpenCode (`opencode`)

OpenCode is an open-source terminal agent written in TypeScript/Node.js.

### Option A: Universal CLI Wrapper

```bash
ctxins run -- opencode
```

### Option B: Project-Level `.env` Configuration

In your OpenCode workspace root, add or update `.env`:

```env
HTTP_PROXY=http://127.0.0.1:8080
HTTPS_PROXY=http://127.0.0.1:8080
NODE_EXTRA_CA_CERTS=/Users/your-username/.ctxins/certs/mitmproxy-ca-cert.pem
```

Then run `opencode` normally.

---

## 4. Pi (`pi`)

Pi is an extensible personal AI coding agent.

### Option A: Universal CLI Wrapper

```bash
ctxins run -- pi
```

### Option B: In-Process Extension Hook (Zero-Proxy)

If running in a container or network where TLS interception cannot be installed, configure the `ctxins` telemetry hook in your `pi.config.ts`:

```typescript
import { defineConfig } from '@pi/core';
import { ctxinsHook } from '@ctxins/pi-hook';

export default defineConfig({
  telemetry: [
    ctxinsHook({
      socketPath: process.env.CTXINS_SOCKET_PATH || `${process.env.HOME}/.ctxins/ctxins.sock`,
      failOpen: true,
    }),
  ],
});
```

---

## 5. AutoGen / AG2 (Python)

AutoGen multi-agent systems often generate high turn-over-turn context bloat due to conversation history repetition among multiple agent roles.

### Option A: Process-Level Proxy Injection

```bash
ctxins run -- python autogen_workflow.py
```

### Option B: In-Code Client Configuration

Configure the proxy directly inside your AutoGen LLM configuration:

```python
import os

# Ensure proxy and certificate environment variables are visible to httpx
os.environ["HTTP_PROXY"] = "http://127.0.0.1:8080"
os.environ["HTTPS_PROXY"] = "http://127.0.0.1:8080"
os.environ["SSL_CERT_FILE"] = os.path.expanduser("~/.ctxins/certs/mitmproxy-ca-cert.pem")

config_list = [
    {
        "model": "gpt-4o",
        "api_key": os.environ["OPENAI_API_KEY"],
    }
]

llm_config = {
    "config_list": config_list,
    "temperature": 0.2,
}
```

> **What `ctxins` tracks for AutoGen:**
> - Context snowballing: multi-agent chat loops repeatedly appending conversation history without compaction.
> - Tool schema overweight: tools registered on assistant agents that are never invoked (`CTX-002`).
> - Redundant error loops: agents bouncing errors back and forth (`CTX-003`).

---

## 6. CrewAI (Python)

CrewAI coordinates specialized agents executing sequential or hierarchical tasks.

### Option A: Process-Level Proxy Injection

```bash
ctxins run -- python main.py
```

### Option B: In-Code Environment Injection

Add this block at the top of your CrewAI script or in `crew.py`:

```python
import os

os.environ["HTTP_PROXY"] = "http://127.0.0.1:8080"
os.environ["HTTPS_PROXY"] = "http://127.0.0.1:8080"
os.environ["REQUESTS_CA_BUNDLE"] = os.path.expanduser("~/.ctxins/certs/mitmproxy-ca-cert.pem")
os.environ["SSL_CERT_FILE"] = os.path.expanduser("~/.ctxins/certs/mitmproxy-ca-cert.pem")

from crewai import Agent, Crew, Process, Task
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(model="gpt-4o")
# Your crew definitions continue...
```

---

## 7. LangChain & LangGraph (Python / TypeScript)

### Option A: Universal Proxy (Zero Code Changes)

```bash
# Python
ctxins run -- python langgraph_agent.py

# TypeScript / Node.js
ctxins run -- npx tsx agent.ts
```

### Option B: Native LangChain Callback Handler (Python)

If you prefer zero proxying, use the `ctxins` callback handler:

```python
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from src.core.server.uds_server import UDSFrameServer # or ctxins client

# Pass standard proxy or custom client
model = ChatAnthropic(
    model="claude-3-5-sonnet-20241022",
    # When using proxy mode:
    # http_client=httpx.Client(proxy="http://127.0.0.1:8080", verify="~/.ctxins/certs/mitmproxy-ca-cert.pem")
)
```

---

## 8. Custom Agent Loops & Raw SDKs

If you are developing custom agent loops with standard client libraries, use the patterns below.

### Python: `anthropic` SDK

```python
import os
import httpx
from anthropic import Anthropic

cert_path = os.path.expanduser("~/.ctxins/certs/mitmproxy-ca-cert.pem")

client = Anthropic(
    api_key=os.environ["ANTHROPIC_API_KEY"],
    http_client=httpx.Client(
        proxy="http://127.0.0.1:8080",
        verify=cert_path,
    ),
)

response = client.messages.create(
    model="claude-3-5-sonnet-20241022",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello world"}],
)
```

### Python: `openai` SDK

```python
import os
import httpx
from openai import OpenAI

cert_path = os.path.expanduser("~/.ctxins/certs/mitmproxy-ca-cert.pem")

client = OpenAI(
    api_key=os.environ["OPENAI_API_KEY"],
    http_client=httpx.Client(
        proxy="http://127.0.0.1:8080",
        verify=cert_path,
    ),
)

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello world"}],
)
```

### Node.js / TypeScript: `@anthropic-ai/sdk`

```typescript
import Anthropic from '@anthropic-ai/sdk';
import { ProxyAgent } from 'undici';

const proxyAgent = new ProxyAgent('http://127.0.0.1:8080');

const client = new Anthropic({
  apiKey: process.env.ANTHROPIC_API_KEY,
  fetchOptions: {
    dispatcher: proxyAgent,
  },
});
```

### Node.js / TypeScript: `openai` SDK

```typescript
import OpenAI from 'openai';
import { ProxyAgent } from 'undici';

const proxyAgent = new ProxyAgent('http://127.0.0.1:8080');

const client = new OpenAI({
  apiKey: process.env.OPENAI_API_KEY,
  httpAgent: proxyAgent,
});
```

### cURL / Raw HTTP Testing

```bash
curl -x http://127.0.0.1:8080 \
  --cacert ~/.ctxins/certs/mitmproxy-ca-cert.pem \
  https://api.anthropic.com/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{
    "model": "claude-3-5-sonnet-20241022",
    "max_tokens": 50,
    "messages": [{"role": "user", "content": "Ping"}]
  }'
```

---

## 9. Docker & Sandboxed Environments

When running agent harnesses inside Docker containers:

```dockerfile
# Dockerfile snippet
ENV HTTP_PROXY="http://host.docker.internal:8080"
ENV HTTPS_PROXY="http://host.docker.internal:8080"
ENV SSL_CERT_FILE="/etc/ssl/certs/ctxins-ca.pem"
ENV NODE_EXTRA_CA_CERTS="/etc/ssl/certs/ctxins-ca.pem"
ENV REQUESTS_CA_BUNDLE="/etc/ssl/certs/ctxins-ca.pem"

# Mount the CA cert from your host:
# docker run -v ~/.ctxins/certs/mitmproxy-ca-cert.pem:/etc/ssl/certs/ctxins-ca.pem:ro ...
```

---

## 10. Troubleshooting & Verification

### How do I verify `ctxins` is intercepting traffic?
1. Start the proxy and listener:
   ```bash
   ctxins proxy --port 8080
   ```
2. Run your agent. In the `ctxins` logs or TUI, you should immediately see incoming turns and events:
   ```text
   [INFO] REQUEST_INITIATED | corr_01j7... | provider=anthropic model=claude-3-5-sonnet
   [INFO] TURN_COMPLETED    | corr_01j7... | tokens=14,200 (cache_read: 8,400) duration=840ms
   ```

### "Self-signed certificate in certificate chain" / SSL Verification Error
- **Node.js**: Verify `NODE_EXTRA_CA_CERTS` points to the absolute path of `~/.ctxins/certs/mitmproxy-ca-cert.pem`.
- **Python (`requests` / `httpx`)**: Verify `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` are set.
- **macOS System Trust (Optional)**:
  ```bash
  sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ~/.ctxins/certs/mitmproxy-ca-cert.pem
  ```

### Fail-Open Guarantee
If `ctxins` crashes or is terminated mid-session:
- The `BoundedRingBuffer` safely drops unsent frames without crashing your agent.
- `ctxins run` restores network bypass immediately.
- Your agent harness continues executing uninterrupted.
