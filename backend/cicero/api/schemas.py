"""Request payload schemas for the API.

Responses reuse the domain models directly (they are validated Pydantic models
carrying no secrets). These input schemas keep the write surface explicit and
validated (NFR-SEC-6).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from cicero.domain.enums import ProviderType, Stance


class ChamberCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    topic: str = Field(min_length=1, max_length=1000)
    category: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=5000)


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
