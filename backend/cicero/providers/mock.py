"""A deterministic, offline provider for tests and demos.

``MockProvider`` performs **no network calls** and returns reproducible output,
so the debate engine, consensus logic, and stop conditions can be tested exactly
(NFR-Q-1/3). It can replay a scripted sequence of responses or fall back to a
deterministic, content-derived reply.
"""

from __future__ import annotations

from collections.abc import Sequence

from cicero.domain.enums import ProviderType
from cicero.providers.base import (
    GenerateOptions,
    GenerateResult,
    Message,
    Provider,
    ProviderError,
)

_DEFAULT_MODELS = ("mock-small", "mock-large")


def _count_tokens(text: str) -> int:
    """Deterministic, whitespace-based token estimate (never network-derived)."""
    return len(text.split())


class MockProvider(Provider):
    """Deterministic provider.

    Args:
        scripted: Replies returned in order; once exhausted, a deterministic
            content-derived reply is produced.
        models: Model identifiers reported by :meth:`list_models`.
        fail_after: If set, the ``fail_after``-th call (1-indexed) and beyond
            raise :class:`ProviderError`, to exercise failure handling (C5).
    """

    provider_type = ProviderType.MOCK

    def __init__(
        self,
        scripted: Sequence[str] | None = None,
        models: Sequence[str] | None = None,
        fail_after: int | None = None,
    ) -> None:
        self._scripted: list[str] = list(scripted or [])
        self._models: list[str] = list(models or _DEFAULT_MODELS)
        self._fail_after = fail_after
        self.call_count = 0

    async def generate(
        self, messages: list[Message], options: GenerateOptions
    ) -> GenerateResult:
        self.call_count += 1
        if self._fail_after is not None and self.call_count >= self._fail_after:
            raise ProviderError("mock provider failure (simulated)")

        if self._scripted:
            content = self._scripted.pop(0)
        else:
            last = messages[-1].content if messages else ""
            snippet = last[:80]
            content = f"[mock:{options.model}] response to: {snippet}"

        prompt_tokens = sum(_count_tokens(m.content) for m in messages)
        return GenerateResult(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=_count_tokens(content),
            metadata={"provider": self.provider_type.value, "call": self.call_count},
        )

    async def list_models(self) -> list[str]:
        return list(self._models)
