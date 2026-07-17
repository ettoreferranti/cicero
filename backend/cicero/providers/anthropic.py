"""Anthropic provider — talks to the Anthropic Messages API (FR-9).

The API key is supplied only via configuration/environment and is never logged
or included in errors (NFR-SEC-1/3). The HTTP client is injectable for hermetic
tests.
"""

from __future__ import annotations

from typing import Any, cast

import httpx

from cicero.domain.enums import ProviderType
from cicero.providers.base import (
    GenerateOptions,
    GenerateResult,
    Message,
    Provider,
    ProviderError,
    Role,
)

_BASE_URL = "https://api.anthropic.com"
_API_VERSION = "2023-06-01"


class AnthropicProvider(Provider):
    """Generate turns using Anthropic's Messages API."""

    provider_type = ProviderType.ANTHROPIC

    def __init__(
        self,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        timeout: float = 120.0,
        base_url: str = _BASE_URL,
    ) -> None:
        if not api_key:
            raise ProviderError("Anthropic API key is not configured")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout)

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": _API_VERSION,
            "content-type": "application/json",
        }

    async def generate(
        self, messages: list[Message], options: GenerateOptions
    ) -> GenerateResult:
        system = "\n\n".join(m.content for m in messages if m.role is Role.SYSTEM)
        convo = [
            {"role": m.role.value, "content": m.content}
            for m in messages
            if m.role is not Role.SYSTEM
        ]
        payload: dict[str, object] = {
            "model": options.model,
            "max_tokens": options.max_tokens,
            "temperature": options.temperature,
            "messages": convo,
        }
        if system:
            payload["system"] = system

        data = await self._post_json("/v1/messages", payload)
        content = _extract_text(data)
        usage = data.get("usage") or {}
        return GenerateResult(
            content=content,
            prompt_tokens=int(usage.get("input_tokens", 0)),
            completion_tokens=int(usage.get("output_tokens", 0)),
            metadata={"provider": self.provider_type.value, "model": options.model},
        )

    async def list_models(self) -> list[str]:
        data = await self._get_json("/v1/models")
        items = data.get("data", [])
        return [m["id"] for m in items if isinstance(m, dict) and "id" in m]

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post_json(self, path: str, payload: dict[str, object]) -> dict[str, Any]:
        try:
            response = await self._client.post(
                f"{self._base_url}{path}", json=payload, headers=self._headers()
            )
            response.raise_for_status()
            return cast(dict[str, Any], response.json())
        except httpx.HTTPError as exc:
            # Never surface headers (they carry the API key).
            raise ProviderError(f"Anthropic request failed: {type(exc).__name__}") from exc

    async def _get_json(self, path: str) -> dict[str, Any]:
        try:
            response = await self._client.get(
                f"{self._base_url}{path}", headers=self._headers()
            )
            response.raise_for_status()
            return cast(dict[str, Any], response.json())
        except httpx.HTTPError as exc:
            raise ProviderError(f"Anthropic request failed: {type(exc).__name__}") from exc


def _extract_text(data: dict[str, Any]) -> str:
    """Concatenate the text blocks from an Anthropic Messages response."""
    blocks = data.get("content", [])
    if not isinstance(blocks, list):
        raise ProviderError("unexpected Anthropic response shape")
    parts = [
        block["text"]
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text" and "text" in block
    ]
    return "".join(parts)
