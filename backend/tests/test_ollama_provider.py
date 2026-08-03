"""Hermetic tests for the Ollama provider (no real network)."""

from __future__ import annotations

import json

import httpx
import pytest

from cicero.providers.base import GenerateOptions, Message, ProviderError, Role
from cicero.providers.ollama import OllamaProvider
from cicero.providers.retry import RetryPolicy

OPTS = GenerateOptions(model="llama3", temperature=0.5, max_tokens=100)

# Retries are exercised here for behaviour, not pacing: a zero base delay keeps
# the suite fast (the schedule itself is unit-tested in test_retry.py).
INSTANT = RetryPolicy(max_attempts=3, base_delay_seconds=0, max_delay_seconds=0)
NO_RETRY = RetryPolicy(max_attempts=1)


def _provider(handler, retry: RetryPolicy = NO_RETRY) -> OllamaProvider:  # type: ignore[no-untyped-def]
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OllamaProvider(host="http://ollama:11434", client=client, retry=retry)


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


async def test_transient_failure_is_retried_then_succeeds() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if len(calls) < 3:
            return httpx.Response(503, json={"error": "overloaded"})
        return httpx.Response(200, json={"message": {"content": "Mars first."}})

    result = await _provider(handler, INSTANT).generate(
        [Message(role=Role.USER, content="go")], OPTS
    )

    assert result.content == "Mars first."
    assert len(calls) == 3  # two failures, then the answer


async def test_retries_are_capped_and_the_failure_still_surfaces() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(503, json={"error": "overloaded"})

    with pytest.raises(ProviderError, match="Ollama request failed"):
        await _provider(handler, INSTANT).generate(
            [Message(role=Role.USER, content="go")], OPTS
        )

    assert len(calls) == 3  # max_attempts, not forever


async def test_permanent_failure_is_not_retried() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(404, json={"error": "model not found"})

    with pytest.raises(ProviderError, match="Ollama request failed"):
        await _provider(handler, INSTANT).generate(
            [Message(role=Role.USER, content="go")], OPTS
        )

    # A missing model fails identically on a retry: one attempt only.
    assert len(calls) == 1
