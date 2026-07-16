"""Tests for the chamber lifecycle state machine."""

from __future__ import annotations

import pytest

from cicero.core import can_transition, transition
from cicero.core.state_machine import InvalidTransition
from cicero.domain import Chamber, ChamberStatus

S = ChamberStatus

VALID = [
    (S.DRAFT, S.RUNNING),
    (S.DRAFT, S.ARCHIVED),
    (S.RUNNING, S.PAUSED),
    (S.RUNNING, S.CONCLUDED),
    (S.PAUSED, S.RUNNING),
    (S.PAUSED, S.CONCLUDED),
    (S.PAUSED, S.ARCHIVED),
    (S.CONCLUDED, S.ARCHIVED),
]

INVALID = [
    (S.DRAFT, S.PAUSED),
    (S.DRAFT, S.CONCLUDED),
    (S.RUNNING, S.DRAFT),
    (S.RUNNING, S.ARCHIVED),
    (S.CONCLUDED, S.RUNNING),
    (S.ARCHIVED, S.RUNNING),
    (S.ARCHIVED, S.DRAFT),
    (S.DRAFT, S.DRAFT),
]


@pytest.mark.parametrize(("current", "target"), VALID)
def test_valid_transitions_allowed(current: ChamberStatus, target: ChamberStatus) -> None:
    assert can_transition(current, target) is True


@pytest.mark.parametrize(("current", "target"), INVALID)
def test_invalid_transitions_rejected(current: ChamberStatus, target: ChamberStatus) -> None:
    assert can_transition(current, target) is False


def test_transition_updates_status() -> None:
    chamber = Chamber(topic="T")
    returned = transition(chamber, S.RUNNING)
    assert chamber.status is S.RUNNING
    assert returned is chamber


def test_transition_raises_on_invalid() -> None:
    chamber = Chamber(topic="T")  # DRAFT
    with pytest.raises(InvalidTransition, match=r"^cannot move chamber from draft to concluded$"):
        transition(chamber, S.CONCLUDED)
    # status is unchanged after a rejected transition
    assert chamber.status is S.DRAFT


def test_archived_is_terminal() -> None:
    for target in ChamberStatus:
        assert can_transition(S.ARCHIVED, target) is False
