"""Request payload schemas for the API.

Responses reuse the domain models directly (they are validated Pydantic models
carrying no secrets). These input schemas keep the write surface explicit and
validated (NFR-SEC-6).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cicero.domain.enums import DecisionRule, ProviderType, Stance


class DebateSettingsIn(BaseModel):
    """Per-chamber debate tuning (duration, budgets, resolution rule).

    Mirrors :class:`cicero.domain.models.DebateSettings`; ranges here are the
    API's hard caps (NFR-SEC-8).
    """

    model_config = ConfigDict(extra="forbid")

    max_rounds: int = Field(default=8, gt=0, le=100)
    max_total_tokens: int = Field(default=200_000, gt=0, le=5_000_000)
    max_duration_seconds: float | None = Field(default=None, gt=0, le=86_400)
    min_rounds: int = Field(default=1, ge=1)
    decision_rule: DecisionRule = DecisionRule.JUDGE
    convergence_rounds: int = Field(default=2, ge=0, le=100)
    web_evidence: bool = False

    @model_validator(mode="after")
    def _check_round_bounds(self) -> DebateSettingsIn:
        if self.min_rounds > self.max_rounds:
            raise ValueError("min_rounds cannot exceed max_rounds")
        return self


class ChamberCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    topic: str = Field(min_length=1, max_length=1000)
    category: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=5000)
    settings: DebateSettingsIn | None = None


class ChamberUpdate(BaseModel):
    """Editable chamber fields while it is a `draft` (FR-3).

    Every field is optional; only those actually sent are applied, so a caller
    can retarget a topic without restating the rest.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    topic: str | None = Field(default=None, min_length=1, max_length=1000)
    category: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=5000)


class ModeratorNoteIn(BaseModel):
    """An observer's note injected into the debate between turns (FR-21)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    content: str = Field(min_length=1, max_length=5000)


class TuningIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=800, gt=0, le=32768)
    persona: str = Field(default="", max_length=2000)
    style: str = Field(default="", max_length=500)


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
