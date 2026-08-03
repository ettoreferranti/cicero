"""Shared test fixtures and deterministic fakes for the debate engine."""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import UUID

import pytest

from cicero.domain.enums import ProviderType, Stance
from cicero.domain.models import Chamber, Participant
from cicero.providers.base import (
    GenerateOptions,
    GenerateResult,
    Message,
    Provider,
)

_POLL_MARKER = "reply with exactly one word"

#: Distinct follow-up points, so consecutive fixture turns are not near-copies.
_NEW_POINTS = (
    "Consider the economic evidence, which cuts the other way.",
    "The historical precedent undermines that reasoning.",
    "Let me address the strongest objection raised so far.",
    "A different framing makes the trade-off clearer.",
    "The empirical record complicates that conclusion.",
    "There is a practical obstacle nobody has mentioned.",
)


def _new_point(turn: int) -> str:
    return _NEW_POINTS[(turn - 1) % len(_NEW_POINTS)]


@pytest.fixture(autouse=True, scope="session")
def _never_touch_the_real_database(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[None]:
    """Point the whole session at a throwaway database.

    Most API tests override ``get_repository``, but any test that builds an app
    without doing so silently resolves the *real* singleton — which reads
    ``DATABASE_URL``, defaulting to the working ``cicero.db``. One such test
    wrote junk chambers into a developer's actual database before this existed.
    Isolating it here makes that impossible rather than a rule every future test
    has to remember.
    """
    from cicero.api.dependencies import reset_dependency_caches
    from cicero.config import get_settings

    database = tmp_path_factory.mktemp("cicero-db") / "test.db"
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite:///{database}"
    get_settings.cache_clear()
    reset_dependency_caches()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        get_settings.cache_clear()
        reset_dependency_caches()


class ScriptedProvider(Provider):
    """A deterministic provider that distinguishes stance polls from turns.

    For a stance poll it returns ``stance_word``; for a debate turn it returns
    ``argument``; otherwise (moderator) it returns ``moderator_reply``. No network.
    """

    provider_type = ProviderType.MOCK

    def __init__(
        self,
        stance_word: str = "neutral",
        argument: str = "Here is my argument.",
        moderator_reply: str = "FINAL STATEMENT.",
    ) -> None:
        self.stance_word = stance_word
        self.argument = argument
        self.moderator_reply = moderator_reply
        self.turn_calls = 0
        self.poll_calls = 0

    async def generate(
        self, messages: list[Message], options: GenerateOptions
    ) -> GenerateResult:
        text = messages[-1].content.lower()
        system = messages[0].content.lower() if messages else ""
        if _POLL_MARKER in text:
            self.poll_calls += 1
            return GenerateResult(content=self.stance_word, prompt_tokens=1, completion_tokens=1)
        if "moderator" in system:
            return GenerateResult(
                content=self.moderator_reply, prompt_tokens=3, completion_tokens=3
            )
        self.turn_calls += 1
        # Each turn says something genuinely different, because a real debater
        # does not restate itself word for word — and a fixture that did would
        # put every test in the state the engine now treats as terminal
        # (StopReason.REPETITION). A counter suffix is not enough: two strings
        # differing by one character are ~0.96 similar, which still reads as a
        # repeat. Tests wanting a repeating debater use RepeatingProvider.
        return GenerateResult(
            content=f"{self.argument} {_new_point(self.turn_calls)}",
            prompt_tokens=5,
            completion_tokens=5,
        )

    async def list_models(self) -> list[str]:
        # Two models so tests can exercise a real model *swap* against the
        # add/edit availability check (C4/FR-12).
        return ["scripted", "scripted-large"]


class RepeatingProvider(ScriptedProvider):
    """A debater that restates itself verbatim once it has run out of things to say.

    Models really do this: qwen3 re-emitted its previous turn word for word once
    a transcript had converged.
    """

    def __init__(self, stance_word: str = "neutral", fresh_turns: int = 1) -> None:
        super().__init__(stance_word=stance_word)
        self._fresh_turns = fresh_turns

    async def generate(
        self, messages: list[Message], options: GenerateOptions
    ) -> GenerateResult:
        if _POLL_MARKER in messages[-1].content.lower():
            return await super().generate(messages, options)
        self.turn_calls += 1
        # Fresh points until the allowance runs out, then the same turn forever.
        index = min(self.turn_calls, self._fresh_turns)
        return GenerateResult(
            content=f"{self.argument} {_new_point(index)}",
            prompt_tokens=5,
            completion_tokens=5,
        )


class StubFactory:
    """Provider factory returning a fixed provider per participant id."""

    def __init__(self, providers: dict[UUID, Provider]) -> None:
        self._providers = providers

    def get(self, participant: Participant) -> Provider:
        return self._providers[participant.id]

    def get_for_type(self, provider_type: ProviderType) -> Provider:
        return next(iter(self._providers.values()))


class ConstantFactory:
    """Provider factory returning the same provider for every participant."""

    def __init__(self, provider: Provider) -> None:
        self._provider = provider

    def get(self, participant: Participant) -> Provider:
        return self._provider

    def get_for_type(self, provider_type: ProviderType) -> Provider:
        return self._provider


def make_participant(name: str, stance: Stance) -> Participant:
    return Participant(
        display_name=name,
        provider=ProviderType.MOCK,
        model="scripted",
        stance=stance,
    )


def make_chamber(*participants: Participant, topic: str = "Should we colonise Mars?") -> Chamber:
    return Chamber(topic=topic, participants=list(participants))
