"""Hermetic tests for the Anthropic provider (no real network)."""

from __future__ import annotations

import json

import httpx
import pytest

from cicero.providers.anthropic import AnthropicProvider
from cicero.providers.base import GenerateOptions, Message, ProviderError, Role

OPTS = GenerateOptions(model="claude-x", temperature=0.5, max_tokens=100)


def _provider(handler, api_key: str = "test-key") -> AnthropicProvider:  # type: ignore[no-untyped-def]
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return AnthropicProvider(api_key=api_key, client=client, base_url="https://api.anthropic.com")


async def test_generate_splits_system_and_parses_text() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "Hello "}, {"type": "text", "text": "world"}],
                "usage": {"input_tokens": 11, "output_tokens": 4},
            },
        )

    messages = [
        Message(role=Role.SYSTEM, content="You are a debater."),
        Message(role=Role.USER, content="Argue."),
    ]
    result = await _provider(handler).generate(messages, OPTS)

    assert captured["path"] == "/v1/messages"
    assert captured["body"]["system"] == "You are a debater."
    assert captured["body"]["messages"] == [{"role": "user", "content": "Argue."}]
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert result.content == "Hello world"
    assert result.prompt_tokens == 11
    assert result.completion_tokens == 4


async def test_api_key_sent_as_header_but_not_leaked_in_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "secret-123"
        return httpx.Response(401, json={"error": "unauthorized"})

    with pytest.raises(ProviderError) as exc:
        await _provider(handler, api_key="secret-123").generate(
            [Message(role=Role.USER, content="x")], OPTS
        )
    assert "secret-123" not in str(exc.value)


async def test_missing_api_key_raises() -> None:
    with pytest.raises(ProviderError, match="API key is not configured"):
        AnthropicProvider(api_key="")


async def test_generate_raises_on_unexpected_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": "not-a-list"})

    with pytest.raises(ProviderError, match="unexpected Anthropic response shape"):
        await _provider(handler).generate([Message(role=Role.USER, content="x")], OPTS)


async def test_list_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"data": [{"id": "claude-x"}, {"id": "claude-y"}]})

    assert await _provider(handler).list_models() == ["claude-x", "claude-y"]
