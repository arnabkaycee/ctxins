"""Unit tests for HeaderSanitizer and PayloadSanitizer."""

import copy

import pytest

from src.interceptor.filter.sanitizer import (
    HeaderSanitizer,
    PayloadSanitizer,
    sanitize_headers,
    sanitize_payload,
)


class TestHeaderSanitizer:
    """Test suite for HeaderSanitizer credential masking."""

    @pytest.fixture
    def sanitizer(self) -> HeaderSanitizer:
        return HeaderSanitizer()

    def test_masks_sensitive_headers(self, sanitizer: HeaderSanitizer):
        headers = {
            "Authorization": "Bearer sk-proj-1234567890abcdef",
            "x-api-key": "secret-anthropic-key-999",
            "api-key": "azure-secret-key-888",
            "Proxy-Authorization": "Basic dXNlcjpwYXNz",
            "Cookie": "session_id=abcdef; token=12345",
            "Set-Cookie": "auth_cookie=deadbeef; Path=/",
        }
        sanitized = sanitizer.sanitize_headers(headers)

        assert sanitized["Authorization"] == "[REDACTED]"
        assert sanitized["x-api-key"] == "[REDACTED]"
        assert sanitized["api-key"] == "[REDACTED]"
        assert sanitized["Proxy-Authorization"] == "[REDACTED]"
        assert sanitized["Cookie"] == "[REDACTED]"
        assert sanitized["Set-Cookie"] == "[REDACTED]"

    def test_preserves_diagnostic_and_standard_headers(self, sanitizer: HeaderSanitizer):
        headers = {
            "anthropic-beta": "prompt-caching-2024-07-25,tools-2024-05-16",
            "anthropic-version": "2023-06-01",
            "openai-organization": "org-deepmind-research",
            "x-request-id": "req-9876543210",
            "Content-Type": "application/json",
            "User-Agent": "ctxins-test/1.0",
            "Host": "api.anthropic.com",
        }
        sanitized = sanitizer.sanitize_headers(headers)

        assert sanitized["anthropic-beta"] == "prompt-caching-2024-07-25,tools-2024-05-16"
        assert sanitized["anthropic-version"] == "2023-06-01"
        assert sanitized["openai-organization"] == "org-deepmind-research"
        assert sanitized["x-request-id"] == "req-9876543210"
        assert sanitized["Content-Type"] == "application/json"
        assert sanitized["User-Agent"] == "ctxins-test/1.0"
        assert sanitized["Host"] == "api.anthropic.com"

    def test_custom_redaction_string_and_headers(self):
        custom = HeaderSanitizer(
            sensitive_headers=frozenset({"x-custom-secret"}),
            redaction_string="<HIDDEN>",
        )
        headers = {
            "x-custom-secret": "supersecret",
            "Authorization": "Bearer sk-123",
        }
        sanitized = custom.sanitize_headers(headers)
        assert sanitized["x-custom-secret"] == "<HIDDEN>"
        # Authorization was not in custom sensitive headers
        assert sanitized["Authorization"] == "Bearer sk-123"

    def test_convenience_sanitize_headers_function(self):
        headers = {"Authorization": "Bearer sk-123", "x-request-id": "req-1"}
        sanitized = sanitize_headers(headers)
        assert sanitized["Authorization"] == "[REDACTED]"
        assert sanitized["x-request-id"] == "req-1"


class TestPayloadSanitizer:
    """Test suite for PayloadSanitizer nested JSON redacting."""

    @pytest.fixture
    def sanitizer(self) -> PayloadSanitizer:
        return PayloadSanitizer()

    def test_sanitizes_api_key_and_token_patterns(self, sanitizer: PayloadSanitizer):
        payload = {
            "model": "claude-3-5-sonnet",
            "api_key": "sk-ant-12345",
            "apiKey": "sk-proj-67890",
            "auth_token": "token-xyz-abc",
            "access_token": "access-123",
            "user_token": "usr-tok-456",
            "nested": {
                "service_token": "serv-789",
                "custom_api_key": "custom-key",
            },
        }
        sanitized = sanitizer.sanitize(payload)

        assert sanitized["api_key"] == "[REDACTED]"
        assert sanitized["apiKey"] == "[REDACTED]"
        assert sanitized["auth_token"] == "[REDACTED]"
        assert sanitized["access_token"] == "[REDACTED]"
        assert sanitized["user_token"] == "[REDACTED]"
        assert sanitized["nested"]["service_token"] == "[REDACTED]"
        assert sanitized["nested"]["custom_api_key"] == "[REDACTED]"
        assert sanitized["model"] == "claude-3-5-sonnet"

    def test_preserves_token_count_and_metrics_keys(self, sanitizer: PayloadSanitizer):
        payload = {
            "max_tokens": 4096,
            "input_tokens": 120,
            "output_tokens": 80,
            "prompt_tokens": 120,
            "completion_tokens": 80,
            "total_tokens": 200,
            "reasoning_tokens": 30,
            "cache_creation_input_tokens": 50,
            "cache_read_input_tokens": 70,
            "token_count": 500,
        }
        sanitized = sanitizer.sanitize(payload)

        assert sanitized == payload

    def test_nested_arrays_and_dictionaries(self, sanitizer: PayloadSanitizer):
        payload = {
            "messages": [
                {"role": "system", "content": "You are a test assistant."},
                {
                    "role": "user",
                    "content": "Run tool",
                    "tool_calls": [
                        {
                            "name": "web_search",
                            "arguments": {
                                "query": "python",
                                "api_key": "search-secret",
                            },
                        },
                        {
                            "name": "auth_service",
                            "arguments": {
                                "client_token": "client-secret",
                            },
                        },
                    ],
                },
            ],
        }
        sanitized = sanitizer.sanitize(payload)

        # Ensure values in nested lists are properly redacted
        args0 = sanitized["messages"][1]["tool_calls"][0]["arguments"]
        assert args0["api_key"] == "[REDACTED]"
        assert args0["query"] == "python"

        args1 = sanitized["messages"][1]["tool_calls"][1]["arguments"]
        assert args1["client_token"] == "[REDACTED]"

    def test_does_not_mutate_original_payload(self, sanitizer: PayloadSanitizer):
        original = {
            "api_key": "secret-value",
            "nested": {"token": "secret-token"},
        }
        original_copy = copy.deepcopy(original)

        sanitized = sanitizer.sanitize(original)

        assert original == original_copy
        assert sanitized != original
        assert sanitized["api_key"] == "[REDACTED]"

    def test_primitives_pass_through_unchanged(self, sanitizer: PayloadSanitizer):
        assert sanitizer.sanitize("simple string") == "simple string"
        assert sanitizer.sanitize(42) == 42
        assert sanitizer.sanitize(3.14) == 3.14
        assert sanitizer.sanitize(True) is True
        assert sanitizer.sanitize(None) is None

    def test_convenience_sanitize_payload_function(self):
        payload = {"token": "my-secret", "normal": "ok"}
        sanitized = sanitize_payload(payload)
        assert sanitized["token"] == "[REDACTED]"
        assert sanitized["normal"] == "ok"
