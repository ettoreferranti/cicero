"""Domain entities and value objects.

Note on security: **no secrets live on these entities.** Provider credentials
(e.g. an Anthropic API key) are never stored on a ``Participant`` or ``Chamber``
— they are resolved at runtime from configuration/environment only (NFR-SEC-1/3).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cicero.domain.enums import (
    ChamberStatus,
    ConsensusOutcome,
    DecisionRule,
    ProviderType,
    Stance,
)


def _utcnow() -> datetime:
    """Timezone-aware UTC now. Injected/overridable in tests for determinism."""
    return datetime.now(UTC)


class _Base(BaseModel):
    """Shared model config."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class Citation(_Base):
    """A web source used to support an argument (see FR-28)."""

    id: UUID = Field(default_factory=uuid4)
    url: str = Field(min_length=1, max_length=2048)
    title: str = Field(default="", max_length=512)
    excerpt: str = Field(default="", max_length=4096)


class ParticipantTuning(_Base):
    """Per-participant generation settings (see FR-11)."""

    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=800, gt=0, le=32768)
    persona: str = Field(default="", max_length=2000)
    style: str = Field(default="", max_length=500)


class DebateSettings(_Base):
    """Per-chamber debate tuning (FR-16, FR-11) — how long it runs and how it ends.

    Hard caps here bound every run (NFR-SEC-8); the API only accepts values in
    these ranges.
    """

    max_rounds: int = Field(default=8, gt=0, le=100)
    max_total_tokens: int = Field(default=200_000, gt=0, le=5_000_000)
    #: Wall-clock cap for the whole debate; ``None`` means no time limit.
    max_duration_seconds: float | None = Field(default=None, gt=0, le=86_400)
    min_rounds: int = Field(default=1, ge=1)
    decision_rule: DecisionRule = DecisionRule.JUDGE
    #: How many closing rounds are steered toward common ground (0 disables).
    convergence_rounds: int = Field(default=2, ge=0, le=100)
    #: Per-chamber opt-in for web evidence (also needs the global flag, FR-26).
    web_evidence: bool = False

    @model_validator(mode="after")
    def _check_round_bounds(self) -> DebateSettings:
        if self.min_rounds > self.max_rounds:
            raise ValueError("min_rounds cannot exceed max_rounds")
        return self


class Participant(_Base):
    """An LLM instance in a chamber, bound to a provider, model and stance."""

    id: UUID = Field(default_factory=uuid4)
    display_name: str = Field(min_length=1, max_length=120)
    provider: ProviderType
    model: str = Field(min_length=1, max_length=200)
    stance: Stance = Stance.NEUTRAL
    tuning: ParticipantTuning = Field(default_factory=ParticipantTuning)
    #: A muted debater stops taking turns and stops counting toward the decision
    #: rule, but stays in the chamber and is still polled for its stance (FR-13).
    muted: bool = False


class Turn(_Base):
    """One contribution to the group chat (see FR-17).

    ``participant_id`` is ``None`` for system-authored turns — moderator notes
    (FR-21) and injected web evidence (FR-28); ``metadata["kind"]`` says which.
    """

    id: UUID = Field(default_factory=uuid4)
    participant_id: UUID | None = None
    round_index: int = Field(ge=0)
    content: str = Field(default="", max_length=100_000)
    citations: list[Citation] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)


class StancePoll(_Base):
    """Every participant's stance as measured after one round (FR-25).

    The engine polls stances to decide convergence; recording each poll turns
    that throwaway signal into the debate's trajectory — who moved, and when.
    """

    round_index: int = Field(ge=0)
    #: Stance per participant id (as a string), matching ``ConsensusResult``.
    stances: dict[str, Stance] = Field(default_factory=dict)
    #: Ids whose reply could not be read; their stance here is carried over from
    #: the previous poll rather than measured, so it is not evidence of anything.
    unparsed: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)


class ConsensusResult(_Base):
    """The terminal artifact of a debate (see FR-23 / FR-24)."""

    id: UUID = Field(default_factory=uuid4)
    outcome: ConsensusOutcome
    statement: str = Field(min_length=1, max_length=50_000)
    #: The position that prevailed; ``None`` only when the debate ended in
    #: disagreement (or a judge verdict could not name a side).
    winning_stance: Stance | None = None
    # Each participant's final stance, keyed by participant id (as string).
    final_stances: dict[str, Stance] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)


class Chamber(_Base):
    """A debate context: a topic plus its participants and transcript."""

    id: UUID = Field(default_factory=uuid4)
    topic: str = Field(min_length=1, max_length=1000)
    category: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=5000)
    status: ChamberStatus = ChamberStatus.DRAFT
    settings: DebateSettings = Field(default_factory=DebateSettings)
    config: dict[str, object] = Field(default_factory=dict)
    participants: list[Participant] = Field(default_factory=list)
    turns: list[Turn] = Field(default_factory=list)
    #: One entry per stance poll the engine took, oldest first (FR-25).
    stance_history: list[StancePoll] = Field(default_factory=list)
    consensus: ConsensusResult | None = None
    created_at: datetime = Field(default_factory=_utcnow)

    def participant_by_id(self, participant_id: UUID) -> Participant | None:
        """Return the participant with ``participant_id``, or ``None``."""
        for participant in self.participants:
            if participant.id == participant_id:
                return participant
        return None
