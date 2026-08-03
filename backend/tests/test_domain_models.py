"""Tests for domain entities and their validation."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from cicero.domain import (
    Chamber,
    ChamberStatus,
    Citation,
    Participant,
    ProviderType,
    Stance,
    Turn,
)
from cicero.domain.models import ParticipantTuning


def _participant(**overrides: object) -> Participant:
    base: dict[str, object] = {
        "display_name": "Athena",
        "provider": ProviderType.MOCK,
        "model": "mock-small",
    }
    base.update(overrides)
    return Participant(**base)  # type: ignore[arg-type]


def test_participant_defaults_to_neutral_stance() -> None:
    # FR-7: neutral is the default stance.
    assert _participant().stance is Stance.NEUTRAL


def test_participant_accepts_explicit_stance() -> None:
    assert _participant(stance=Stance.PRO).stance is Stance.PRO


def test_participant_requires_nonempty_name_and_model() -> None:
    with pytest.raises(ValidationError):
        _participant(display_name="")
    with pytest.raises(ValidationError):
        _participant(model="")


def test_participant_name_is_stripped() -> None:
    assert _participant(display_name="  Athena  ").display_name == "Athena"


def test_turn_budget_leaves_room_for_a_reasoning_model() -> None:
    # Measured: a debate answer costs 150-500 tokens, but a reasoning model
    # spends its budget on hidden thinking first (qwen3: 510-1019, worst
    # observed total 1460). Anything under ~1500 truncates it mid-thought.
    assert ParticipantTuning().max_tokens >= 1500


def test_api_and_domain_tuning_defaults_agree() -> None:
    # Two copies of this number drift, and the drift is invisible until a turn
    # truncates in production.
    from cicero.api.schemas import TuningIn

    assert TuningIn().max_tokens == ParticipantTuning().max_tokens
    assert TuningIn().temperature == ParticipantTuning().temperature


def test_tuning_rejects_out_of_range_temperature() -> None:
    with pytest.raises(ValidationError):
        ParticipantTuning(temperature=2.5)
    with pytest.raises(ValidationError):
        ParticipantTuning(temperature=-0.1)


def test_tuning_rejects_nonpositive_max_tokens() -> None:
    with pytest.raises(ValidationError):
        ParticipantTuning(max_tokens=0)


def test_chamber_defaults() -> None:
    chamber = Chamber(topic="Should AI be regulated?")
    assert chamber.status is ChamberStatus.DRAFT
    assert chamber.participants == []
    assert chamber.turns == []
    assert chamber.consensus is None
    assert isinstance(chamber.created_at, datetime)
    assert chamber.created_at.tzinfo is not None  # timezone-aware


def test_chamber_requires_nonempty_topic() -> None:
    with pytest.raises(ValidationError):
        Chamber(topic="")


def test_chamber_participant_by_id_found_and_missing() -> None:
    p = _participant()
    chamber = Chamber(topic="T", participants=[p])
    assert chamber.participant_by_id(p.id) is p
    assert chamber.participant_by_id(uuid4()) is None


def test_turn_round_index_must_be_nonnegative() -> None:
    with pytest.raises(ValidationError):
        Turn(participant_id=uuid4(), round_index=-1)


def test_extra_fields_are_forbidden() -> None:
    # extra="forbid" guards against typos silently creating fields.
    with pytest.raises(ValidationError):
        Chamber(topic="T", unexpected="x")  # type: ignore[call-arg]


def test_citation_url_required() -> None:
    with pytest.raises(ValidationError):
        Citation(url="")  # type: ignore[call-arg]
    assert Citation(url="https://example.org").title == ""
