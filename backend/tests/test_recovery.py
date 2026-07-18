"""Tests for restart recovery of interrupted debates (J3)."""

from __future__ import annotations

from cicero.core.recovery import recover_interrupted_debates
from cicero.core.state_machine import transition
from cicero.domain.enums import ChamberStatus, Stance
from cicero.persistence.memory import InMemoryChamberRepository
from tests.conftest import make_chamber, make_participant


def _chamber_in(repo: InMemoryChamberRepository, *statuses: ChamberStatus):  # type: ignore[no-untyped-def]
    chamber = make_chamber(make_participant("A", Stance.PRO), make_participant("B", Stance.CON))
    for status in statuses:
        transition(chamber, status)
    repo.add(chamber)
    return chamber


def test_running_chambers_are_parked_as_paused() -> None:
    repo = InMemoryChamberRepository()
    interrupted = _chamber_in(repo, ChamberStatus.RUNNING)
    draft = _chamber_in(repo)
    concluded = _chamber_in(repo, ChamberStatus.RUNNING, ChamberStatus.CONCLUDED)

    recovered = recover_interrupted_debates(repo)

    assert [c.id for c in recovered] == [interrupted.id]
    stored = repo.get(interrupted.id)
    assert stored is not None and stored.status is ChamberStatus.PAUSED
    assert repo.get(draft.id).status is ChamberStatus.DRAFT  # type: ignore[union-attr]
    assert repo.get(concluded.id).status is ChamberStatus.CONCLUDED  # type: ignore[union-attr]


def test_recovery_is_idempotent() -> None:
    repo = InMemoryChamberRepository()
    _chamber_in(repo, ChamberStatus.RUNNING)
    assert len(recover_interrupted_debates(repo)) == 1
    assert recover_interrupted_debates(repo) == []
