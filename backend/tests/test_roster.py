"""Unit tests for the muting rules (D6 / FR-13)."""

from __future__ import annotations

from cicero.core.roster import active_participants, active_stances
from cicero.domain.enums import Stance
from tests.conftest import make_chamber, make_participant


def _chamber():  # type: ignore[no-untyped-def]
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    c = make_participant("C", Stance.CON)
    return make_chamber(a, b, c), a, b, c


def test_active_participants_excludes_muted_and_keeps_order() -> None:
    chamber, a, b, c = _chamber()
    assert active_participants(chamber) == [a, b, c]
    b.muted = True
    assert active_participants(chamber) == [a, c]
    a.muted = c.muted = True
    assert active_participants(chamber) == []


def test_active_stances_drops_the_muted_vote() -> None:
    chamber, a, b, c = _chamber()
    poll = {str(a.id): Stance.PRO, str(b.id): Stance.CON, str(c.id): Stance.CON}

    assert active_stances(chamber, poll) == poll

    # Muting B flips a 1-2 split into a 1-1 tie...
    b.muted = True
    assert active_stances(chamber, poll) == {str(a.id): Stance.PRO, str(c.id): Stance.CON}

    # ...and muting C as well leaves a unanimous single voter.
    c.muted = True
    assert active_stances(chamber, poll) == {str(a.id): Stance.PRO}


def test_a_fully_muted_chamber_falls_back_to_every_stance() -> None:
    # Otherwise the tally would be empty and read as "no agreement" for a reason
    # that has nothing to do with what the debaters actually said.
    chamber, a, b, c = _chamber()
    poll = {str(a.id): Stance.PRO, str(b.id): Stance.PRO, str(c.id): Stance.PRO}
    for participant in chamber.participants:
        participant.muted = True
    assert active_stances(chamber, poll) == poll


def test_stances_for_unknown_ids_are_ignored() -> None:
    # A stance recorded for someone no longer on the roster carries no vote.
    chamber, a, _b, _c = _chamber()
    poll = {str(a.id): Stance.PRO, "ghost": Stance.CON}
    assert active_stances(chamber, poll) == {str(a.id): Stance.PRO}
