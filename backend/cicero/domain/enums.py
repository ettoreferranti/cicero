"""Enumerations that make up the ubiquitous language of a debate."""

from __future__ import annotations

from enum import StrEnum


class Stance(StrEnum):
    """A participant's assigned position on the chamber's topic.

    ``NEUTRAL`` is the default when a participant is added (see FR-7).
    """

    PRO = "pro"
    CON = "con"
    NEUTRAL = "neutral"


class ChamberStatus(StrEnum):
    """Lifecycle state of a chamber (see FR-4).

    Allowed transitions are enforced by the chamber state machine, not here.
    """

    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    CONCLUDED = "concluded"
    ARCHIVED = "archived"


class ConsensusOutcome(StrEnum):
    """How a concluded debate ended (see FR-23 / FR-24).

    ``CONSENSUS`` is unanimous; ``MAJORITY`` means a plurality of final stances
    won; ``VERDICT`` means the moderator-as-judge picked the winner; only
    ``DISAGREEMENT`` ends without a winning position.
    """

    CONSENSUS = "consensus"
    MAJORITY = "majority"
    VERDICT = "verdict"
    DISAGREEMENT = "disagreement"


class DecisionRule(StrEnum):
    """How a debate is resolved when unanimity is not reached (FR-22..24).

    - ``UNANIMOUS``: only full agreement counts; otherwise a Summary of
      Disagreement is produced (no winner).
    - ``MAJORITY``: the plurality of final stances wins; a tie yields
      disagreement.
    - ``JUDGE``: like ``MAJORITY``, but on a tie the moderator weighs the
      arguments and declares a winner — the debate always ends with one
      position on top.
    """

    UNANIMOUS = "unanimous"
    MAJORITY = "majority"
    JUDGE = "judge"


class ProviderType(StrEnum):
    """Supported LLM provider integrations.

    ``MOCK`` is a deterministic, offline provider used for tests and demos; it
    never performs network calls.
    """

    OLLAMA = "ollama"
    ANTHROPIC = "anthropic"
    MOCK = "mock"
