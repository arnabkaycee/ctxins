"""Header and payload redaction engine for sensitive credential sanitization."""

from __future__ import annotations

import re
from typing import Any, Mapping

REDACTED = "[REDACTED]"

# Headers containing sensitive authentication or session credentials
SENSITIVE_HEADERS: frozenset[str] = frozenset({
    "authorization",
    "x-api-key",
    "api-key",
    "proxy-authorization",
    "cookie",
    "set-cookie",
})

# Token counting / usage / configuration keys that must not be mistaken for credentials
EXCLUDED_PAYLOAD_KEYS: frozenset[str] = frozenset({
    "max_tokens",
    "input_tokens",
    "output_tokens",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "reasoning_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "tokens",
    "token_count",
    "token_usage",
})

# Pattern matching credentials like api_key, auth_token, accessToken, etc.
SENSITIVE_KEY_PATTERN: re.Pattern[str] = re.compile(
    r"(api[_-]?key|token)",
    re.IGNORECASE,
)


class HeaderSanitizer:
    """Redacts authentication keys while preserving diagnostic and operational headers."""

    SENSITIVE_HEADERS: frozenset[str] = SENSITIVE_HEADERS
    REDACTION_STRING: str = REDACTED

    def __init__(
        self,
        sensitive_headers: frozenset[str] | set[str] | None = None,
        redaction_string: str = REDACTED,
    ) -> None:
        self.sensitive_headers = (
            frozenset(h.lower() for h in sensitive_headers)
            if sensitive_headers is not None
            else self.SENSITIVE_HEADERS
        )
        self.redaction_string = redaction_string
        self._payload_sanitizer = PayloadSanitizer(redaction_string=redaction_string)

    def sanitize_headers(self, headers: Mapping[str, Any]) -> dict[str, str]:
        """Sanitize request/response headers, masking credentials with [REDACTED].

        Diagnostic headers such as 'anthropic-beta', 'anthropic-version',
        'openai-organization', and 'x-request-id' are preserved.

        Args:
            headers: Mapping or dict of header name-value pairs.

        Returns:
            Dictionary of headers with sensitive values redacted.
        """
        sanitized: dict[str, str] = {}
        for key, value in headers.items():
            str_key = str(key)
            lower_key = str_key.lower()
            str_val = str(value) if value is not None else ""
            if lower_key in self.sensitive_headers:
                sanitized[str_key] = self.redaction_string
            else:
                sanitized[str_key] = str_val
        return sanitized

    def sanitize(self, headers: Mapping[str, Any]) -> dict[str, str]:
        """Alias for sanitize_headers."""
        return self.sanitize_headers(headers)

    def sanitize_payload(self, payload: Any) -> Any:
        """Sanitize payload dictionary/data structures."""
        return self._payload_sanitizer.sanitize(payload)


class PayloadSanitizer:
    """Sanitizes nested dictionaries and lists by masking sensitive credential keys."""

    REDACTION_STRING: str = REDACTED

    def __init__(
        self,
        key_pattern: re.Pattern[str] | None = None,
        excluded_keys: frozenset[str] | None = None,
        redaction_string: str = REDACTED,
    ) -> None:
        self.key_pattern = key_pattern or SENSITIVE_KEY_PATTERN
        self.excluded_keys = (
            frozenset(k.lower() for k in excluded_keys)
            if excluded_keys is not None
            else EXCLUDED_PAYLOAD_KEYS
        )
        self.redaction_string = redaction_string

    def _is_sensitive_key(self, key: str) -> bool:
        lower_key = key.lower()
        if lower_key in self.excluded_keys:
            return False
        return bool(self.key_pattern.search(key))

    def sanitize(self, payload: Any) -> Any:
        """Recursively redact sensitive keys in nested dictionaries and lists.

        Args:
            payload: JSON-like structure (dict, list, primitive).

        Returns:
            Deep copy of payload with sensitive key values replaced by [REDACTED].
        """
        if isinstance(payload, dict):
            sanitized_dict: dict[str, Any] = {}
            for k, v in payload.items():
                str_key = str(k)
                if self._is_sensitive_key(str_key):
                    sanitized_dict[str_key] = self.redaction_string
                else:
                    sanitized_dict[str_key] = self.sanitize(v)
            return sanitized_dict

        if isinstance(payload, list):
            return [self.sanitize(item) for item in payload]

        if isinstance(payload, tuple):
            return tuple(self.sanitize(item) for item in payload)

        return payload

    def sanitize_payload(self, payload: Any) -> Any:
        """Alias for sanitize."""
        return self.sanitize(payload)


def sanitize_headers(headers: Mapping[str, Any]) -> dict[str, str]:
    """Convenience function to sanitize headers with default settings."""
    return HeaderSanitizer().sanitize_headers(headers)


def sanitize_payload(payload: Any) -> Any:
    """Convenience function to sanitize payload with default settings."""
    return PayloadSanitizer().sanitize(payload)
