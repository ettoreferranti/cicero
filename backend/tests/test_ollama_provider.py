"""Hermetic tests for the Ollama provider (no real network)."""

from __future__ import annotations

import json

import httpx
import pytest

from cicero.providers.base import GenerateOptions, Message, ProviderError, Role
from cicero.providers.ollama import OllamaProvider

OPTS = GenerateOptions(model="llama3", temperature=0.5, max_tokens=100)


def _provider(handler) -> OllamaProvider:  # type: ignore[no-untyped-def]
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OllamaProvider(host="http://ollama:11434", client=client)


async def test_generate_parses_response_and_sends_expected_payload() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"message": {"content": "Mars first."}, "prompt_eval_count": 7, "eval_count": 3},
        )

    result = await _provider(handler).generate([Message(role=Role.USER, content="go")], OPTS)

    assert captured["path"] == "/api/chat"
    assert captured["body"]["model"] == "llama3"
    assert captured["body"]["stream"] is False
    assert captured["body"]["options"]["num_predict"] == 100
    assert result.content == "Mars first."
    assert result.prompt_tokens == 7
    assert result.completion_tokens == 3


async def test_generate_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    with pytest.raises(ProviderError, match="Ollama request failed"):
        await _provider(handler).generate([Message(role=Role.USER, content="go")], OPTS)


async def test_generate_raises_on_unexpected_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    with pytest.raises(ProviderError, match="unexpected Ollama response shape"):
        await _provider(handler).generate([Message(role=Role.USER, content="go")], OPTS)


async def test_list_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": "llama3"}, {"name": "mistral"}]})

    assert await _provider(handler).list_models() == ["llama3", "mistral"]
