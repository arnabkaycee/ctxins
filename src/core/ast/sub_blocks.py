"""Sub-block decomposition engine for parsing structured context segments.

Decomposes monolithic system, user, and assistant payloads containing XML-like
tags or markdown sections into discrete, granular ContextBlocks with semantic
identity keys, content hashes, and accurate token estimations.
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable, Dict, List, Optional

from src.core.graph.hasher import compute_block_hash
from src.schema.ast import BlockType, ContextBlock


def default_token_estimator(text: str) -> int:
    """Default fallback token estimator: max(1, len(text) // 4) for non-empty text."""
    if not text:
        return 0
    return max(1, len(text) // 4)


# XML tag pattern capturing known or structured context tags
# Handles: <tag attrs>body</tag>, <tag[path] attrs>body</tag>, <RULE[path]>body</RULE>
_TAG_PATTERN = re.compile(
    r"<(?P<tag>skills|skill|user_rules|rules|rule|tools|tool|tool_def|thought|thinking|context|environment|identity|instructions|system)"
    r"(?:\[(?P<bracket>[^\]]+)\])?"
    r"(?P<attrs>[^>]*)>"
    r"(?P<body>.*?)"
    r"</(?P=tag)[^>]*>",
    re.DOTALL | re.IGNORECASE,
)

# Markdown header pattern: # Title, ## Title, ### Title
_MARKDOWN_HEADER_PATTERN = re.compile(
    r"^(#{1,6})\s+(.+)$",
    re.MULTILINE,
)


def _extract_name_from_attrs_or_bracket(attrs: str, bracket: Optional[str]) -> Optional[str]:
    """Extract semantic name identifier from tag attributes or bracket expression."""
    if attrs:
        name_match = re.search(r'\b(?:name|id)=["\']([^"\']+)["\']', attrs)
        if name_match:
            return name_match.group(1).strip()
    if bracket:
        base = os.path.basename(bracket.strip())
        return base if base else bracket.strip()
    return None


def _tag_to_block_type(tag: str) -> BlockType:
    """Map XML tag name to corresponding BlockType."""
    t = tag.lower()
    if t in ("skills", "skill"):
        return BlockType.SKILL
    if t in ("user_rules", "rules", "rule", "identity", "instructions", "system"):
        return BlockType.SYSTEM
    if t in ("tools", "tool", "tool_def"):
        return BlockType.TOOL_DEF
    if t in ("thought", "thinking"):
        return BlockType.THOUGHT
    if t in ("context", "environment"):
        return BlockType.INJECTED_STATE
    return BlockType.SYSTEM


def _tag_to_identity_key(tag: str, name: Optional[str], index: int) -> str:
    """Compute semantic identity_key for an XML tag context block."""
    t = tag.lower()
    if t in ("skills", "skill"):
        if name:
            return f"skill:{name}"
        if t == "skills":
            return "skills"
        return f"skill:{index}"
    if t in ("user_rules", "rules", "rule"):
        if name:
            return f"rules:{name}"
        if t == "user_rules":
            return "rules:user_rules"
        return f"rules:{index}"
    if t in ("tools", "tool", "tool_def"):
        if name:
            return f"tool_def:{name}"
        if t == "tools":
            return "tools"
        return f"tool_def:{index}"
    if t in ("thought", "thinking"):
        return f"thought:{index}"
    if t == "context":
        if name:
            return f"context:{name}"
        return "context"
    if t == "environment":
        if name:
            return f"environment:{name}"
        return "environment"
    return f"{t}:{name or index}"


def _markdown_to_block_info(
    title: str, index: int, default_block_type: BlockType
) -> tuple[BlockType, str]:
    """Map markdown header title to BlockType and semantic identity_key."""
    t = title.lower()

    subname = ""
    for sep in (":", "-", "–", "—"):
        if sep in title:
            subname = title.split(sep, 1)[1].strip()
            break

    if "skill" in t:
        b_type = BlockType.SKILL
        ident = f"skill:{subname}" if subname else "skills"
    elif "tool" in t:
        b_type = BlockType.TOOL_DEF
        ident = f"tool_def:{subname}" if subname else "tools"
    elif "rule" in t:
        b_type = BlockType.SYSTEM
        ident = f"rules:{subname}" if subname else "rules:user_rules"
    elif "thought" in t or "reasoning" in t or "thinking" in t:
        b_type = BlockType.THOUGHT
        ident = f"thought:{index}"
    elif "context" in t:
        b_type = BlockType.INJECTED_STATE
        ident = f"context:{subname}" if subname else "context"
    elif "environment" in t:
        b_type = BlockType.INJECTED_STATE
        ident = f"environment:{subname}" if subname else "environment"
    elif "instruction" in t:
        b_type = BlockType.SYSTEM
        ident = f"instructions:{subname}" if subname else "instructions"
    else:
        b_type = default_block_type
        slug = re.sub(r"[^a-zA-Z0-9_]+", "_", t).strip("_")
        ident = f"section:{slug}" if slug else f"section:{index}"

    return b_type, ident


class SubBlockDecomposer:
    """Decomposes composite text prompts into granular, typed ContextBlocks."""

    def __init__(
        self,
        token_counter: Optional[Callable[[str], int]] = None,
    ) -> None:
        """Initialize decomposer with optional token counting callable."""
        self._token_counter = token_counter or default_token_estimator

    def estimate_tokens(self, text: str) -> int:
        """Estimate token count for a text string."""
        return self._token_counter(text)

    def decompose(
        self,
        text: str,
        default_block_type: BlockType = BlockType.SYSTEM,
        base_id: str = "sys",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[ContextBlock]:
        """Decompose text into discrete ContextBlocks.

        Inspects text for XML-like tags (<skills>, <user_rules>, <tools>, etc.)
        and markdown sections (# Skills, ## Tools, etc.). If structured markers
        are found, decomposes text into typed ContextBlocks with semantic
        identity keys. If no structured markers are detected, returns a single
        ContextBlock with the default block type.

        Args:
            text: Raw input string to decompose.
            default_block_type: BlockType for fallback and un-tagged text segments.
            base_id: Base identifier prefix for generated block IDs.
            metadata: Base metadata dictionary to attach to parsed blocks.

        Returns:
            List of ContextBlock objects preserving accurate order and content.
        """
        if not text or not text.strip():
            return []

        base_meta = dict(metadata) if metadata else {}

        # 1. Try XML tag-based decomposition
        tag_blocks = self._decompose_xml_tags(text, default_block_type, base_meta)
        if tag_blocks:
            return self._assign_block_ids(tag_blocks, base_id)

        # 2. Try Markdown section decomposition
        md_blocks = self._decompose_markdown_sections(text, default_block_type, base_meta)
        if md_blocks:
            return self._assign_block_ids(md_blocks, base_id)

        # 3. Fallback: single monolithic ContextBlock
        fallback_id = (
            base_id if ("_" in base_id and base_id.split("_")[-1].isdigit()) else f"{base_id}_0"
        )
        return [
            ContextBlock(
                block_id=fallback_id,
                block_type=default_block_type,
                content_hash=compute_block_hash(text),
                token_count=self.estimate_tokens(text),
                content=text,
                metadata=base_meta,
                identity_key="",
            )
        ]

    def _decompose_xml_tags(
        self,
        text: str,
        default_block_type: BlockType,
        base_meta: Dict[str, Any],
    ) -> List[ContextBlock]:
        """Parse XML tags from text, preserving intervening text segments."""
        matches = list(_TAG_PATTERN.finditer(text))
        if not matches:
            return []

        blocks: list[ContextBlock] = []
        last_idx = 0
        tag_index = 0

        for match in matches:
            start, end = match.span()

            # Text before the tag
            intervening = text[last_idx:start].strip()
            if intervening:
                blocks.append(
                    ContextBlock(
                        block_id="",
                        block_type=default_block_type,
                        content_hash=compute_block_hash(intervening),
                        token_count=self.estimate_tokens(intervening),
                        content=intervening,
                        metadata=dict(base_meta),
                        identity_key="",
                    )
                )

            tag_name = match.group("tag")
            bracket = match.group("bracket")
            attrs = match.group("attrs") or ""
            body = match.group("body")
            name = _extract_name_from_attrs_or_bracket(attrs, bracket)

            # Check if tag body contains nested structured child tags
            child_matches = list(_TAG_PATTERN.finditer(body))
            if child_matches:
                # Recursively parse child tags
                parent_meta = dict(base_meta)
                parent_meta["parent_tag"] = tag_name
                child_blocks = self._decompose_xml_tags(
                    body,
                    default_block_type=_tag_to_block_type(tag_name),
                    base_meta=parent_meta,
                )
                blocks.extend(child_blocks)
            else:
                body_clean = body.strip()
                block_type = _tag_to_block_type(tag_name)
                identity_key = _tag_to_identity_key(tag_name, name, tag_index)
                block_meta = dict(base_meta)
                block_meta["tag"] = tag_name.lower()
                if name:
                    block_meta["name"] = name
                if bracket:
                    block_meta["bracket"] = bracket

                blocks.append(
                    ContextBlock(
                        block_id="",
                        block_type=block_type,
                        content_hash=compute_block_hash(body_clean),
                        token_count=self.estimate_tokens(body_clean),
                        content=body_clean,
                        metadata=block_meta,
                        identity_key=identity_key,
                    )
                )
                tag_index += 1

            last_idx = end

        # Postamble text after the last tag
        tail = text[last_idx:].strip()
        if tail:
            blocks.append(
                ContextBlock(
                    block_id="",
                    block_type=default_block_type,
                    content_hash=compute_block_hash(tail),
                    token_count=self.estimate_tokens(tail),
                    content=tail,
                    metadata=dict(base_meta),
                    identity_key="",
                )
            )

        return blocks

    def _decompose_markdown_sections(
        self,
        text: str,
        default_block_type: BlockType,
        base_meta: Dict[str, Any],
    ) -> List[ContextBlock]:
        """Parse markdown headers from text, creating typed section blocks."""
        headers = list(_MARKDOWN_HEADER_PATTERN.finditer(text))
        if not headers:
            return []

        # Check if at least one header matches a known domain section keyword
        known_keywords = (
            "skill",
            "tool",
            "rule",
            "thought",
            "reasoning",
            "thinking",
            "context",
            "environment",
            "instruction",
        )
        has_recognized_header = any(
            any(k in h.group(2).lower() for k in known_keywords) for h in headers
        )
        if not has_recognized_header and len(headers) < 2:
            return []

        blocks: list[ContextBlock] = []

        # Text before the first header (preamble)
        first_start = headers[0].start()
        preamble = text[:first_start].strip()
        if preamble:
            blocks.append(
                ContextBlock(
                    block_id="",
                    block_type=default_block_type,
                    content_hash=compute_block_hash(preamble),
                    token_count=self.estimate_tokens(preamble),
                    content=preamble,
                    metadata=dict(base_meta),
                    identity_key="",
                )
            )

        for idx, h in enumerate(headers):
            sec_end = headers[idx + 1].start() if idx + 1 < len(headers) else len(text)
            header_line = text[h.start() : h.end()]
            header_level = len(h.group(1))
            title = h.group(2).strip()
            body = text[h.end() : sec_end].strip()

            content = body if body else title
            block_type, identity_key = _markdown_to_block_info(title, idx, default_block_type)

            block_meta = dict(base_meta)
            block_meta["header"] = header_line
            block_meta["title"] = title
            block_meta["level"] = header_level

            blocks.append(
                ContextBlock(
                    block_id="",
                    block_type=block_type,
                    content_hash=compute_block_hash(content),
                    token_count=self.estimate_tokens(content),
                    content=content,
                    metadata=block_meta,
                    identity_key=identity_key,
                )
            )

        return blocks

    @staticmethod
    def _assign_block_ids(blocks: List[ContextBlock], base_id: str) -> List[ContextBlock]:
        """Assign deterministic, sequential block_ids to decomposed blocks."""
        if len(blocks) == 1:
            fallback_id = (
                base_id if ("_" in base_id and base_id.split("_")[-1].isdigit()) else f"{base_id}_0"
            )
            blocks[0].block_id = fallback_id
            return blocks

        for idx, b in enumerate(blocks):
            b.block_id = f"{base_id}_{idx}"
        return blocks
