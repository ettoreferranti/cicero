"""Request payload schemas for the API.

Responses reuse the domain models directly (they are validated Pydantic models
carrying no secrets). These input schemas keep the write surface explicit and
validated (NFR-SEC-6).
"""

from __future__ import annotations

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from cicero.domain.enums import DecisionRule, ProviderType, Stance
from cicero.domain.models import DEFAULT_MAX_TOKENS


class DebateSettingsIn(BaseModel):
    """Per-chamber debate tuning (duration, budgets, resolution rule).

    Mirrors :class:`cicero.domain.models.DebateSettings`; ranges here are the
    API's hard caps (NFR-SEC-8).
    """

    model_config = ConfigDict(extra="forbid")

    max_rounds: int = Field(default=8, gt=0, le=100)
    max_total_tokens: int = Field(default=200_000, gt=0, le=5_000_000)
    max_duration_seconds: float | None = Field(default=None, gt=0, le=86_400)
    #: Rounds that must run before the engine may look for an early stop. One
    #: round is only opening statements — nobody has answered anybody yet — and
    #: polling after it let a chamber record "consensus" the first time the pro
    #: side was out-argued, rounds before the convergence phase it was
    #: configured to reach. A default, not a lower bound on ``max_rounds``: a
    #: caller asking for a shorter debate gets one (see the validator).
    min_rounds: int = Field(default=3, ge=1)
    decision_rule: DecisionRule = DecisionRule.JUDGE
    convergence_rounds: int = Field(default=2, ge=0, le=100)
    web_evidence: bool = False
    stop_on_repetition: bool = True
    repetition_threshold: float = Field(default=0.95, ge=0.5, le=1.0)
    measure_compliance: bool = True

    @model_validator(mode="after")
    def _check_round_bounds(self) -> DebateSettingsIn:
        if self.min_rounds > self.max_rounds:
            if "min_rounds" in self.model_fields_set:
                raise ValueError("min_rounds cannot exceed max_rounds")
            # Only the default overshot, so the shorter debate the caller
            # actually asked for wins: the floor gives way rather than
            # rejecting a value nobody set.
            self.min_rounds = self.max_rounds
        return self


class ModeratorIn(BaseModel):
    """Who writes the outcome (F8).

    Tuning is optional: unset fields fall through to the domain defaults rather
    than being pinned here, so the measured 4096-token judge budget lives in one
    place.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider: ProviderType
    model: str = Field(min_length=1, max_length=200)
    max_tokens: int | None = Field(default=None, gt=0, le=32768)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)


class ChamberCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    topic: str = Field(min_length=1, max_length=1000)
    category: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=5000)
    settings: DebateSettingsIn | None = None
    moderator: ModeratorIn | None = None


class ChamberUpdate(BaseModel):
    """Editable chamber fields while it is a `draft` (FR-3).

    Every field is optional; only those actually sent are applied, so a caller
    can retarget a topic without restating the rest.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    topic: str | None = Field(default=None, min_length=1, max_length=1000)
    category: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=5000)
    moderator: ModeratorIn | None = None


class ModeratorNoteIn(BaseModel):
    """An observer's note injected into the debate between turns (FR-21)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    content: str = Field(min_length=1, max_length=5000)


class TuningIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    # Shares the domain default rather than restating it: two copies of a
    # number like this drift, and the drift is invisible until a turn truncates.
    max_tokens: int = Field(default=DEFAULT_MAX_TOKENS, gt=0, le=32768)
    persona: str = Field(default="", max_length=2000)
    # `style` stays accepted as a deprecated alias so existing API clients (and
    # anything replaying an old export) keep working; responses only ever carry
    # `instructions`.
    instructions: str = Field(
        default="",
        max_length=500,
        validation_alias=AliasChoices("instructions", "style"),
    )
    # Mirrors the domain default. See ParticipantTuning.allow_reasoning: on a
    # thinking model max_tokens is otherwise shared with invisible reasoning,
    # so the turn gets whatever is left and is stored truncated without a word.
    allow_reasoning: bool = True


class ParticipantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    display_name: str = Field(min_length=1, max_length=120)
    provider: ProviderType
    model: str = Field(min_length=1, max_length=200)
    stance: Stance = Stance.NEUTRAL
    tuning: TuningIn | None = None


class MuteIn(BaseModel):
    """Mute or unmute a debater (FR-13)."""

    model_config = ConfigDict(extra="forbid")

    muted: bool


class ParticipantUpdate(BaseModel):
    """Editable participant fields while the chamber is a `draft` (FR-3).

    Every field is optional; only those actually sent are applied. ``tuning``
    replaces the whole tuning block rather than merging into it.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    provider: ProviderType | None = None
    model: str | None = Field(default=None, min_length=1, max_length=200)
    stance: Stance | None = None
    tuning: TuningIn | None = None
