"""Tests for the deterministic mock provider."""

from __future__ import annotations

import pytest

from cicero.core import prompts
from cicero.core.compliance import parse_judgement
from cicero.core.consensus import parse_stance
from cicero.core.prompt_builder import build_compliance_messages
from cicero.core.prompts import MODERATOR_SYSTEM
from cicero.domain.enums import ProviderType, Stance
from cicero.providers import (
    GenerateOptions,
    Message,
    MockProvider,
    ProviderError,
    Role,
)
from cicero.providers.mock import (
    COMPLIANCE_MARKER,
    MODERATOR_MARKER,
    POLL_MARKER,
    TRANSCRIPT_CLOSE_MARKER,
    TRANSCRIPT_OPEN_MARKER,
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


async def test_stance_poll_gets_an_answer_not_an_echo() -> None:
    # Regression: the mock used to echo the prompt, and the (substring) parser
    # matched pro/con out of the quoted transcript — so an offline debate's
    # stance poll was reading the transcript, never an actual position.
    poll = _msgs(
        "<<<TRANSCRIPT>>> [Ada (pro)]: I am for it. [Zeno (con)]: I am not.\n\n"
        "In light of the debate so far, reply with exactly one word — pro, con, "
        "or neutral — giving your current position on the motion."
    )
    result = await MockProvider().generate(poll, OPTS)
    assert result.content == "neutral"
    assert parse_stance(result.content) is Stance.NEUTRAL


async def test_stance_poll_answer_is_configurable() -> None:
    result = await MockProvider(poll_answer="con").generate(
        _msgs("reply with exactly one word — pro, con, or neutral"), OPTS
    )
    assert parse_stance(result.content) is Stance.CON


async def test_scripted_replies_still_win_over_the_poll_answer() -> None:
    provider = MockProvider(scripted=["pro"])
    result = await provider.generate(
        _msgs("reply with exactly one word — pro, con, or neutral"), OPTS
    )
    assert result.content == "pro"


async def test_ordinary_turns_are_unaffected() -> None:
    result = await MockProvider().generate(_msgs("Make your case."), OPTS)
    assert result.content.startswith("[mock:mock-small] response to:")


def test_poll_marker_still_matches_the_real_poll_prompt() -> None:
    # The marker is duplicated to keep the provider layer independent of
    # core.prompts; this is what stops the two drifting apart.
    assert POLL_MARKER in prompts.POLL_USER_INSTRUCTION.lower()


async def test_mock_answers_a_moderator_prompt_with_a_headline() -> None:
    provider = MockProvider()
    result = await provider.generate(
        [
            Message(role=Role.SYSTEM, content=MODERATOR_SYSTEM),
            Message(role=Role.USER, content="Motion: x\n\nWrite a CONSENSUS STATEMENT."),
        ],
        GenerateOptions(model="mock-small"),
    )
    # The offline acceptance path has to exercise the real directive parser, not
    # a shape that only looks like a moderator reply.
    assert result.content.startswith("HEADLINE: ")
    assert "\n" in result.content
    # Pins the full reply text, not just the prefix, so a mutation to the body
    # line (after the headline) is caught rather than passing on a partial match.
    assert result.content == (
        "HEADLINE: The chamber reached a deterministic mock outcome.\n"
        "This is a mock synthesis of the debate."
    )


async def test_mock_moderator_marker_matches_the_real_moderator_prompt() -> None:
    # Kept as a literal so the provider layer stays independent of core.prompts;
    # this is what stops the two drifting apart (same contract as POLL_MARKER).
    assert MODERATOR_MARKER in MODERATOR_SYSTEM.lower()


async def test_mock_still_echoes_for_a_debate_turn() -> None:
    provider = MockProvider()
    result = await provider.generate(
        [
            Message(role=Role.SYSTEM, content='You are "Ada", a participant.'),
            Message(role=Role.USER, content="Give your next contribution."),
        ],
        GenerateOptions(model="mock-small"),
    )
    assert result.content.startswith("[mock:mock-small]")


async def test_mock_scripted_replies_still_win_over_the_moderator_branch() -> None:
    provider = MockProvider(scripted=["exact reply"])
    result = await provider.generate(
        [
            Message(role=Role.SYSTEM, content=MODERATOR_SYSTEM),
            Message(role=Role.USER, content="Write a CONSENSUS STATEMENT."),
        ],
        GenerateOptions(model="mock-small"),
    )
    assert result.content == "exact reply"


async def test_mock_answers_the_compliance_question_with_a_stance() -> None:
    provider = MockProvider()
    messages = build_compliance_messages("a motion", "an argument")
    result = await provider.generate(messages, GenerateOptions(model="mock-small"))
    assert parse_stance(result.content) is not None


async def test_mock_compliance_answer_is_configurable() -> None:
    provider = MockProvider(compliance_answer="con")
    messages = build_compliance_messages("a motion", "an argument")
    result = await provider.generate(messages, GenerateOptions(model="mock-small"))
    assert parse_stance(result.content) is Stance.CON


def test_compliance_marker_matches_the_real_prompt() -> None:
    """The provider layer keeps its own literal so it does not import core.prompts;
    this is the test that stops the two drifting apart."""
    assert COMPLIANCE_MARKER in prompts.COMPLIANCE_SYSTEM.lower()


async def test_a_compliance_prompt_is_not_mistaken_for_a_stance_poll() -> None:
    """Both ask for one word. They must not collapse into the same branch, or the
    mock reports the debater's poll answer as the judge's reading."""
    provider = MockProvider(poll_answer="pro", compliance_answer="con")
    messages = build_compliance_messages("a motion", "an argument")
    result = await provider.generate(messages, GenerateOptions(model="mock-small"))
    assert parse_stance(result.content) is Stance.CON


async def test_mock_emits_a_grounded_two_directive_judgement() -> None:
    provider = MockProvider()
    content = "Invading would violate the UN Charter. I oppose the motion."
    messages = build_compliance_messages("a motion", content)
    result = await provider.generate(messages, GenerateOptions(model="mock-small"))
    assert parse_judgement(result.content, content) is not None


def test_transcript_markers_match_the_real_prompt() -> None:
    """The provider layer keeps its own literals so it does not import
    core.prompts; this is what stops the two drifting apart."""
    assert TRANSCRIPT_OPEN_MARKER == prompts.TRANSCRIPT_OPEN
    assert TRANSCRIPT_CLOSE_MARKER == prompts.TRANSCRIPT_CLOSE
