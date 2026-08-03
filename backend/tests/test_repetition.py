"""Unit tests for repetition detection (FR-16)."""

from __future__ import annotations

from cicero.core.repetition import (
    REPEATED,
    is_repeat,
    normalise,
    previous_turn_content,
    round_all_repeated,
    similarity,
)
from cicero.domain.enums import Stance
from cicero.domain.models import Turn
from tests.conftest import make_chamber, make_participant

A_POINT = "The economic evidence cuts decisively the other way, and here is why."


def test_normalise_ignores_case_and_whitespace() -> None:
    assert normalise("  Hello   World \n") == "hello world"
    assert normalise("") == ""


def test_similarity_bounds() -> None:
    assert similarity(A_POINT, A_POINT) == 1.0
    assert similarity("", "") == 1.0  # two silences are alike, if uninformative
    assert similarity(A_POINT, "") == 0.0
    assert similarity("", A_POINT) == 0.0
    # Formatting differences are not content differences.
    assert similarity(A_POINT, f"  {A_POINT.upper()}  ") == 1.0


def test_similarity_separates_a_restatement_from_a_new_point() -> None:
    reworded = "The economic evidence cuts decisively the other way, and here is how."
    unrelated = "A completely different consideration applies to the security question."
    assert similarity(A_POINT, reworded) > 0.95
    assert similarity(A_POINT, unrelated) < 0.7


def test_is_repeat_respects_the_threshold() -> None:
    reworded = "The economic evidence cuts decisively the other way, and here is how."
    assert is_repeat(reworded, A_POINT, threshold=0.9)
    # 1.0 demands byte-identity (after normalising), so a reword is not a repeat.
    assert not is_repeat(reworded, A_POINT, threshold=1.0)
    assert is_repeat(A_POINT, A_POINT, threshold=1.0)


def test_is_repeat_needs_something_to_compare_against() -> None:
    assert not is_repeat(A_POINT, None, threshold=0.95)


def test_an_empty_turn_is_never_a_repeat() -> None:
    # An empty turn is a provider failure, already recorded as an error. Reading
    # it as "nothing new to say" would end debates on an outage.
    assert not is_repeat("", "", threshold=0.95)
    assert not is_repeat("   ", A_POINT, threshold=0.5)


def test_previous_turn_content_finds_the_last_substantive_turn() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    assert previous_turn_content(chamber, a.id) is None

    chamber.turns.append(Turn(participant_id=a.id, round_index=0, content="first"))
    chamber.turns.append(Turn(participant_id=b.id, round_index=0, content="other speaker"))
    chamber.turns.append(Turn(participant_id=a.id, round_index=1, content="second"))
    # A failed turn is skipped, so a provider outage does not erase the history.
    chamber.turns.append(Turn(participant_id=a.id, round_index=2, content=""))

    assert previous_turn_content(chamber, a.id) == "second"
    assert previous_turn_content(chamber, b.id) == "other speaker"


def _round(chamber, round_index, repeated):  # type: ignore[no-untyped-def]
    for participant, flag in zip(chamber.participants, repeated, strict=True):
        chamber.turns.append(
            Turn(
                participant_id=participant.id,
                round_index=round_index,
                content="something",
                metadata={REPEATED: True} if flag else {},
            )
        )


def test_round_all_repeated_needs_everyone() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)

    _round(chamber, 0, [True, False])
    assert not round_all_repeated(chamber, 0)  # B is still making progress

    _round(chamber, 1, [True, True])
    assert round_all_repeated(chamber, 1)


def test_round_all_repeated_ignores_muted_debaters() -> None:
    # A muted debater takes no turns, so it can never "repeat" — requiring one
    # from it would keep a finished debate alive forever.
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    b.muted = True
    chamber.turns.append(
        Turn(participant_id=a.id, round_index=0, content="x", metadata={REPEATED: True})
    )
    assert round_all_repeated(chamber, 0)


def test_a_round_nobody_finished_is_not_repetition() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    chamber.turns.append(
        Turn(participant_id=a.id, round_index=0, content="x", metadata={REPEATED: True})
    )
    # B has not spoken yet — a partial round decides nothing.
    assert not round_all_repeated(chamber, 0)
    assert not round_all_repeated(chamber, 99)


def test_an_empty_roster_never_reads_as_repetition() -> None:
    assert not round_all_repeated(make_chamber(), 0)
