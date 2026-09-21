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
SHIM_PORT=11435

echo "=================================================="
echo " Starting Smoke Test for ${TOOL}@${VERSION}"
echo " Backend: Ollama (${OLLAMA_URL}) with ${MODEL}"
echo " ctxins proxy: 127.0.0.1:${PROXY_PORT}, web: 127.0.0.1:${WEB_PORT}"
echo "=================================================="

# Ensure ~/.mitmproxy exists for certificate generation
mkdir -p ~/.mitmproxy

# Ensure Ollama is reachable
if ! curl -s "${OLLAMA_URL}/api/tags" > /dev/null; then
  echo "⚠️ Warning: Ollama not reachable at ${OLLAMA_URL}. Ensure 'ollama serve' is running."
fi

SHIM_PID=""
cleanup() {
  echo "Cleaning up background services..."
  if [ -n "${SHIM_PID}" ] && kill -0 "${SHIM_PID}" 2>/dev/null; then
    kill "${SHIM_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

EXPECTED_HARNESS="${TOOL}"

case "${TOOL}" in
  claude)
    EXPECTED_HARNESS="claude-code"
    if ! command -v claude >/dev/null 2>&1; then
      echo "❌ Error: 'claude' CLI binary not found in PATH! Installation failed or PATH not set." >&2
      exit 1
    fi

    echo "Starting Anthropic -> Ollama shim on port ${SHIM_PORT}..."
    python3 tests/ci/anthropic_ollama_shim.py --port "${SHIM_PORT}" --ollama-url "${OLLAMA_URL}" --model "${MODEL}" &
    SHIM_PID=$!
    sleep 2

    export ANTHROPIC_BASE_URL="http://127.0.0.1:${SHIM_PORT}"
    export ANTHROPIC_API_KEY="sk-ant-dummy-ci-test-key"
    export CI=true

    echo "Running claude through ctxins..."
    timeout 30 uv run ctxins run --web --web-port "${WEB_PORT}" --proxy-port "${PROXY_PORT}" -- claude -p "ping" || true
    ;;

  opencode*|opencode)
    EXPECTED_HARNESS="opencode"
    if ! command -v opencode >/dev/null 2>&1; then
      echo "❌ Error: 'opencode' CLI binary not found in PATH! Installation failed or PATH not set." >&2
      exit 1
    fi

    export OPENAI_BASE_URL="${OLLAMA_URL}/v1"
    export OPENAI_API_KEY="dummy-key"
    export OLLAMA_HOST="${OLLAMA_URL}"
    export CI=true

    echo "Running opencode through ctxins..."
    timeout 30 uv run ctxins run --web --web-port "${WEB_PORT}" --proxy-port "${PROXY_PORT}" -- opencode run "ping" || true
    ;;

  pi)
    EXPECTED_HARNESS="pi"
    if ! command -v pi >/dev/null 2>&1; then
      echo "❌ Error: 'pi' CLI binary not found in PATH! Installation failed or PATH not set." >&2
      exit 1
    fi

    export OPENAI_BASE_URL="${OLLAMA_URL}/v1"
    export OLLAMA_HOST="${OLLAMA_URL}"
    export CI=true

    echo "Running pi through ctxins..."
    timeout 30 uv run ctxins run --web --web-port "${WEB_PORT}" --proxy-port "${PROXY_PORT}" -- pi -p "ping" || true
    ;;

  agy)
    EXPECTED_HARNESS="agy"
    if ! command -v agy >/dev/null 2>&1; then
      echo "❌ Error: 'agy' binary not found in PATH!" >&2
      exit 1
    fi

    export CI=true
    echo "Running agy through ctxins..."
    timeout 30 uv run ctxins run --web --web-port "${WEB_PORT}" --proxy-port "${PROXY_PORT}" -- agy --version || true
    ;;

  *)
    echo "Unknown tool: ${TOOL}" >&2
    exit 1
    ;;
esac

echo "=================================================="
echo " Verifying session capture in ctxins..."
echo "=================================================="
python3 tests/ci/verify_smoke_turn.py --web-url "http://127.0.0.1:${WEB_PORT}" --expected-harness "${EXPECTED_HARNESS}" --min-turns 1

echo "Smoke test cycle finished successfully."
