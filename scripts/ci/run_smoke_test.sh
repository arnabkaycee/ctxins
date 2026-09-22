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

cleanup() {
  echo "Cleaning up background services..."
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

    export OPENAI_BASE_URL="${OLLAMA_URL}/v1"
    export OPENAI_API_KEY="dummy-key"
    export OLLAMA_HOST="${OLLAMA_URL}"
    export CI=true

    mkdir -p "$HOME/.config/opencode"
    cat <<EOF > "$HOME/.config/opencode/opencode.json"
{
  "model": "openai/${MODEL}",
  "providers": {
    "openai": {
      "baseURL": "${OLLAMA_URL}/v1",
      "apiKey": "dummy"
    }
  }
}
EOF

    echo "Executing opencode..."
    timeout 30 opencode run -m "openai/${MODEL}" "ping" || timeout 30 opencode run "ping" || true
    ;;

  pi)
    EXPECTED_HARNESS="pi"
    if ! command -v pi >/dev/null 2>&1; then
      echo "❌ Error: 'pi' binary not found in PATH!" >&2
      exit 1
    fi

    export OPENAI_BASE_URL="${OLLAMA_URL}/v1"
    export OPENAI_API_KEY="dummy-key"
    export OLLAMA_HOST="${OLLAMA_URL}"
    export CI=true

    echo "Executing pi..."
    timeout 30 pi --provider openai --model "${MODEL}" -p "ping" || true
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
