"""Gemini API payload normalizer producing CanonicalTurn AST."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

from src.core.ast.normalizers.base import BaseNormalizer
from src.core.graph.hasher import compute_block_hash
from src.schema.ast import BlockType, CanonicalTurn, ContextBlock


class GeminiASTNormalizer(BaseNormalizer):
    """Normalizes Google Gemini GenerateContent request and response payloads into CanonicalTurn."""

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
            default_provider="gemini",
        )

        # Handle Cloud Code / AI Code wrapped payloads
        if "request" in req and isinstance(req["request"], dict):
            outer_model = req.get("model")
            req = dict(req["request"])
            if outer_model and "model" not in req:
                req["model"] = outer_model
                meta["model"] = outer_model

        if "response" in resp and isinstance(resp["response"], dict):
            resp = dict(resp["response"])

        # 1. System Instruction Blocks
        system_blocks: list[ContextBlock] = []
        sys_inst = req.get("systemInstruction") or req.get("system_instruction")
        if isinstance(sys_inst, dict):
            parts = sys_inst.get("parts", [])
            for idx, part in enumerate(parts):
                text = part.get("text", "") if isinstance(part, dict) else str(part)
                if text:
                    system_blocks.append(
                        ContextBlock(
                            block_id=f"sys_{idx}",
                            block_type=BlockType.SYSTEM,
                            content_hash=compute_block_hash(text),
                            token_count=self.estimate_tokens(text),
                            content=text,
                            metadata={"role": "system"},
                        )
                    )
        elif isinstance(sys_inst, str) and sys_inst:
            system_blocks.append(
                ContextBlock(
                    block_id="sys_0",
                    block_type=BlockType.SYSTEM,
                    content_hash=compute_block_hash(sys_inst),
                    token_count=self.estimate_tokens(sys_inst),
                    content=sys_inst,
                    metadata={"role": "system"},
                )
            )

        # 2. Tools (Function Declarations)
        tool_defs: list[ContextBlock] = []
        for i, tool in enumerate(req.get("tools", [])):
            fn_decls = tool.get("functionDeclarations") or tool.get("function_declarations")
            if fn_decls and isinstance(fn_decls, list):
                for fn_idx, fn in enumerate(fn_decls):
                    name = fn.get("name", f"tool_{i}_{fn_idx}")
                    schema_str = json.dumps(fn, sort_keys=True)
                    tool_defs.append(
                        ContextBlock(
                            block_id=f"tool_def_{name}",
                            block_type=BlockType.TOOL_DEF,
                            content_hash=compute_block_hash(schema_str),
                            token_count=self.estimate_tokens(schema_str),
                            content=schema_str,
                            metadata={"name": name, "description": fn.get("description", "")},
                        )
                    )
            else:
                name = tool.get("name", f"tool_{i}")
                schema_str = json.dumps(tool, sort_keys=True)
                tool_defs.append(
                    ContextBlock(
                        block_id=f"tool_def_{name}",
                        block_type=BlockType.TOOL_DEF,
                        content_hash=compute_block_hash(schema_str),
                        token_count=self.estimate_tokens(schema_str),
                        content=schema_str,
                        metadata={"name": name},
                    )
                )

        # 3. Contents (User, Model, Tool Results)
        history: list[ContextBlock] = []
        tool_results: list[ContextBlock] = []

        for msg_idx, item in enumerate(req.get("contents", [])):
            role = item.get("role", "user")
            parts = item.get("parts", [])
            if isinstance(parts, str):
                parts = [{"text": parts}]

            for part_idx, part in enumerate(parts):
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

                if "functionResponse" in part or "function_response" in part:
                    fn_resp = part.get("functionResponse") or part.get("function_response") or {}
                    name = fn_resp.get("name", f"tool_{msg_idx}")
                    resp_data = fn_resp.get("response", {})
                    resp_str = json.dumps(resp_data, sort_keys=True) if isinstance(resp_data, (dict, list)) else str(resp_data)
                    tool_results.append(
                        ContextBlock(
                            block_id=f"tool_res_{name}_{msg_idx}_{part_idx}",
                            block_type=BlockType.TOOL_RESULT,
                            content_hash=compute_block_hash(resp_str),
                            token_count=self.estimate_tokens(resp_str),
                            content=resp_str,
                            metadata={"name": name, "role": role},
                        )
                    )
                elif "functionCall" in part or "function_call" in part:
                    fn_call = part.get("functionCall") or part.get("function_call") or {}
                    name = fn_call.get("name", f"call_{msg_idx}")
                    call_str = json.dumps(fn_call, sort_keys=True)
                    history.append(
                        ContextBlock(
                            block_id=f"hist_{msg_idx}_{part_idx}",
                            block_type=BlockType.ASSISTANT_MSG,
                            content_hash=compute_block_hash(call_str),
                            token_count=self.estimate_tokens(call_str),
                            content=call_str,
                            metadata={"role": role, "name": name, "type": "function_call"},
                        )
                    )
                elif "text" in part:
                    text = part["text"]
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

        # 4. Response Candidates
        assistant_blocks: list[ContextBlock] = []
        candidates = resp.get("candidates", [])
        if isinstance(candidates, list) and candidates:
            for c_idx, cand in enumerate(candidates):
                c_content = cand.get("content", {})
                parts = c_content.get("parts", [])
                for p_idx, part in enumerate(parts):
                    if isinstance(part, dict):
                        if "text" in part:
                            text = part["text"]
                            assistant_blocks.append(
                                ContextBlock(
                                    block_id=f"resp_text_{c_idx}_{p_idx}",
                                    block_type=BlockType.ASSISTANT_MSG,
                                    content_hash=compute_block_hash(text),
                                    token_count=self.estimate_tokens(text),
                                    content=text,
                                    metadata={"role": "model"},
                                )
                            )
                        elif "functionCall" in part or "function_call" in part:
                            fn_call = part.get("functionCall") or part.get("function_call") or {}
                            name = fn_call.get("name", f"call_{c_idx}_{p_idx}")
                            call_str = json.dumps(fn_call, sort_keys=True)
                            assistant_blocks.append(
                                ContextBlock(
                                    block_id=f"resp_tool_{name}_{c_idx}_{p_idx}",
                                    block_type=BlockType.ASSISTANT_MSG,
                                    content_hash=compute_block_hash(call_str),
                                    token_count=self.estimate_tokens(call_str),
                                    content=call_str,
                                    metadata={
                                        "role": "model",
                                        "type": "function_call",
                                        "name": name,
                                    },
                                )
                            )
                    elif isinstance(part, str):
                        assistant_blocks.append(
                            ContextBlock(
                                block_id=f"resp_text_{c_idx}_{p_idx}",
                                block_type=BlockType.ASSISTANT_MSG,
                                content_hash=compute_block_hash(part),
                                token_count=self.estimate_tokens(part),
                                content=part,
                                metadata={"role": "model"},
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
