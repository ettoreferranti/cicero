"""Unit tests for the muting rules (D6 / FR-13)."""

from __future__ import annotations

from cicero.core.roster import active_participants, active_stances, deciding_stances
from cicero.domain.enums import Stance
from cicero.domain.models import StancePoll
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


def test_a_never_measured_stance_does_not_vote() -> None:
    # A debater whose reply was never readable carries their *assigned* role,
    # not a measured position. Counting it would let the setup decide the
    # outcome: here B's phantom CON would tie a debate A has actually won.
    chamber, a, b, _c = _chamber()
    chamber.participants.remove(_c)
    poll = {str(a.id): Stance.PRO, str(b.id): Stance.CON}

    assert deciding_stances(chamber, poll, unparsed=[str(b.id)]) == {str(a.id): Stance.PRO}
    # Without the flag it is treated as a real measurement, as before.
    assert deciding_stances(chamber, poll) == poll


def test_a_stale_but_once_measured_stance_still_votes() -> None:
    # Unreadable *this* round, but read successfully earlier: the carried value
    # is genuine evidence, merely stale, so it keeps its vote.
    chamber, a, b, _c = _chamber()
    chamber.participants.remove(_c)
    chamber.stance_history.append(
        StancePoll(round_index=0, stances={str(b.id): Stance.CON}, unparsed=[])
    )
    poll = {str(a.id): Stance.PRO, str(b.id): Stance.CON}
    assert deciding_stances(chamber, poll, unparsed=[str(b.id)]) == poll


def test_an_earlier_failure_does_not_count_as_a_measurement() -> None:
    chamber, a, b, _c = _chamber()
    chamber.participants.remove(_c)
    # Present in the history, but flagged unparsed there too.
    chamber.stance_history.append(
        StancePoll(round_index=0, stances={str(b.id): Stance.CON}, unparsed=[str(b.id)])
    )
    poll = {str(a.id): Stance.PRO, str(b.id): Stance.CON}
    assert deciding_stances(chamber, poll, unparsed=[str(b.id)]) == {str(a.id): Stance.PRO}


def test_deciding_stances_never_empties_the_tally() -> None:
    # If nothing was ever measured, resolve on what we have rather than on an
    # empty tally that would read as "no agreement" for the wrong reason.
    chamber, a, b, _c = _chamber()
    chamber.participants.remove(_c)
    poll = {str(a.id): Stance.PRO, str(b.id): Stance.CON}
    everyone = [str(a.id), str(b.id)]
    assert deciding_stances(chamber, poll, unparsed=everyone) == poll


def test_deciding_stances_applies_muting_too() -> None:
    chamber, a, b, c = _chamber()
    b.muted = True
    poll = {str(a.id): Stance.PRO, str(b.id): Stance.CON, str(c.id): Stance.CON}
    assert deciding_stances(chamber, poll, unparsed=[str(c.id)]) == {str(a.id): Stance.PRO}


def test_stances_for_unknown_ids_are_ignored() -> None:
    # A stance recorded for someone no longer on the roster carries no vote.
    chamber, a, _b, _c = _chamber()
    poll = {str(a.id): Stance.PRO, "ghost": Stance.CON}
    assert active_stances(chamber, poll) == {str(a.id): Stance.PRO}
