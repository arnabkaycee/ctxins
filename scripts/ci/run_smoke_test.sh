#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# run_smoke_test.sh: Execute an agent CLI smoke test through ctxins
# ---------------------------------------------------------------------------

TOOL="${1:-claude}"
VERSION="${2:-latest}"
OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
MODEL="${MODEL:-qwen2.5:0.5b}"
WEB_PORT="${WEB_PORT:-8484}"
PROXY_PORT="${PROXY_PORT:-8080}"
SHIM_PORT="${SHIM_PORT:-11435}"

echo "=================================================="
echo " Starting Smoke Test for ${TOOL}@${VERSION}"
echo " Backend: Ollama (${OLLAMA_URL}) with ${MODEL}"
echo " ctxins proxy: 127.0.0.1:${PROXY_PORT}, web: 127.0.0.1:${WEB_PORT}"
echo "=================================================="

# Ensure ~/.mitmproxy exists for certificate generation
mkdir -p ~/.mitmproxy

CTXINS_PID=""
SHIM_PID=""
DUMMY_PID=""
OPENCODE_PID=""

cleanup() {
  echo "Cleaning up background services..."
  if [ -n "${OPENCODE_PID:-}" ] && kill -0 "${OPENCODE_PID}" 2>/dev/null; then
    kill "${OPENCODE_PID}" 2>/dev/null || true
  fi
  if [ -n "${DUMMY_PID:-}" ] && kill -0 "${DUMMY_PID}" 2>/dev/null; then
    kill "${DUMMY_PID}" 2>/dev/null || true
  fi
  if [ -n "${SHIM_PID:-}" ] && kill -0 "${SHIM_PID}" 2>/dev/null; then
    kill "${SHIM_PID}" 2>/dev/null || true
  fi
  if [ -n "${CTXINS_PID:-}" ] && kill -0 "${CTXINS_PID}" 2>/dev/null; then
    kill "${CTXINS_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

# 1. Start ctxins web and proxy in the background
echo "Starting ctxins proxy on port ${PROXY_PORT} and web dashboard on port ${WEB_PORT}..."
uv run ctxins web --port "${WEB_PORT}" --proxy-port "${PROXY_PORT}" > /tmp/ctxins.log 2>&1 &
CTXINS_PID=$!

# Wait for ctxins readiness
READY=0
for i in {1..30}; do
  if curl -s "http://127.0.0.1:${WEB_PORT}/api/config" > /dev/null 2>&1; then
    echo "ctxins web dashboard is ready!"
    READY=1
    break
  fi
  sleep 1
done

if [ "$READY" -ne 1 ]; then
  echo "❌ Error: ctxins web server failed to become ready on port ${WEB_PORT} within 30s" >&2
  cat /tmp/ctxins.log || true
  exit 1
fi

# 2. Configure proxy environment variables in current shell
eval "$(uv run ctxins env --proxy-port "${PROXY_PORT}")"

EXPECTED_HARNESS="${TOOL}"

case "${TOOL}" in
  claude)
    EXPECTED_HARNESS="claude-code"
    if ! command -v claude >/dev/null 2>&1; then
      echo "❌ Error: 'claude' binary not found in PATH!" >&2
      exit 1
    fi

    echo "Starting Anthropic -> Ollama shim on port ${SHIM_PORT}..."
    uv run python tests/ci/anthropic_ollama_shim.py --port "${SHIM_PORT}" --ollama-url "${OLLAMA_URL}" --model "${MODEL}" &
    SHIM_PID=$!
    sleep 2

    export ANTHROPIC_BASE_URL="http://127.0.0.1:${SHIM_PORT}"
    export ANTHROPIC_API_KEY="sk-ant-dummy-ci-test-key"
    export CI=true

    echo "Executing claude..."
    timeout 60 claude -p "ping" || true
    ;;

  opencode*|opencode)
    EXPECTED_HARNESS="opencode"
    if ! command -v opencode >/dev/null 2>&1; then
      echo "❌ Error: 'opencode' binary not found in PATH!" >&2
      exit 1
    fi

    export CI=true
    export OPENCODE_SERVER_PASSWORD="ci-test-password"
    export OPENCODE_SERVER_USERNAME="opencode"
    export NO_PROXY="127.0.0.1:4096,localhost:4096,127.0.0.1:1234,localhost:1234,127.0.0.1:8000,localhost:8000,models.opencode.ai,${NO_PROXY:-}"
    export no_proxy="127.0.0.1:4096,localhost:4096,127.0.0.1:1234,localhost:1234,127.0.0.1:8000,localhost:8000,models.opencode.ai,${no_proxy:-}"

    # Prevent proxy 502 on background model discovery probes (port 1234 for LM Studio, port 8000 for vLLM)
    python3 -c "
import http.server, socketserver, threading, time
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(404); self.end_headers()
    def do_POST(self): self.send_response(404); self.end_headers()
    def log_message(self, *args): pass
for p in (1234, 8000):
    try:
        s = socketserver.TCPServer(('127.0.0.1', p), H)
        threading.Thread(target=s.serve_forever, daemon=True).start()
    except Exception: pass
while True: time.sleep(1)
" >/dev/null 2>&1 &
    DUMMY_PID=$!
    sleep 1

    mkdir -p "$HOME/.config/opencode"
    cat <<EOF > "$HOME/.config/opencode/opencode.json"
{
  "\$schema": "https://opencode.ai/config.json",
  "model": "ollama/${MODEL}",
  "providers": {
    "ollama": {
      "name": "Ollama",
      "package": "@opencode/ai/providers/openai-compatible",
      "settings": {
        "baseURL": "${OLLAMA_URL}/v1"
      },
      "models": {
        "${MODEL}": {
          "name": "${MODEL}"
        }
      }
    }
  },
  "permission": {
    "read": "allow",
    "edit": "allow",
    "bash": "allow"
  }
}
EOF
    cp -f "$HOME/.config/opencode/opencode.json" ./opencode.json

    echo "Starting opencode server on port 4096..."
    opencode serve --port 4096 > /tmp/opencode_serve.log 2>&1 &
    OPENCODE_PID=$!

    for i in {1..30}; do
      if curl -s "http://127.0.0.1:4096/" > /dev/null 2>&1; then
        echo "opencode server is ready on port 4096!"
        break
      fi
      sleep 1
    done

    echo "Executing opencode run..."
    timeout 60 opencode run --server "http://127.0.0.1:4096" --auto -m "ollama/${MODEL}" "ping" || true
    ;;

  pi)
    EXPECTED_HARNESS="pi"
    if ! command -v pi >/dev/null 2>&1; then
      echo "❌ Error: 'pi' binary not found in PATH!" >&2
      exit 1
    fi

    export PI_CODING_AGENT_DIR="$HOME/.pi/agent"
    mkdir -p "$HOME/.pi/agent"
    cat <<EOF > "$HOME/.pi/agent/models.json"
{
  "providers": {
    "ollama": {
      "baseUrl": "${OLLAMA_URL}/v1",
      "api": "openai-completions",
      "apiKey": "ollama",
      "compat": {
        "supportsDeveloperRole": false,
        "supportsReasoningEffort": false
      },
      "models": [
        { "id": "${MODEL}" }
      ]
    }
  }
}
EOF

    export CI=true

    echo "Executing pi..."
    timeout 60 pi --provider ollama --model "${MODEL}" --no-context-files --no-skills -p "ping" || true
    ;;

  agy)
    EXPECTED_HARNESS="agy"
    if ! command -v agy >/dev/null 2>&1; then
      echo "❌ Error: 'agy' binary not found in PATH!" >&2
      exit 1
    fi

    export CI=true
    echo "Executing agy..."
    timeout 30 agy "ping" || true
    ;;

  *)
    echo "Unknown tool: ${TOOL}" >&2
    exit 1
    ;;
esac

echo "=================================================="
echo " Verifying session capture in ctxins..."
echo "=================================================="
uv run python tests/ci/verify_smoke_turn.py --web-url "http://127.0.0.1:${WEB_PORT}" --expected-harness "${EXPECTED_HARNESS}" --min-turns 1

echo "✅ Smoke test completed successfully!"
