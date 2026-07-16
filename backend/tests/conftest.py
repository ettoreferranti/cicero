"""Shared test fixtures and deterministic fakes for the debate engine."""

from __future__ import annotations

from uuid import UUID

from cicero.domain.enums import ProviderType, Stance
from cicero.domain.models import Chamber, Participant
from cicero.providers.base import (
    GenerateOptions,
    GenerateResult,
    Message,
    Provider,
)

_POLL_MARKER = "reply with exactly one word"


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
        return GenerateResult(content=self.argument, prompt_tokens=5, completion_tokens=5)

    async def list_models(self) -> list[str]:
        return ["scripted"]


class StubFactory:
    """Provider factory returning a fixed provider per participant id."""

    def __init__(self, providers: dict[UUID, Provider]) -> None:
        self._providers = providers

    def get(self, participant: Participant) -> Provider:
        return self._providers[participant.id]


class ConstantFactory:
    """Provider factory returning the same provider for every participant."""

    def __init__(self, provider: Provider) -> None:
        self._provider = provider

    def get(self, participant: Participant) -> Provider:
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
