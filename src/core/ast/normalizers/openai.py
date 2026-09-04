"""OpenAI API payload normalizer producing CanonicalTurn AST."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

from src.core.ast.normalizers.base import BaseNormalizer
from src.core.graph.hasher import compute_block_hash
from src.schema.ast import BlockType, CanonicalTurn, ContextBlock


class OpenAIASTNormalizer(BaseNormalizer):
    """Normalizes OpenAI Chat Completion request and response payloads into CanonicalTurn."""

    def __init__(
        self,
        token_counter: Optional[Callable[[str], int]] = None,
    ) -> None:
        super().__init__(token_counter=token_counter)

    def normalize(
        self,
        turn_data: Dict[str, Any],
        turn_index: Optional[int] = None,
    ) -> CanonicalTurn:
        meta, req, resp = self._extract_base_fields(
            turn_data,
            turn_index=turn_index,
            default_provider="openai",
        )

        system_blocks: list[ContextBlock] = []
        tool_defs: list[ContextBlock] = []
        history: list[ContextBlock] = []
        tool_results: list[ContextBlock] = []

        # 1. Tool Definitions
        for i, tool in enumerate(req.get("tools", [])):
            fn = tool.get("function", {}) if tool.get("type") == "function" else tool
            tool_name = fn.get("name", tool.get("name", f"tool_{i}"))
            schema_str = json.dumps(tool, sort_keys=True)
            tool_defs.append(
                ContextBlock(
                    block_id=f"tool_def_{tool_name}",
                    block_type=BlockType.TOOL_DEF,
                    content_hash=compute_block_hash(schema_str),
                    token_count=self.estimate_tokens(schema_str),
                    content=schema_str,
                    metadata={"name": tool_name, "type": tool.get("type", "function")},
                )
            )

        # 2. Messages Decomposition
        for msg_idx, msg in enumerate(req.get("messages", [])):
            role = msg.get("role")
            content = msg.get("content")

            if role in ("system", "developer"):
                sys_text = content if isinstance(content, str) else json.dumps(content, sort_keys=True)
                system_blocks.append(
                    ContextBlock(
                        block_id=f"sys_{msg_idx}",
                        block_type=BlockType.SYSTEM,
                        content_hash=compute_block_hash(sys_text),
                        token_count=self.estimate_tokens(sys_text),
                        content=sys_text,
                        metadata={"role": role},
                    )
                )
            elif role == "tool":
                call_id = msg.get("tool_call_id", f"call_{msg_idx}")
                res_str = content if isinstance(content, str) else json.dumps(content, sort_keys=True)
                tool_results.append(
                    ContextBlock(
                        block_id=f"tool_res_{call_id}",
                        block_type=BlockType.TOOL_RESULT,
                        content_hash=compute_block_hash(res_str),
                        token_count=self.estimate_tokens(res_str),
                        content=res_str,
                        metadata={"tool_use_id": call_id, "name": msg.get("name")},
                    )
                )
            elif role == "assistant":
                if content:
                    content_str = content if isinstance(content, str) else json.dumps(content, sort_keys=True)
                    history.append(
                        ContextBlock(
                            block_id=f"hist_{msg_idx}",
                            block_type=BlockType.ASSISTANT_MSG,
                            content_hash=compute_block_hash(content_str),
                            token_count=self.estimate_tokens(content_str),
                            content=content_str,
                            metadata={"role": "assistant"},
                        )
                    )
                tool_calls = msg.get("tool_calls", [])
                for tc_idx, tc in enumerate(tool_calls):
                    call_id = tc.get("id", f"call_{msg_idx}_{tc_idx}")
                    tc_str = json.dumps(tc, sort_keys=True)
                    history.append(
                        ContextBlock(
                            block_id=f"hist_{msg_idx}_call_{call_id}",
                            block_type=BlockType.ASSISTANT_MSG,
                            content_hash=compute_block_hash(tc_str),
                            token_count=self.estimate_tokens(tc_str),
                            content=tc_str,
                            metadata={
                                "role": "assistant",
                                "tool_use_id": call_id,
                                "name": tc.get("function", {}).get("name"),
                            },
                        )
                    )
            else:  # user
                if isinstance(content, str):
                    history.append(
                        ContextBlock(
                            block_id=f"hist_{msg_idx}",
                            block_type=BlockType.USER_MSG,
                            content_hash=compute_block_hash(content),
                            token_count=self.estimate_tokens(content),
                            content=content,
                            metadata={"role": role},
                        )
                    )
                elif isinstance(content, list):
                    for part_idx, part in enumerate(content):
                        part_text = part.get("text", "") if isinstance(part, dict) else str(part)
                        history.append(
                            ContextBlock(
                                block_id=f"hist_{msg_idx}_{part_idx}",
                                block_type=BlockType.USER_MSG,
                                content_hash=compute_block_hash(part_text),
                                token_count=self.estimate_tokens(part_text),
                                content=part_text,
                                metadata={"role": role},
                            )
                        )

        # 3. Response Messages
        assistant_blocks: list[ContextBlock] = []
        choices = resp.get("choices", [])
        if isinstance(choices, list) and choices:
            for c_idx, choice in enumerate(choices):
                c_msg = choice.get("message", {}) or choice.get("delta", {})
                content = c_msg.get("content")
                if content:
                    assistant_blocks.append(
                        ContextBlock(
                            block_id=f"resp_text_{c_idx}",
                            block_type=BlockType.ASSISTANT_MSG,
                            content_hash=compute_block_hash(content),
                            token_count=self.estimate_tokens(content),
                            content=content,
                            metadata={"role": "assistant"},
                        )
                    )
                tool_calls = c_msg.get("tool_calls", [])
                for tc_idx, tc in enumerate(tool_calls):
                    call_id = tc.get("id", f"call_{c_idx}_{tc_idx}")
                    tc_str = json.dumps(tc, sort_keys=True)
                    fn = tc.get("function", {})
                    assistant_blocks.append(
                        ContextBlock(
                            block_id=f"resp_tool_{call_id}",
                            block_type=BlockType.ASSISTANT_MSG,
                            content_hash=compute_block_hash(tc_str),
                            token_count=self.estimate_tokens(tc_str),
                            content=tc_str,
                            metadata={
                                "type": "tool_call",
                                "name": fn.get("name"),
                                "tool_use_id": call_id,
                            },
                        )
                    )
        elif resp.get("blocks"):
            for idx, blk in enumerate(resp["blocks"]):
                if hasattr(blk, "text"):
                    text = blk.text or blk.partial_json or ""
                    assistant_blocks.append(
                        ContextBlock(
                            block_id=f"resp_{blk.index}",
                            block_type=BlockType.ASSISTANT_MSG,
                            content_hash=compute_block_hash(text),
                            token_count=self.estimate_tokens(text),
                            content=text,
                            metadata={"type": blk.block_type},
                        )
                    )
                elif isinstance(blk, dict):
                    text = blk.get("text") or blk.get("partial_json") or ""
                    assistant_blocks.append(
                        ContextBlock(
                            block_id=f"resp_{blk.get('index', idx)}",
                            block_type=BlockType.ASSISTANT_MSG,
                            content_hash=compute_block_hash(text),
                            token_count=self.estimate_tokens(text),
                            content=text,
                            metadata={"type": blk.get("block_type", "text")},
                        )
                    )

        # 4. Usage & Timing
        in_tokens, out_tokens, read_tokens, crt_tokens = self._parse_usage(meta["usage"])
        dur_ms, ttft = self._parse_timing(meta["timing"])

        return CanonicalTurn(
            turn_id=meta["turn_id"],
            correlation_id=meta["correlation_id"],
            session_id=meta["session_id"],
            turn_index=meta["turn_index"],
            timestamp=meta["timestamp"],
            provider=meta["provider"],
            model=meta["model"],
            system_blocks=system_blocks,
            tool_defs=tool_defs,
            conversation_history=history,
            tool_results=tool_results,
            assistant_blocks=assistant_blocks,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
            cached_read_tokens=read_tokens,
            cached_created_tokens=crt_tokens,
            duration_ms=dur_ms,
            ttft_ms=ttft,
        )
