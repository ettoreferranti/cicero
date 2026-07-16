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
    """How a concluded debate ended (see FR-23 / FR-24)."""

    CONSENSUS = "consensus"
    DISAGREEMENT = "disagreement"


class ProviderType(StrEnum):
    """Supported LLM provider integrations.

    ``MOCK`` is a deterministic, offline provider used for tests and demos; it
    never performs network calls.
    """

    OLLAMA = "ollama"
    ANTHROPIC = "anthropic"
    MOCK = "mock"
