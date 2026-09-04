"""Unit tests for ProviderRouter."""

import pytest

from src.interceptor.filter.provider_router import ProviderRouter
from src.schema.wire import Provider


@pytest.fixture
def router() -> ProviderRouter:
    return ProviderRouter()


class TestProviderRouter:
    """Test suite for ProviderRouter matching and rejection logic."""

    @pytest.mark.parametrize(
        ("host", "path", "expected_provider"),
        [
            ("api.anthropic.com", "/v1/messages", Provider.ANTHROPIC),
            ("api.anthropic.com:443", "/v1/messages", Provider.ANTHROPIC),
            ("API.ANTHROPIC.COM", "/v1/messages", Provider.ANTHROPIC),
            ("https://api.anthropic.com", "/v1/messages", Provider.ANTHROPIC),
            ("api.anthropic.com", "/v1/messages?beta=true", Provider.ANTHROPIC),
            ("api.anthropic.com", "/v1/messages/123", Provider.ANTHROPIC),
            ("api.openai.com", "/v1/chat/completions", Provider.OPENAI),
            ("api.openai.com:443", "/v1/chat/completions", Provider.OPENAI),
            ("api.openai.com", "/v1/responses", Provider.OPENAI),
            ("api.openai.com", "/v1/chat/completions?stream=true", Provider.OPENAI),
            (
                "generativelanguage.googleapis.com",
                "/v1beta/models/gemini-1.5-pro:generateContent",
                Provider.GEMINI,
            ),
            (
                "generativelanguage.googleapis.com:443",
                "/v1beta/models/gemini-1.5-flash:streamGenerateContent?alt=sse",
                Provider.GEMINI,
            ),
            (
                "custom-tenant.openai.azure.com",
                "/openai/deployments/gpt-4o/chat/completions?api-version=2024-02-15",
                Provider.AZURE_OPENAI,
            ),
            (
                "eastus.openai.azure.com:443",
                "/openai/deployments/deployment-1/chat/completions",
                Provider.AZURE_OPENAI,
            ),
            ("openrouter.ai", "/api/v1/chat/completions", Provider.OPENROUTER),
            ("openrouter.ai:443", "/api/v1/chat/completions?stream=true", Provider.OPENROUTER),
            ("localhost:11434", "/api/chat", Provider.OLLAMA),
            ("localhost:11434", "/v1/chat/completions", Provider.OLLAMA),
            ("127.0.0.1:11434", "/api/chat", Provider.OLLAMA),
            ("127.0.0.1:11434", "/v1/chat/completions", Provider.OLLAMA),
            ("localhost", "/api/chat", Provider.OLLAMA),
            ("127.0.0.1", "/api/chat", Provider.OLLAMA),
        ],
    )
    def test_known_llm_routes_match(
        self,
        router: ProviderRouter,
        host: str,
        path: str,
        expected_provider: Provider,
    ):
        is_match, provider = router.match(host, path)
        assert is_match is True
        assert provider == expected_provider
        assert router.is_llm_request(host, path) is True

    def test_port_argument_matching(self, router: ProviderRouter):
        is_match, provider = router.match("localhost", "/api/chat", port=11434)
        assert is_match is True
        assert provider == Provider.OLLAMA

        is_match, provider = router.match("api.anthropic.com", "/v1/messages", port=443)
        assert is_match is True
        assert provider == Provider.ANTHROPIC

    @pytest.mark.parametrize(
        ("host", "path"),
        [
            ("github.com", "/api/v3"),
            ("registry.npmjs.org", "/ctxins"),
            ("telemetry.anthropic.com", "/v1/events"),
            ("api.anthropic.com", "/v1/complete"),
            ("api.anthropic.com", "/v1/messages_suffix"),
            ("api.openai.com", "/v1/embeddings"),
            ("api.openai.com", "/v1/models"),
            ("generativelanguage.googleapis.com", "/v1beta/models"),
            ("unknown-host.com", "/v1/chat/completions"),
            ("google.com", "/search"),
            ("example.org", "/"),
        ],
    )
    def test_non_llm_and_unsupported_endpoints_rejected(
        self,
        router: ProviderRouter,
        host: str,
        path: str,
    ):
        is_match, provider = router.match(host, path)
        assert is_match is False
        assert provider == Provider.UNKNOWN
        assert router.is_llm_request(host, path) is False
