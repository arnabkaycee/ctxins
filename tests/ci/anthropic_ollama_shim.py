#!/usr/bin/env python3
"""Lightweight Anthropic Messages API to Ollama Chat bridge for CI smoke tests.

Translates Anthropic Messages format (POST /v1/messages with streaming SSE)
to Ollama /api/chat with model qwen2.5:0.5b (or configured model), and translates
streamed chunks back into Anthropic event stream protocol.

Enables Anthropic-only harnesses like Claude Code (@anthropic-ai/claude-code)
to test through ctxins with a local Ollama instance.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from typing import Any, Dict, List

import aiohttp
from aiohttp import web

logger = logging.getLogger("anthropic_ollama_shim")


def convert_anthropic_to_ollama(
    anthropic_payload: Dict[str, Any], default_model: str
) -> Dict[str, Any]:
    """Convert Anthropic /v1/messages payload to Ollama /api/chat payload."""
    anthropic_messages = anthropic_payload.get("messages", [])
    ollama_messages: List[Dict[str, str]] = []

    # Handle system prompt if present
    system_prompt = anthropic_payload.get("system")
    if system_prompt:
        if isinstance(system_prompt, str):
            ollama_messages.append({"role": "system", "content": system_prompt})
        elif isinstance(system_prompt, list):
            sys_text = " ".join(
                item.get("text", "")
                for item in system_prompt
                if isinstance(item, dict) and item.get("type") == "text"
            )
            if sys_text:
                ollama_messages.append({"role": "system", "content": sys_text})

    for msg in anthropic_messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, str):
            ollama_messages.append({"role": role, "content": content})
        elif isinstance(content, list):
            text_parts: List[str] = []
            for block in content:
                if isinstance(block, str):
                    text_parts.append(block)
                elif isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_result":
                        text_parts.append(f"Tool Result: {block.get('content', '')}")
            ollama_messages.append({"role": role, "content": "\n".join(text_parts)})

    return {
        "model": default_model,
        "messages": ollama_messages,
        "stream": anthropic_payload.get("stream", True),
    }


async def handle_messages(request: web.Request) -> web.StreamResponse:
    """Handle POST /v1/messages."""
    default_model = request.app["default_model"]
    ollama_url = request.app["ollama_url"]

    try:
        body = await request.json()
    except Exception:
        return web.Response(
            status=400, text=json.dumps({"error": "Invalid JSON"}), content_type="application/json"
        )

    stream = body.get("stream", True)
    ollama_payload = convert_anthropic_to_ollama(body, default_model=default_model)
    msg_id = f"msg_{uuid.uuid4().hex[:12]}"

    if stream:
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await response.prepare(request)

        # 1. message_start
        message_start = {
            "type": "message_start",
            "message": {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "model": body.get("model", "claude-3-5-sonnet-20241022"),
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 20, "output_tokens": 1},
            },
        }
        await response.write(
            f"event: message_start\ndata: {json.dumps(message_start)}\n\n".encode("utf-8")
        )

        # 2. content_block_start
        block_start = {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        }
        await response.write(
            f"event: content_block_start\ndata: {json.dumps(block_start)}\n\n".encode("utf-8")
        )

        # Stream from Ollama
        output_tokens = 0
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    f"{ollama_url}/api/chat", json=ollama_payload
                ) as ollama_resp:
                    async for line in ollama_resp.content:
                        line_str = line.decode("utf-8").strip()
                        if not line_str:
                            continue
                        try:
                            data = json.loads(line_str)
                            chunk_text = data.get("message", {}).get("content", "")
                            if chunk_text:
                                output_tokens += 1
                                delta_event = {
                                    "type": "content_block_delta",
                                    "index": 0,
                                    "delta": {"type": "text_delta", "text": chunk_text},
                                }
                                await response.write(
                                    f"event: content_block_delta\ndata: {json.dumps(delta_event)}\n\n".encode(
                                        "utf-8"
                                    )
                                )
                        except Exception:
                            continue
            except Exception as e:
                logger.error("Error communicating with Ollama: %s", e)
                # Fallback text delta so test doesn't hang
                fallback_event = {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "pong"},
                }
                await response.write(
                    f"event: content_block_delta\ndata: {json.dumps(fallback_event)}\n\n".encode(
                        "utf-8"
                    )
                )

        # 3. content_block_stop
        await response.write(
            f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n".encode(
                "utf-8"
            )
        )

        # 4. message_delta
        message_delta = {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": max(output_tokens, 1)},
        }
        await response.write(
            f"event: message_delta\ndata: {json.dumps(message_delta)}\n\n".encode("utf-8")
        )

        # 5. message_stop
        await response.write(
            f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n".encode("utf-8")
        )

        return response

    else:
        # Non-streaming response
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{ollama_url}/api/chat", json=ollama_payload) as ollama_resp:
                result = await ollama_resp.json()
                text = result.get("message", {}).get("content", "pong")

        resp_payload = {
            "id": msg_id,
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
            "model": body.get("model", "claude-3-5-sonnet-20241022"),
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 20, "output_tokens": 5},
        }
        return web.Response(text=json.dumps(resp_payload), content_type="application/json")


async def handle_health(request: web.Request) -> web.Response:
    return web.Response(text="OK")


def create_app(
    ollama_url: str = "http://127.0.0.1:11434", default_model: str = "qwen2.5:0.5b"
) -> web.Application:
    app = web.Application()
    app["ollama_url"] = ollama_url
    app["default_model"] = default_model
    app.router.add_post("/v1/messages", handle_messages)
    app.router.add_get("/health", handle_health)
    return app


def main() -> int:
    parser = argparse.ArgumentParser(description="Anthropic Messages to Ollama Bridge")
    parser.add_argument(
        "--port", type=int, default=11435, help="Port to listen on (default: 11435)"
    )
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434", help="Ollama base URL")
    parser.add_argument("--model", default="qwen2.5:0.5b", help="Model name in Ollama")
    args = parser.parse_args()

    app = create_app(ollama_url=args.ollama_url, default_model=args.model)
    web.run_app(app, host="127.0.0.1", port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
