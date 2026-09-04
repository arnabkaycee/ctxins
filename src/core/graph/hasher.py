"""Deterministic SHA-256 content hasher for Canonical AST context blocks."""

from __future__ import annotations

import hashlib
import unicodedata


def compute_block_hash(content: str) -> str:
    """Compute deterministic SHA-256 hash of normalized content string.

    Normalizes Unicode representation to NFC form before hashing so that
    equivalent Unicode sequences produce identical digest strings.

    Args:
        content: The text content of the context block.

    Returns:
        Hex-encoded SHA-256 digest string (64 characters).
    """
    if not isinstance(content, str):
        content = str(content)
    normalized = unicodedata.normalize("NFC", content)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
