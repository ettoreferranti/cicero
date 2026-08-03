"""Ollama provider — talks to a local Ollama server over HTTP (FR-8).

The HTTP client is injectable so tests can run hermetically against a
``httpx.MockTransport`` with no real network access.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

import httpx

from cicero.domain.enums import ProviderType
from cicero.providers.base import (
    GenerateOptions,
    GenerateResult,
    Message,
    Provider,
    ProviderError,
)
from cicero.providers.retry import RetryPolicy, call_with_retry


def _content_of(data: dict[str, Any]) -> str:
    try:
        return cast(str, data["message"]["content"])
    except (KeyError, TypeError) as exc:
        raise ProviderError("unexpected Ollama response shape") from exc


def _thought_itself_silent(data: dict[str, Any], content: str) -> bool:
    """Truncated before saying anything — all budget spent in `message.thinking`."""
    return not content.strip() and data.get("done_reason") == "length"


class OllamaProvider(Provider):
    """Generate turns using a local Ollama server (default ``localhost:11434``)."""

    provider_type = ProviderType.OLLAMA

    def __init__(
        self,
        host: str = "http://localhost:11434",
        client: httpx.AsyncClient | None = None,
        timeout: float = 120.0,
        retry: RetryPolicy | None = None,
    ) -> None:
        self._host = host.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._retry = retry or RetryPolicy()

    async def generate(
        self, messages: list[Message], options: GenerateOptions
    ) -> GenerateResult:
        payload: dict[str, Any] = {
            "model": options.model,
            "messages": [{"role": m.role.value, "content": m.content} for m in messages],
            "stream": False,
            "options": {
                "temperature": options.temperature,
                "num_predict": options.max_tokens,
            },
        }
        if not options.allow_reasoning:
            # Ollama keeps reasoning in a separate `message.thinking` field. A
            # thinking model asked for one word can burn the whole num_predict
            # budget there and return empty content (qwen3 does exactly that on
            # a stance poll). Models without a reasoning mode ignore this.
            payload["think"] = False
        data = await self._post_json("/api/chat", payload)
        content = _content_of(data)

        if _thought_itself_silent(data, content) and options.allow_reasoning:
            # The model spent its whole budget thinking and said nothing. Rather
            # than lose the turn, ask again without reasoning: a plainer answer
            # beats no answer, and the caller still gets *something* to record.
            payload["think"] = False
            data = await self._post_json("/api/chat", payload)
            content = _content_of(data)

        if _thought_itself_silent(data, content):
            # Silence caused by truncation is a failure, not an empty opinion —
            # say so instead of handing back "" for the caller to misread.
            raise ProviderError(
                "Ollama response truncated before any content was produced "
                f"(model {options.model!r} spent its token budget reasoning); "
                "raise this participant's max tokens"
            )
        return GenerateResult(
            content=content,
            prompt_tokens=int(data.get("prompt_eval_count", 0)),
            completion_tokens=int(data.get("eval_count", 0)),
            metadata={"provider": self.provider_type.value, "model": options.model},
        )

    async def list_models(self) -> list[str]:
        data = await self._get_json("/api/tags")
        return [m["name"] for m in data.get("models", [])]

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post_json(self, path: str, payload: dict[str, object]) -> dict[str, Any]:
        async def send() -> dict[str, Any]:
            response = await self._client.post(f"{self._host}{path}", json=payload)
            response.raise_for_status()
            return cast(dict[str, Any], response.json())

        return await self._request(send)

    async def _get_json(self, path: str) -> dict[str, Any]:
        async def send() -> dict[str, Any]:
            response = await self._client.get(f"{self._host}{path}")
            response.raise_for_status()
            return cast(dict[str, Any], response.json())

        return await self._request(send)

    async def _request(
        self, send: Callable[[], Awaitable[dict[str, Any]]]
    ) -> dict[str, Any]:
        """Send with retries, then translate any surviving failure (NFR-R-1)."""
        try:
            return await call_with_retry(send, self._retry)
        except httpx.HTTPError as exc:
            # Do not include headers/payload — avoid leaking anything sensitive.
            raise ProviderError(f"Ollama request failed: {type(exc).__name__}") from exc
