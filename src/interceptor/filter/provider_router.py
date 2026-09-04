"""ProviderRouter for LLM host and path endpoint recognition."""

from __future__ import annotations

import re
import urllib.parse
from typing import Tuple

from src.schema.wire import Provider


class ProviderRouter:
    """Matches incoming requests against recognized LLM provider hosts and paths."""

    # Compiled route patterns: (host_regex, path_regex, Provider)
    ROUTES: list[Tuple[re.Pattern[str], re.Pattern[str], Provider]] = [
        (
            re.compile(r"^api\.anthropic\.com(:443)?$", re.IGNORECASE),
            re.compile(r"^/v1/messages(?=[/?#]|$)"),
            Provider.ANTHROPIC,
        ),
        (
            re.compile(r"^api\.openai\.com(:443)?$", re.IGNORECASE),
            re.compile(r"^/v1/(chat/completions|responses)(?=[/?#]|$)"),
            Provider.OPENAI,
        ),
        (
            re.compile(r"^([a-zA-Z0-9_.-]+\.)?googleapis\.com(:443)?$", re.IGNORECASE),
            re.compile(r"^/.*:(generateContent|streamGenerateContent)(?=[/?#]|$)"),
            Provider.GEMINI,
        ),
        (
            re.compile(r"^.*\.openai\.azure\.com(:443)?$", re.IGNORECASE),
            re.compile(r"^/openai/deployments/.*/chat/completions(?=[/?#]|$)"),
            Provider.AZURE_OPENAI,
        ),
        (
            re.compile(r"^openrouter\.ai(:443)?$", re.IGNORECASE),
            re.compile(r"^/api/v1/chat/completions(?=[/?#]|$)"),
            Provider.OPENROUTER,
        ),
        (
            re.compile(r"^(localhost|127\.0\.0\.1)(:11434)?$", re.IGNORECASE),
            re.compile(r"^/(api/chat|v1/chat/completions)(?=[/?#]|$)"),
            Provider.OLLAMA,
        ),
    ]

    def _normalize_host(self, host: str, port: int | None = None) -> str:
        """Normalize host string, stripping protocols and handling ports."""
        host_str = host.strip()
        if "://" in host_str:
            parsed = urllib.parse.urlsplit(host_str)
            host_str = parsed.netloc

        if port is not None and ":" not in host_str:
            host_str = f"{host_str}:{port}"

        return host_str

    def match(
        self, host: str, path: str, port: int | None = None
    ) -> Tuple[bool, Provider]:
        """Match an incoming request host and path against recognized LLM endpoints.

        Args:
            host: Target host (e.g. 'api.anthropic.com', 'localhost:11434').
            path: Target request path (e.g. '/v1/messages', '/v1/chat/completions').
            port: Optional port number if host does not contain it.

        Returns:
            Tuple of (is_match, Provider). If no match, returns (False, Provider.UNKNOWN).
        """
        normalized_host = self._normalize_host(host, port)
        cleaned_path = path.strip()

        for host_regex, path_regex, provider in self.ROUTES:
            if host_regex.match(normalized_host) and path_regex.match(cleaned_path):
                return True, provider

        # Also attempt match without port if host had standard port (:443 or :80)
        if ":" in normalized_host and not normalized_host.endswith(":11434"):
            host_without_port = normalized_host.split(":", 1)[0]
            for host_regex, path_regex, provider in self.ROUTES:
                if host_regex.match(host_without_port) and path_regex.match(cleaned_path):
                    return True, provider

        return False, Provider.UNKNOWN

    def is_llm_request(
        self, host: str, path: str, port: int | None = None
    ) -> bool:
        """Check if request matches a known LLM provider endpoint."""
        is_match, _ = self.match(host, path, port)
        return is_match
