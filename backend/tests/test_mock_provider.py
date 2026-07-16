"""Tests for the deterministic mock provider."""

from __future__ import annotations

import pytest

from cicero.domain.enums import ProviderType
from cicero.providers import (
    GenerateOptions,
    Message,
    MockProvider,
    ProviderError,
    Role,
)

OPTS = GenerateOptions(model="mock-small")


def _msgs(text: str = "Make your case.") -> list[Message]:
    return [Message(role=Role.USER, content=text)]


async def test_scripted_responses_returned_in_order() -> None:
    provider = MockProvider(scripted=["first", "second"])
    r1 = await provider.generate(_msgs(), OPTS)
    r2 = await provider.generate(_msgs(), OPTS)
    assert r1.content == "first"
    assert r2.content == "second"


async def test_deterministic_fallback_is_content_derived() -> None:
    provider = MockProvider()
    result = await provider.generate(_msgs("Pineapple belongs on pizza."), OPTS)
    assert "Pineapple belongs on pizza." in result.content
    assert result.content.startswith("[mock:mock-small]")


async def test_fallback_snippet_is_truncated_to_80_chars() -> None:
    # Pins the [:80] truncation so an off-by-one is caught.
    provider = MockProvider()
    result = await provider.generate(_msgs("z" * 100), OPTS)
    assert result.content == "[mock:mock-small] response to: " + "z" * 80


async def test_fallback_with_no_messages_uses_empty_snippet() -> None:
    provider = MockProvider()
    result = await provider.generate([], OPTS)
    assert result.content == "[mock:mock-small] response to: "


async def test_result_metadata_keys() -> None:
    provider = MockProvider(scripted=["hi"])
    result = await provider.generate(_msgs(), OPTS)
    assert result.metadata["provider"] == "mock"
    assert result.metadata["call"] == 1


async def test_same_input_yields_same_output() -> None:
    a = await MockProvider().generate(_msgs("stable"), OPTS)
    b = await MockProvider().generate(_msgs("stable"), OPTS)
    assert a.content == b.content


async def test_token_counts_are_whitespace_based() -> None:
    provider = MockProvider(scripted=["three word reply"])
    result = await provider.generate(_msgs("one two"), OPTS)
    assert result.prompt_tokens == 2
    assert result.completion_tokens == 3


async def test_call_count_increments() -> None:
    provider = MockProvider(scripted=["a", "b"])
    await provider.generate(_msgs(), OPTS)
    await provider.generate(_msgs(), OPTS)
    assert provider.call_count == 2


async def test_fail_after_raises() -> None:
    provider = MockProvider(fail_after=2)
    await provider.generate(_msgs(), OPTS)  # call 1 ok
    with pytest.raises(ProviderError, match=r"^mock provider failure \(simulated\)$"):
        await provider.generate(_msgs(), OPTS)  # call 2 fails


async def test_list_models_defaults() -> None:
    assert await MockProvider().list_models() == ["mock-small", "mock-large"]


async def test_list_models_custom() -> None:
    assert await MockProvider(models=["x"]).list_models() == ["x"]


def test_provider_type() -> None:
    assert MockProvider().provider_type is ProviderType.MOCK
