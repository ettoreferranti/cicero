import dataclasses

import pytest

from cicero.core.compliance import (
    ARGUED_KEY,
    COMPLIANCE_MAX_TOKENS,
    UNOPPOSED_CAVEAT,
    ComplianceJudge,
    ComplianceRecord,
    argued_stance,
    compliance_caveat,
    debater_compliance,
    noncompliance_lines,
)
from cicero.domain.enums import ConsensusOutcome, ProviderType, Stance
from cicero.domain.models import Chamber, ConsensusResult, Participant, Turn
from cicero.providers.base import GenerateOptions, GenerateResult, Message
from cicero.providers.mock import MockProvider


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


def test_compliance_still_counts_a_turn_after_another_debaters_turn() -> None:
    """A mismatched participant must be skipped, not treated as a reason to stop
    scanning: the loop has to look past it to the target debater's own turns
    later in the transcript."""
    bob = _debater(Stance.CON, "Bob")
    ada = _debater(Stance.PRO, "Ada")
    chamber = Chamber(topic="a motion", participants=[ada, bob])
    chamber.turns = [_turn(ada, "pro", 0), _turn(bob, "con", 1)]
    assert debater_compliance(chamber, bob) == ComplianceRecord(
        assigned=Stance.CON, judged=1, held=1, argued_against=0
    )


def test_compliance_still_counts_a_turn_after_an_earlier_unjudged_one() -> None:
    """An unjudged turn must be skipped, not treated as a reason to stop scanning:
    a judge failure early in the transcript must not silently drop every turn
    that comes after it — that is the exact bug class this guards against."""
    bob = _debater(Stance.CON)
    chamber = Chamber(topic="a motion", participants=[bob])
    chamber.turns = [_turn(bob, None, 0), _turn(bob, "con", 1)]
    assert debater_compliance(chamber, bob) == ComplianceRecord(
        assigned=Stance.CON, judged=1, held=1, argued_against=0
    )


def test_compliance_accumulates_held_across_multiple_turns() -> None:
    """Holding the assigned side is a count, not a flag: two turns that both hold
    must add up to two, not saturate at one."""
    bob = _debater(Stance.CON)
    chamber = Chamber(topic="a motion", participants=[bob])
    chamber.turns = [_turn(bob, "con", 0), _turn(bob, "con", 1)]
    assert debater_compliance(chamber, bob) == ComplianceRecord(
        assigned=Stance.CON, judged=2, held=2, argued_against=0
    )


def test_argued_key_is_the_literal_string_argued() -> None:
    """Pinned, not just consistent with itself: a later task exports this key in
    a JSON payload, so the stored wire format must not be free to drift."""
    assert ARGUED_KEY == "argued"


def test_compliance_record_is_immutable() -> None:
    """A record like this gets handed around and rendered from; nothing should
    be able to rewrite a debater's counted history after the fact."""
    record = ComplianceRecord(assigned=Stance.CON, judged=1, held=1, argued_against=0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.held = 2  # type: ignore[misc]


def _concluded(
    participants: list[Participant],
    turns: list[Turn],
    winner: Stance | None,
    outcome: ConsensusOutcome = ConsensusOutcome.CONSENSUS,
) -> Chamber:
    chamber = Chamber(topic="a motion", participants=participants)
    chamber.turns = turns
    chamber.consensus = ConsensusResult(
        outcome=outcome,
        statement="the chamber said something",
        winning_stance=winner,
        final_stances={str(p.id): p.stance for p in participants},
    )
    return chamber


def test_unopposed_caveat_wording_is_pinned() -> None:
    """This is user-facing copy, not an internal sentinel — compared here against
    a literal rather than the imported symbol so a change to the wording is a
    deliberate edit to this test, not something that can drift silently."""
    assert UNOPPOSED_CAVEAT == (
        "No debater was judged to argue against the winning position, though one "
        "was assigned to. Nothing in the judged turns contests it — read the "
        "transcript before treating this as a tested result."
    )


def test_caveat_fires_when_the_losing_side_was_never_argued() -> None:
    ada, bob = _debater(Stance.PRO, "Ada"), _debater(Stance.CON, "Bob")
    chamber = _concluded(
        [ada, bob],
        [_turn(ada, "pro", 0), _turn(bob, "pro", 0)],
        winner=Stance.PRO,
    )
    assert compliance_caveat(chamber) == UNOPPOSED_CAVEAT


def test_no_caveat_when_the_losing_side_was_argued() -> None:
    ada, bob = _debater(Stance.PRO, "Ada"), _debater(Stance.CON, "Bob")
    chamber = _concluded(
        [ada, bob],
        [_turn(ada, "pro", 0), _turn(bob, "con", 0)],
        winner=Stance.PRO,
    )
    assert compliance_caveat(chamber) == ""


def test_no_caveat_when_the_opposing_debater_was_never_judged() -> None:
    """Condition 3, and the one a loose implementation gets wrong.

    Bob is the only con-assigned debater and none of his turns were judged, while
    every pro turn was. An implementation that asks only "was anything judged?"
    fires the caveat here — publishing a finding the debate never measured. The
    other side was not absent; it was unmeasured."""
    ada, bob = _debater(Stance.PRO, "Ada"), _debater(Stance.CON, "Bob")
    chamber = _concluded(
        [ada, bob],
        [_turn(ada, "pro", 0), _turn(bob, None, 0)],
        winner=Stance.PRO,
    )
    assert compliance_caveat(chamber) == ""


def test_no_caveat_when_nothing_at_all_was_judged() -> None:
    ada, bob = _debater(Stance.PRO, "Ada"), _debater(Stance.CON, "Bob")
    chamber = _concluded(
        [ada, bob],
        [_turn(ada, None, 0), _turn(bob, None, 0)],
        winner=Stance.PRO,
    )
    assert compliance_caveat(chamber) == ""


def test_no_caveat_when_nobody_was_assigned_the_opposing_side() -> None:
    """Two pro debaters agreeing is not a suppressed opposition — there was none."""
    ada, eve = _debater(Stance.PRO, "Ada"), _debater(Stance.PRO, "Eve")
    chamber = _concluded(
        [ada, eve],
        [_turn(ada, "pro", 0), _turn(eve, "pro", 0)],
        winner=Stance.PRO,
    )
    assert compliance_caveat(chamber) == ""


def test_no_caveat_when_the_only_other_participant_is_neutral() -> None:
    """The opposing-debater lookup must select ``stance is opposite``, not
    ``stance is not winning_stance`` — the latter would treat a NEUTRAL
    co-debater as someone assigned to oppose the winner, when nobody assigned
    them anything. Nobody was assigned CON here, so this must read exactly
    like the all-PRO case above."""
    ada, eve = _debater(Stance.PRO, "Ada"), _debater(Stance.NEUTRAL, "Eve")
    chamber = _concluded(
        [ada, eve],
        [_turn(ada, "pro", 0), _turn(eve, "pro", 0)],
        winner=Stance.PRO,
    )
    assert compliance_caveat(chamber) == ""


def test_caveat_fires_when_only_one_of_several_opposing_debaters_was_judged() -> None:
    """Condition 3 asks whether *any* assigned-opposite debater was measured,
    not whether *all* of them were. One measured CON debater who did not argue
    con is enough to know the chamber had the chance to hear that side — a
    co-debater's turns all failing to judge does not erase that."""
    ada = _debater(Stance.PRO, "Ada")
    bob = _debater(Stance.CON, "Bob")
    carl = _debater(Stance.CON, "Carl")
    chamber = _concluded(
        [ada, bob, carl],
        [_turn(ada, "pro", 0), _turn(bob, "pro", 0), _turn(carl, None, 0)],
        winner=Stance.PRO,
    )
    assert compliance_caveat(chamber) == UNOPPOSED_CAVEAT


def test_caveat_is_unaffected_by_muting_the_opposing_debater() -> None:
    """Muting is mutable roster state applied after the fact; the module
    docstring promises nothing here reads ``Participant.muted``. Muting the
    only CON debater must not change this finding — a version that filtered
    the opposing-debater lookup or ``_judged_sides`` by ``not muted`` would
    let a mid-debate mute retroactively rewrite what got measured."""
    ada, bob = _debater(Stance.PRO, "Ada"), _debater(Stance.CON, "Bob")
    bob.muted = True
    chamber = _concluded(
        [ada, bob],
        [_turn(ada, "pro", 0), _turn(bob, "pro", 0)],
        winner=Stance.PRO,
    )
    assert compliance_caveat(chamber) == UNOPPOSED_CAVEAT


def test_no_caveat_for_a_neutral_winner() -> None:
    """NEUTRAL has no polar opposite, so 'the other side' names nothing."""
    ada, bob = _debater(Stance.PRO, "Ada"), _debater(Stance.CON, "Bob")
    chamber = _concluded(
        [ada, bob],
        [_turn(ada, "neutral", 0), _turn(bob, "neutral", 0)],
        winner=Stance.NEUTRAL,
    )
    assert compliance_caveat(chamber) == ""


def test_no_caveat_without_a_winner() -> None:
    ada, bob = _debater(Stance.PRO, "Ada"), _debater(Stance.CON, "Bob")
    chamber = _concluded(
        [ada, bob],
        [_turn(ada, "pro", 0), _turn(bob, "pro", 0)],
        winner=None,
        outcome=ConsensusOutcome.DISAGREEMENT,
    )
    assert compliance_caveat(chamber) == ""


def test_no_caveat_before_the_debate_concludes() -> None:
    ada, bob = _debater(Stance.PRO, "Ada"), _debater(Stance.CON, "Bob")
    chamber = Chamber(topic="a motion", participants=[ada, bob])
    chamber.turns = [_turn(ada, "pro", 0), _turn(bob, "pro", 0)]
    assert compliance_caveat(chamber) == ""


def test_the_opposite_side_counts_from_any_debater() -> None:
    """The claim is about the chamber, not about one debater: if anyone argued
    con, the chamber heard con, whoever was assigned it."""
    ada, bob = _debater(Stance.PRO, "Ada"), _debater(Stance.CON, "Bob")
    chamber = _concluded(
        [ada, bob],
        [_turn(ada, "con", 0), _turn(bob, "pro", 0)],
        winner=Stance.PRO,
    )
    assert compliance_caveat(chamber) == ""


def test_noncompliance_names_a_debater_that_abandoned_its_side() -> None:
    bob = _debater(Stance.CON, "Bob")
    chamber = _concluded(
        [bob], [_turn(bob, "pro", 0), _turn(bob, "pro", 1), _turn(bob, "pro", 2)],
        winner=Stance.PRO,
    )
    assert noncompliance_lines(chamber) == (
        "Bob (assigned con) argued pro in 3 of 3 judged turns",
    )


def test_noncompliance_is_silent_for_a_debater_that_held_its_side() -> None:
    bob = _debater(Stance.CON, "Bob")
    chamber = _concluded([bob], [_turn(bob, "con", 0)], winner=Stance.CON)
    assert noncompliance_lines(chamber) == ()


def test_noncompliance_reports_a_partial_count() -> None:
    """Held it, then conceded. That is the truth-seeking clause working, and the
    count says so rather than flattening it to non-compliance."""
    bob = _debater(Stance.CON, "Bob")
    chamber = _concluded(
        [bob], [_turn(bob, "con", 0), _turn(bob, "pro", 1)], winner=Stance.PRO
    )
    assert noncompliance_lines(chamber) == (
        "Bob (assigned con) argued pro in 1 of 2 judged turns",
    )


def test_noncompliance_never_reports_a_neutral_assigned_debater() -> None:
    """STANCE_INSTRUCTION[NEUTRAL] tells it to follow the evidence, so a neutral
    debater arguing pro is the instruction being obeyed, not broken."""
    eve = _debater(Stance.NEUTRAL, "Eve")
    chamber = _concluded([eve], [_turn(eve, "pro", 0)], winner=Stance.PRO)
    assert noncompliance_lines(chamber) == ()


def test_noncompliance_continues_past_a_neutral_participant() -> None:
    """A neutral participant has no side to skip past — it must not be treated
    as a reason to stop scanning the roster before reaching a debater that
    actually abandoned its assigned side."""
    eve = _debater(Stance.NEUTRAL, "Eve")
    bob = _debater(Stance.CON, "Bob")
    chamber = _concluded(
        [eve, bob], [_turn(eve, "pro", 0), _turn(bob, "pro", 0)], winner=Stance.PRO
    )
    assert noncompliance_lines(chamber) == (
        "Bob (assigned con) argued pro in 1 of 1 judged turns",
    )


def test_noncompliance_continues_past_a_compliant_participant() -> None:
    """A debater with nothing to report must not be treated as a reason to stop
    scanning the roster before reaching one that does have something to
    report."""
    ada = _debater(Stance.PRO, "Ada")
    bob = _debater(Stance.CON, "Bob")
    chamber = _concluded(
        [ada, bob], [_turn(ada, "pro", 0), _turn(bob, "pro", 0)], winner=Stance.PRO
    )
    assert noncompliance_lines(chamber) == (
        "Bob (assigned con) argued pro in 1 of 1 judged turns",
    )


async def test_judge_reads_a_one_word_answer() -> None:
    judge = ComplianceJudge(MockProvider(scripted=["con"]), "mock-small")
    assert await judge.judge("a motion", "an argument") is Stance.CON


async def test_judge_returns_none_for_an_unreadable_answer() -> None:
    judge = ComplianceJudge(MockProvider(scripted=["it depends, really"]), "mock-small")
    assert await judge.judge("a motion", "an argument") is None


async def test_judge_returns_none_when_the_provider_fails() -> None:
    """A failing judge must not crash a debate (NFR-R-1)."""
    judge = ComplianceJudge(MockProvider(fail_after=1), "mock-small")
    assert await judge.judge("a motion", "an argument") is None


async def test_judge_suppresses_reasoning_and_pins_temperature() -> None:
    """A thinking model can spend its whole budget reasoning and return empty
    content — measured on qwen3 with the one-word stance poll."""
    captured: list[GenerateOptions] = []

    class Recording(MockProvider):
        async def generate(
            self, messages: list[Message], options: GenerateOptions
        ) -> GenerateResult:
            captured.append(options)
            return await super().generate(messages, options)

    judge = ComplianceJudge(Recording(scripted=["pro"]), "mock-small")
    await judge.judge("a motion", "an argument")
    assert captured[0].allow_reasoning is False
    assert captured[0].temperature == 0.0
    assert captured[0].max_tokens == COMPLIANCE_MAX_TOKENS
    assert captured[0].model == "mock-small"


async def test_judge_is_not_told_who_wrote_the_turn() -> None:
    """The load-bearing constraint: nothing identifying the debater or its
    assigned stance may reach the judge."""
    captured: list[list[Message]] = []

    class Recording(MockProvider):
        async def generate(
            self, messages: list[Message], options: GenerateOptions
        ) -> GenerateResult:
            captured.append(messages)
            return await super().generate(messages, options)

    judge = ComplianceJudge(Recording(scripted=["pro"]), "mock-small")
    await judge.judge("a motion", "Bartholomew thinks so too")
    prompt = " ".join(m.content for m in captured[0])
    assert "assigned" not in prompt.lower()
    # The only occurrence of a name is the one inside the judged text itself.
    assert prompt.count("Bartholomew") == 1


# --- The raw-reply seam (F10 follow-up) -------------------------------------
# ``judge`` collapses a provider error, an unreadable reply and a genuine
# "neutral" into values a measurement run cannot tell apart. The F10 quote-first
# run failed on 32 of 32 turns and the record could not say why, because the
# harness only ever saw ``None``. ``read`` keeps what the judge actually said.


async def test_read_returns_the_stance_and_the_raw_reply() -> None:
    judge = ComplianceJudge(MockProvider(scripted=["con"]), "mock-small")
    reading = await judge.read("a motion", "an argument")
    assert reading.stance is Stance.CON
    assert reading.reply == "con"
    assert reading.error is None


async def test_read_keeps_an_unreadable_reply_verbatim() -> None:
    """The whole point: an unparseable answer is recoverable for inspection
    rather than being flattened into the same None as a provider failure."""
    judge = ComplianceJudge(MockProvider(scripted=["it depends, really"]), "mock-small")
    reading = await judge.read("a motion", "an argument")
    assert reading.stance is None
    assert reading.reply == "it depends, really"
    assert reading.error is None


async def test_read_reports_a_provider_failure_as_an_error_not_a_reply() -> None:
    judge = ComplianceJudge(MockProvider(fail_after=1), "mock-small")
    reading = await judge.read("a motion", "an argument")
    assert reading.stance is None
    assert reading.reply is None
    assert reading.error is not None


async def test_read_distinguishes_the_two_failures_judge_cannot() -> None:
    """Both of these are ``None`` from ``judge``; the distinction is the feature."""
    unreadable = await ComplianceJudge(
        MockProvider(scripted=["hmm"]), "mock-small"
    ).read("a motion", "an argument")
    broken = await ComplianceJudge(MockProvider(fail_after=1), "mock-small").read(
        "a motion", "an argument"
    )
    assert unreadable.stance is broken.stance is None
    assert (unreadable.reply is None) != (broken.reply is None)


async def test_judge_delegates_to_read() -> None:
    """``judge`` keeps its signature and its contract; no caller changes."""
    for scripted, expected in (["pro"], Stance.PRO), (["nonsense"], None):
        judge = ComplianceJudge(MockProvider(scripted=scripted), "mock-small")
        reading = await judge.read("a motion", "an argument")
        again = ComplianceJudge(MockProvider(scripted=scripted), "mock-small")
        assert await again.judge("a motion", "an argument") is reading.stance is expected
