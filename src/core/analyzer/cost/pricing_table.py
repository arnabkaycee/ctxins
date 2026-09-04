"""Pricing catalog for LLM models with token costs per 1,000 tokens."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """Pricing rates in USD per 1,000 tokens."""

    model_name: str
    provider: str
    input_cost_per_1k: float
    output_cost_per_1k: float
    cache_write_cost_per_1k: float
    cache_read_cost_per_1k: float


# Catalog of standard models and pricing per 1,000 tokens (as of 2024-2026 rates)
PRICING_CATALOG: Dict[str, ModelPricing] = {
    # Anthropic Models
    "claude-3-5-sonnet": ModelPricing(
        model_name="claude-3-5-sonnet",
        provider="anthropic",
        input_cost_per_1k=0.003,
        output_cost_per_1k=0.015,
        cache_write_cost_per_1k=0.00375,
        cache_read_cost_per_1k=0.0003,
    ),
    "claude-3-5-haiku": ModelPricing(
        model_name="claude-3-5-haiku",
        provider="anthropic",
        input_cost_per_1k=0.0008,
        output_cost_per_1k=0.004,
        cache_write_cost_per_1k=0.001,
        cache_read_cost_per_1k=0.00008,
    ),
    "claude-3-opus": ModelPricing(
        model_name="claude-3-opus",
        provider="anthropic",
        input_cost_per_1k=0.015,
        output_cost_per_1k=0.075,
        cache_write_cost_per_1k=0.01875,
        cache_read_cost_per_1k=0.0015,
    ),
    # OpenAI Models
    "gpt-4o": ModelPricing(
        model_name="gpt-4o",
        provider="openai",
        input_cost_per_1k=0.0025,
        output_cost_per_1k=0.010,
        cache_write_cost_per_1k=0.0025,
        cache_read_cost_per_1k=0.00125,
    ),
    "gpt-4o-mini": ModelPricing(
        model_name="gpt-4o-mini",
        provider="openai",
        input_cost_per_1k=0.00015,
        output_cost_per_1k=0.0006,
        cache_write_cost_per_1k=0.00015,
        cache_read_cost_per_1k=0.000075,
    ),
    "o1": ModelPricing(
        model_name="o1",
        provider="openai",
        input_cost_per_1k=0.015,
        output_cost_per_1k=0.060,
        cache_write_cost_per_1k=0.015,
        cache_read_cost_per_1k=0.0075,
    ),
    "o1-mini": ModelPricing(
        model_name="o1-mini",
        provider="openai",
        input_cost_per_1k=0.003,
        output_cost_per_1k=0.012,
        cache_write_cost_per_1k=0.003,
        cache_read_cost_per_1k=0.0015,
    ),
    # Google Gemini Models
    "gemini-1.5-pro": ModelPricing(
        model_name="gemini-1.5-pro",
        provider="gemini",
        input_cost_per_1k=0.00125,
        output_cost_per_1k=0.005,
        cache_write_cost_per_1k=0.00125,
        cache_read_cost_per_1k=0.0003125,
    ),
    "gemini-1.5-flash": ModelPricing(
        model_name="gemini-1.5-flash",
        provider="gemini",
        input_cost_per_1k=0.000075,
        output_cost_per_1k=0.0003,
        cache_write_cost_per_1k=0.000075,
        cache_read_cost_per_1k=0.00001875,
    ),
}

DEFAULT_PRICING = ModelPricing(
    model_name="default",
    provider="default",
    input_cost_per_1k=0.003,
    output_cost_per_1k=0.015,
    cache_write_cost_per_1k=0.00375,
    cache_read_cost_per_1k=0.0003,
)


def get_pricing(model_name: Optional[str], provider: Optional[str] = None) -> ModelPricing:
    """Resolve model pricing from catalog using fuzzy matching or default.

    Args:
        model_name: Name of LLM model (e.g. 'claude-3-5-sonnet-20241022', 'gpt-4o').
        provider: Optional provider name for fallback.

    Returns:
        Matched ModelPricing record or default pricing.
    """
    if not model_name:
        return DEFAULT_PRICING

    normalized = model_name.strip().lower().replace(".", "-")

    # 1. Exact match
    if normalized in PRICING_CATALOG:
        return PRICING_CATALOG[normalized]

    # 2. Prefix / substring match
    for key, pricing in PRICING_CATALOG.items():
        if key in normalized or normalized.startswith(key):
            return pricing

    # 3. Provider-based fallback
    if provider:
        norm_prov = provider.strip().lower()
        if "anthropic" in norm_prov:
            return PRICING_CATALOG["claude-3-5-sonnet"]
        elif "openai" in norm_prov:
            return PRICING_CATALOG["gpt-4o"]
        elif "gemini" in norm_prov or "google" in norm_prov:
            return PRICING_CATALOG["gemini-1.5-pro"]

    return DEFAULT_PRICING
