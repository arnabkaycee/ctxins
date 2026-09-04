"""Unit tests for deterministic block content hasher."""

import unicodedata

from src.core.graph.hasher import compute_block_hash


def test_hasher_deterministic():
    text = "You are an intelligent coding assistant."
    hash1 = compute_block_hash(text)
    hash2 = compute_block_hash(text)
    assert hash1 == hash2
    assert len(hash1) == 64


def test_hasher_known_vectors():
    # Empty string standard SHA-256
    assert compute_block_hash("") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    # "abc" standard SHA-256
    assert compute_block_hash("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_hasher_unicode_nfc_normalization():
    # Decomposed: 'e' + combining acute accent
    decomposed = "re\u0301sume\u0301"
    # Composed: 'é' precomposed character
    composed = "\u00e9".join(["r", "sum", ""])

    assert decomposed != composed
    assert unicodedata.normalize("NFC", decomposed) == unicodedata.normalize("NFC", composed)

    hash_decomposed = compute_block_hash(decomposed)
    hash_composed = compute_block_hash(composed)
    assert hash_decomposed == hash_composed


def test_hasher_distinct_inputs():
    h1 = compute_block_hash("Hello World")
    h2 = compute_block_hash("Hello World!")
    assert h1 != h2


def test_hasher_non_string_coercion():
    h_int = compute_block_hash(12345)
    h_str = compute_block_hash("12345")
    assert h_int == h_str
