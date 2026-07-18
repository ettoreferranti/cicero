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
    SearchExecutor,
)

_BASE_URL = "https://api.anthropic.com"
_API_VERSION = "2023-06-01"

# The sandboxed web-search tool exposed to the model (executed by Cicero's
# SSRF-guarded fetcher, never by the model itself).
_WEB_SEARCH_TOOL: dict[str, Any] = {
    "name": "web_search",
    "description": (
        "Search the public web for evidence to support your argument. Returns "
        "sanitised text excerpts with their source URLs."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query."}
        },
        "required": ["query"],
    },
}


class AnthropicProvider(Provider):
    """Generate turns using Anthropic's Messages API."""

    provider_type = ProviderType.ANTHROPIC
    supports_native_search = True

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

    async def generate_with_search(
        self,
        messages: list[Message],
        options: GenerateOptions,
        search: SearchExecutor,
        max_searches: int = 1,
    ) -> GenerateResult:
        """Run a native tool-use loop: the model may call ``web_search`` up to
        ``max_searches`` times; every query is executed by the caller-supplied
        (sandboxed) executor, never by the model."""
        system = "\n\n".join(m.content for m in messages if m.role is Role.SYSTEM)
        convo: list[dict[str, Any]] = [
            {"role": m.role.value, "content": m.content}
            for m in messages
            if m.role is not Role.SYSTEM
        ]
        prompt_tokens = completion_tokens = 0
        searches_used = 0

        # max_searches tool rounds plus the final answer (+1 safety margin).
        for _ in range(max_searches + 2):
            payload: dict[str, object] = {
                "model": options.model,
                "max_tokens": options.max_tokens,
                "temperature": options.temperature,
                "messages": convo,
                "tools": [_WEB_SEARCH_TOOL],
                # Once the search budget is spent, forbid further tool calls so
                # the model must answer.
                "tool_choice": {
                    "type": "auto" if searches_used < max_searches else "none"
                },
            }
            if system:
                payload["system"] = system

            data = await self._post_json("/v1/messages", payload)
            usage = data.get("usage") or {}
            prompt_tokens += int(usage.get("input_tokens", 0))
            completion_tokens += int(usage.get("output_tokens", 0))

            tool_use = _find_web_search_call(data)
            if data.get("stop_reason") != "tool_use" or tool_use is None:
                return GenerateResult(
                    content=_extract_text(data),
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    metadata={
                        "provider": self.provider_type.value,
                        "model": options.model,
                        "native_searches": searches_used,
                    },
                )

            query = str((tool_use.get("input") or {}).get("query") or "").strip()
            searches_used += 1
            result_text = await search(query) if query else "(empty search query)"
            convo.append({"role": "assistant", "content": data.get("content", [])})
            convo.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use.get("id"),
                            "content": result_text,
                        }
                    ],
                }
            )
        raise ProviderError("Anthropic tool-use loop did not terminate")

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


def _find_web_search_call(data: dict[str, Any]) -> dict[str, Any] | None:
    """The first ``web_search`` tool_use block in a response, if any."""
    blocks = data.get("content", [])
    if not isinstance(blocks, list):
        return None
    for block in blocks:
        if (
            isinstance(block, dict)
            and block.get("type") == "tool_use"
            and block.get("name") == _WEB_SEARCH_TOOL["name"]
        ):
            return block
    return None


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
