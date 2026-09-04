"""Provider-specific AST normalizers converting wire payloads to CanonicalTurn."""

from typing import Union

from src.core.ast.normalizers.anthropic import AnthropicASTNormalizer
from src.core.ast.normalizers.base import BaseNormalizer
from src.core.ast.normalizers.gemini import GeminiASTNormalizer
from src.core.ast.normalizers.openai import OpenAIASTNormalizer
from src.schema.wire import Provider

__all__ = [
    "AnthropicASTNormalizer",
    "BaseNormalizer",
    "GeminiASTNormalizer",
    "OpenAIASTNormalizer",
    "get_normalizer",
]


def get_normalizer(provider: Union[str, Provider]) -> BaseNormalizer:
    """Return appropriate BaseNormalizer instance for given provider.

    Args:
        provider: Provider enum or string ("anthropic", "openai", "gemini", etc.).

    Returns:
        BaseNormalizer instance.

    Raises:
        ValueError: If provider is unrecognized.
    """
    prov_str = provider.value if isinstance(provider, Provider) else str(provider).lower()

    if prov_str in ("anthropic", "claude"):
        return AnthropicASTNormalizer()
    elif prov_str in ("openai", "azure_openai", "openrouter", "ollama"):
        return OpenAIASTNormalizer()
    elif prov_str in ("gemini", "google"):
        return GeminiASTNormalizer()
    else:
        raise ValueError(f"No AST normalizer implemented for provider: {provider}")
