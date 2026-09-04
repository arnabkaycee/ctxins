"""Anthropic API payload normalizer producing CanonicalTurn AST."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

from src.core.ast.normalizers.base import BaseNormalizer
from src.core.graph.hasher import compute_block_hash
from src.schema.ast import BlockType, CanonicalTurn, ContextBlock


class AnthropicASTNormalizer(BaseNormalizer):
    """Normalizes Anthropic Messages request and response payloads into CanonicalTurn."""

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
            default_provider="anthropic",
        )

        # 1. System Prompt Blocks
        system_blocks: list[ContextBlock] = []
        raw_system = req.get("system", "")
        if isinstance(raw_system, str) and raw_system:
            system_blocks.append(
                ContextBlock(
                    block_id="sys_0",
                    block_type=BlockType.SYSTEM,
                    content_hash=compute_block_hash(raw_system),
                    token_count=self.estimate_tokens(raw_system),
                    content=raw_system,
                    metadata={"role": "system"},
                )
            )
        elif isinstance(raw_system, list):
            for i, s_blk in enumerate(raw_system):
                if isinstance(s_blk, dict):
                    text = s_blk.get("text", "")
                    s_meta = dict(s_blk)
                else:
                    text = str(s_blk)
                    s_meta = {"type": "text"}
                if text:
                    system_blocks.append(
                        ContextBlock(
                            block_id=f"sys_{i}",
                            block_type=BlockType.SYSTEM,
                            content_hash=compute_block_hash(text),
                            token_count=self.estimate_tokens(text),
                            content=text,
                            metadata=s_meta,
                        )
                    )

        # 2. Tool Definitions
        tool_defs: list[ContextBlock] = []
        for i, tool in enumerate(req.get("tools", [])):
            schema_str = json.dumps(tool, sort_keys=True)
            tool_name = tool.get("name", f"tool_{i}")
            tool_defs.append(
                ContextBlock(
                    block_id=f"tool_def_{tool_name}",
                    block_type=BlockType.TOOL_DEF,
                    content_hash=compute_block_hash(schema_str),
                    token_count=self.estimate_tokens(schema_str),
                    content=schema_str,
                    metadata={"name": tool_name, "description": tool.get("description", "")},
                )
            )

        # 3. Conversation History & Tool Results
        history: list[ContextBlock] = []
        tool_results: list[ContextBlock] = []

        for msg_idx, msg in enumerate(req.get("messages", [])):
            role = msg.get("role")
            content = msg.get("content")

            if isinstance(content, str):
                b_type = BlockType.USER_MSG if role == "user" else BlockType.ASSISTANT_MSG
                history.append(
                    ContextBlock(
                        block_id=f"hist_{msg_idx}",
                        block_type=b_type,
                        content_hash=compute_block_hash(content),
                        token_count=self.estimate_tokens(content),
                        content=content,
                        metadata={"role": role},
                    )
                )
            elif isinstance(content, list):
                for part_idx, part in enumerate(content):
                    if not isinstance(part, dict):
                        part_str = str(part)
                        b_type = BlockType.USER_MSG if role == "user" else BlockType.ASSISTANT_MSG
                        history.append(
                            ContextBlock(
                                block_id=f"hist_{msg_idx}_{part_idx}",
                                block_type=b_type,
                                content_hash=compute_block_hash(part_str),
                                token_count=self.estimate_tokens(part_str),
                                content=part_str,
                                metadata={"role": role},
                            )
                        )
                        continue

                    p_type = part.get("type")
                    if p_type == "tool_result":
                        res_content = part.get("content", "")
                        if isinstance(res_content, (dict, list)):
                            res_str = json.dumps(res_content, sort_keys=True)
                        else:
                            res_str = str(res_content)

                        tool_use_id = part.get("tool_use_id", f"call_{msg_idx}_{part_idx}")
                        tool_results.append(
                            ContextBlock(
                                block_id=f"tool_res_{tool_use_id}",
                                block_type=BlockType.TOOL_RESULT,
                                content_hash=compute_block_hash(res_str),
                                token_count=self.estimate_tokens(res_str),
                                content=res_str,
                                metadata={
                                    "tool_use_id": tool_use_id,
                                    "is_error": part.get("is_error", False),
                                },
                            )
                        )
                    elif p_type == "text":
                        text = part.get("text", "")
                        b_type = BlockType.USER_MSG if role == "user" else BlockType.ASSISTANT_MSG
                        history.append(
                            ContextBlock(
                                block_id=f"hist_{msg_idx}_{part_idx}",
                                block_type=b_type,
                                content_hash=compute_block_hash(text),
                                token_count=self.estimate_tokens(text),
                                content=text,
                                metadata={"role": role},
                            )
                        )
                    elif p_type == "tool_use":
                        call_str = json.dumps(part, sort_keys=True)
                        call_id = part.get("id", f"call_{msg_idx}_{part_idx}")
                        history.append(
                            ContextBlock(
                                block_id=f"hist_{msg_idx}_{part_idx}",
                                block_type=BlockType.ASSISTANT_MSG,
                                content_hash=compute_block_hash(call_str),
                                token_count=self.estimate_tokens(call_str),
                                content=call_str,
                                metadata={"role": role, "tool_use_id": call_id, "name": part.get("name")},
                            )
                        )
                    else:
                        raw_str = json.dumps(part, sort_keys=True)
                        b_type = BlockType.USER_MSG if role == "user" else BlockType.ASSISTANT_MSG
                        history.append(
                            ContextBlock(
                                block_id=f"hist_{msg_idx}_{part_idx}",
                                block_type=b_type,
                                content_hash=compute_block_hash(raw_str),
                                token_count=self.estimate_tokens(raw_str),
                                content=raw_str,
                                metadata={"role": role, "type": p_type},
                            )
                        )

        # 4. Assistant Response Blocks
        assistant_blocks: list[ContextBlock] = []
        resp_content = resp.get("content", [])
        if isinstance(resp_content, list):
            for idx, blk in enumerate(resp_content):
                if isinstance(blk, dict):
                    b_type = blk.get("type", "text")
                    if b_type == "text":
                        text = blk.get("text", "")
                        assistant_blocks.append(
                            ContextBlock(
                                block_id=f"resp_{idx}",
                                block_type=BlockType.ASSISTANT_MSG,
                                content_hash=compute_block_hash(text),
                                token_count=self.estimate_tokens(text),
                                content=text,
                                metadata={"type": "text"},
                            )
                        )
                    elif b_type == "tool_use":
                        call_str = json.dumps(blk, sort_keys=True)
                        tool_id = blk.get("id", f"call_{idx}")
                        assistant_blocks.append(
                            ContextBlock(
                                block_id=f"resp_tool_{tool_id}",
                                block_type=BlockType.ASSISTANT_MSG,
                                content_hash=compute_block_hash(call_str),
                                token_count=self.estimate_tokens(call_str),
                                content=call_str,
                                metadata={
                                    "type": "tool_use",
                                    "name": blk.get("name"),
                                    "tool_use_id": tool_id,
                                },
                            )
                        )
                    else:
                        blk_str = json.dumps(blk, sort_keys=True)
                        assistant_blocks.append(
                            ContextBlock(
                                block_id=f"resp_{idx}",
                                block_type=BlockType.ASSISTANT_MSG,
                                content_hash=compute_block_hash(blk_str),
                                token_count=self.estimate_tokens(blk_str),
                                content=blk_str,
                                metadata={"type": b_type},
                            )
                        )
                elif isinstance(blk, str):
                    assistant_blocks.append(
                        ContextBlock(
                            block_id=f"resp_{idx}",
                            block_type=BlockType.ASSISTANT_MSG,
                            content_hash=compute_block_hash(blk),
                            token_count=self.estimate_tokens(blk),
                            content=blk,
                            metadata={"type": "text"},
                        )
                    )
        elif isinstance(resp_content, str) and resp_content:
            assistant_blocks.append(
                ContextBlock(
                    block_id="resp_0",
                    block_type=BlockType.ASSISTANT_MSG,
                    content_hash=compute_block_hash(resp_content),
                    token_count=self.estimate_tokens(resp_content),
                    content=resp_content,
                    metadata={"type": "text"},
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

        # 5. Usage & Timing
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
