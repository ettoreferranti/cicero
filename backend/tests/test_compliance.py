import pytest

from cicero.core.compliance import (
    ARGUED_KEY,
    ComplianceRecord,
    argued_stance,
    debater_compliance,
)
from cicero.domain.enums import ProviderType, Stance
from cicero.domain.models import Chamber, Participant, Turn


def _debater(stance: Stance, name: str = "Bob") -> Participant:
    return Participant(
        display_name=name,
        provider=ProviderType.MOCK,
        model="mock-small",
        stance=stance,
    )


def _turn(participant: Participant, argued: str | None, round_index: int = 0) -> Turn:
    metadata: dict[str, object] = {} if argued is None else {ARGUED_KEY: argued}
    return Turn(
        participant_id=participant.id,
        round_index=round_index,
        content="an argument",
        metadata=metadata,
    )


def test_argued_stance_reads_a_recorded_judgement() -> None:
    bob = _debater(Stance.CON)
    assert argued_stance(_turn(bob, "pro")) is Stance.PRO


def test_argued_stance_is_none_when_the_key_is_absent() -> None:
    bob = _debater(Stance.CON)
    assert argued_stance(_turn(bob, None)) is None


@pytest.mark.parametrize("value", ["", "sideways", "PRO ", 3, None, True])
def test_argued_stance_is_none_for_a_value_that_is_not_a_stance(value: object) -> None:
    """Metadata is dict[str, object] and survives a round trip through JSON, so
    anything at all can be sitting under the key. None of it may be read as a
    stance, and none of it may raise."""
    bob = _debater(Stance.CON)
    turn = _turn(bob, None)
    turn.metadata[ARGUED_KEY] = value
    assert argued_stance(turn) is None


def test_compliance_counts_turns_that_held_and_abandoned_the_side() -> None:
    bob = _debater(Stance.CON)
    chamber = Chamber(topic="a motion", participants=[bob])
    chamber.turns = [
        _turn(bob, "con", 0),
        _turn(bob, "pro", 1),
        _turn(bob, "pro", 2),
    ]
    assert debater_compliance(chamber, bob) == ComplianceRecord(
        assigned=Stance.CON, judged=3, held=1, argued_against=2
    )


def test_compliance_ignores_unjudged_turns_entirely() -> None:
    """An unjudged turn is not evidence of anything — it must not count as held,
    and it must not count as argued_against."""
    bob = _debater(Stance.CON)
    chamber = Chamber(topic="a motion", participants=[bob])
    chamber.turns = [_turn(bob, "con", 0), _turn(bob, None, 1)]
    assert debater_compliance(chamber, bob) == ComplianceRecord(
        assigned=Stance.CON, judged=1, held=1, argued_against=0
    )


def test_a_neutral_turn_is_neither_held_nor_against_for_a_con_debater() -> None:
    """neutral is not the polar opposite of con, and it is not con either."""
    bob = _debater(Stance.CON)
    chamber = Chamber(topic="a motion", participants=[bob])
    chamber.turns = [_turn(bob, "neutral", 0)]
    assert debater_compliance(chamber, bob) == ComplianceRecord(
        assigned=Stance.CON, judged=1, held=0, argued_against=0
    )


def test_compliance_counts_only_the_named_debaters_turns() -> None:
    bob = _debater(Stance.CON, "Bob")
    ada = _debater(Stance.PRO, "Ada")
    chamber = Chamber(topic="a motion", participants=[bob, ada])
    chamber.turns = [_turn(bob, "con", 0), _turn(ada, "pro", 0)]
    assert debater_compliance(chamber, bob).judged == 1


def test_a_system_turn_belongs_to_no_debater() -> None:
    """Moderator notes and injected evidence have participant_id None; they must
    not be attributed to anyone."""
    bob = _debater(Stance.CON)
    chamber = Chamber(topic="a motion", participants=[bob])
    system_turn = Turn(round_index=0, content="a note", metadata={ARGUED_KEY: "pro"})
    chamber.turns = [system_turn]
    assert debater_compliance(chamber, bob).judged == 0
