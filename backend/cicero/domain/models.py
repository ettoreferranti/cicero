"""Domain entities and value objects.

Note on security: **no secrets live on these entities.** Provider credentials
(e.g. an Anthropic API key) are never stored on a ``Participant`` or ``Chamber``
— they are resolved at runtime from configuration/environment only (NFR-SEC-1/3).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

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


#: Default per-turn generation cap. Measured, not guessed: a debate *answer*
#: costs 150-500 tokens across every model tried, but a reasoning model spends
#: its budget on hidden thinking first — qwen3 used 510-1019 tokens thinking
#: before writing anything, 1460 in the worst observation. At the old default of
#: 800 its turns were being truncated mid-thought or lost entirely. This is a
#: cap and not a target: a model that finishes in 200 tokens still costs 200.
DEFAULT_MAX_TOKENS = 2048

#: A headline is one sentence. A longer value means the moderator wrote its
#: statement on the wrong line, so the value is dropped rather than truncated —
#: half a sentence presented as the chamber's conclusion is worse than none.
MAX_HEADLINE_LENGTH = 500


class ParticipantTuning(_Base):
    """Per-participant generation settings (see FR-11).

    ``persona`` says *who* the debater is; ``instructions`` says *how* it should
    behave while arguing ("be extra polite", "speak in rhyme", "always yield your
    position"). Both are injected into the turn prompt only — never into the
    stance poll, whose one-word answer they would corrupt.
    """

    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=DEFAULT_MAX_TOKENS, gt=0, le=32768)
    persona: str = Field(default="", max_length=2000)
    #: A directive, not prose — hence the tighter cap than ``persona``.
    #: ``style`` is the pre-D7 name: chambers persist as a JSON document and this
    #: model forbids extra keys, so without the alias every chamber stored before
    #: the rename would fail to load. Accepted on the way in, never written back.
    instructions: str = Field(
        default="",
        max_length=500,
        validation_alias=AliasChoices("instructions", "style"),
    )
    #: Whether this debater's model may "think" before writing its turn.
    #:
    #: ``True`` keeps the behaviour every chamber has had: reasoning is useful in
    #: a debate, and the defect it caused was that it was *unbudgeted*, not that
    #: it existed. Ollama counts reasoning tokens against the same budget as the
    #: reply but returns them in a separate field, so on a thinking model
    #: ``max_tokens`` is shared between invisible narration and the speech, in
    #: that order — measured on muse-glimmer:30b-mlx at 1400 tokens, three
    #: samples spent 3733-6319 characters thinking and returned 0-2635 of
    #: content, every one cut mid-sentence. Set ``False`` when the turn budget
    #: needs to mean the turn.
    allow_reasoning: bool = True


class DebateSettings(_Base):
    """Per-chamber debate tuning (FR-16, FR-11) — how long it runs and how it ends.

    Hard caps here bound every run (NFR-SEC-8); the API only accepts values in
    these ranges.
    """

    max_rounds: int = Field(default=8, gt=0, le=100)
    max_total_tokens: int = Field(default=200_000, gt=0, le=5_000_000)
    #: Wall-clock cap for the whole debate; ``None`` means no time limit.
    max_duration_seconds: float | None = Field(default=None, gt=0, le=86_400)
    #: Rounds that must run before the engine may look for an early stop. One
    #: round is only opening statements — nobody has answered anybody yet — and
    #: polling after it let a chamber record "consensus" the first time the pro
    #: side was out-argued, rounds before the convergence phase it was
    #: configured to reach. A default, not a lower bound on ``max_rounds``: a
    #: caller asking for a shorter debate gets one (see the validator).
    min_rounds: int = Field(default=3, ge=1)
    decision_rule: DecisionRule = DecisionRule.JUDGE
    #: How many closing rounds are steered toward common ground (0 disables).
    convergence_rounds: int = Field(default=2, ge=0, le=100)
    #: Per-chamber opt-in for web evidence (also needs the global flag, FR-26).
    web_evidence: bool = False
    #: End the debate when every active debater merely restates their previous
    #: turn. Models converge and then repeat; those rounds cost full price and
    #: add no argument.
    stop_on_repetition: bool = True
    #: How alike two turns must be to count as a repeat. 1.0 means byte-identical
    #: (after case/whitespace normalisation); lower catches a model that reworded
    #: one clause and said nothing new.
    repetition_threshold: float = Field(default=0.95, ge=0.5, le=1.0)
    #: Judge each turn for which side it actually argues (FR-34). One extra
    #: model call per turn — 24 on an 8-round debate with three debaters — so
    #: this is a real cost, not a free measurement. Off means no calls, no
    #: metadata, and a silent caveat.
    measure_compliance: bool = True

    @model_validator(mode="after")
    def _check_round_bounds(self) -> DebateSettings:
        if self.min_rounds > self.max_rounds:
            if "min_rounds" in self.model_fields_set:
                raise ValueError("min_rounds cannot exceed max_rounds")
            # Only the default overshot, so the shorter debate the caller
            # actually asked for wins: the floor gives way rather than
            # rejecting a value nobody set.
            self.min_rounds = self.max_rounds
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
    #: One declarative sentence stating what the chamber concluded — the TL;DR a
    #: stance word cannot carry. Empty when the moderator produced none that was
    #: usable; never fabricated from the statement.
    headline: str = Field(default="", max_length=MAX_HEADLINE_LENGTH)
    #: Ids whose final position could not be read, exactly as passed to
    #: ``ConsensusEngine.finalize``. ``None`` means *not recorded* — a chamber
    #: concluded before this field existed — which is a different claim from
    #: ``[]`` ("every position was read"). Kept here rather than derived from
    #: ``stance_history`` because the final poll is not always recorded there.
    unparsed: list[str] | None = None
    # Each participant's final stance, keyed by participant id (as string).
    final_stances: dict[str, Stance] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)


class Moderator(_Base):
    """Who writes the outcome. Not a debater: no stance, no turns, no vote.

    This used to be implicit — the engine took ``chamber.participants[0]``'s provider
    and model and paired them with two module-level constants in the API router. That
    made the arbiter depend on roster order, and made its token budget unreachable:
    at the old 2048, a reasoning model spent the whole budget thinking and returned
    empty content, producing no readable ``WINNER:`` on 5 of 9 measured judge calls.
    """

    provider: ProviderType
    model: str = Field(min_length=1, max_length=200)
    #: 4096 cleared all 9 measured judge calls where 2048 failed 5; 8192 gained
    #: nothing. A cap, not a target — a model that answers in 300 tokens costs 300.
    max_tokens: int = Field(default=4096, gt=0, le=32768)
    #: Was an invisible module constant. Exposed so a comparison run that wants
    #: reproducible verdicts can set it to 0.
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)


class Chamber(_Base):
    """A debate context: a topic plus its participants and transcript."""

    id: UUID = Field(default_factory=uuid4)
    topic: str = Field(min_length=1, max_length=1000)
    category: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=5000)
    status: ChamberStatus = ChamberStatus.DRAFT
    settings: DebateSettings = Field(default_factory=DebateSettings)
    #: Who writes the outcome. ``None`` means the first participant, which is
    #: what the engine did before this field existed.
    moderator: Moderator | None = None
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
