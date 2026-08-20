"""Tests for the derived outcome summary (F5)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from cicero.core.compliance import UNOPPOSED_CAVEAT
from cicero.core.outcome import (
    STANCE_CAVEAT,
    OutcomeSummary,
    decision_basis,
    movements,
    summarize_outcome,
    support_summary,
)
from cicero.domain.enums import ConsensusOutcome, DecisionRule, Stance
from cicero.domain.models import ConsensusResult, StancePoll, Turn
from tests.conftest import make_chamber, make_participant


def _concluded(
    outcome: ConsensusOutcome,
    stances: dict[str, Stance],
    winner: Stance | None = None,
    unparsed: list[str] | None = None,
    headline: str = "",
):  # type: ignore[no-untyped-def]
    """A chamber with three debaters and a recorded consensus."""
    ada = make_participant("Ada", Stance.PRO)
    zeno = make_participant("Zeno", Stance.CON)
    kant = make_participant("Kant", Stance.NEUTRAL)
    chamber = make_chamber(ada, zeno, kant)
    # strict=False: some tests deliberately pass fewer stance values than
    # participants (e.g. a lone unresolved debater), so zip must truncate.
    keyed = dict(
        zip([str(p.id) for p in chamber.participants], stances.values(), strict=False)
    )
    chamber.consensus = ConsensusResult(
        outcome=outcome,
        statement="Statement.",
        headline=headline,
        winning_stance=winner,
        final_stances=keyed,
        unparsed=unparsed,
    )
    return chamber


def test_no_consensus_yields_no_summary() -> None:
    chamber = make_chamber(make_participant("Ada", Stance.PRO))
    assert summarize_outcome(chamber) is None


def test_support_reports_unanimity() -> None:
    chamber = _concluded(
        ConsensusOutcome.CONSENSUS,
        {"a": Stance.PRO, "b": Stance.PRO, "c": Stance.PRO},
        winner=Stance.PRO,
        unparsed=[],
    )
    assert support_summary(chamber) == "unanimous: all 3 debaters on pro"


def test_support_reports_unanimity_without_a_recorded_winner() -> None:
    # Defensive fallback: is_consensus always implies a single winning stance,
    # so this should not arise in practice, but a chamber that somehow reached
    # CONSENSUS without one recorded must still get an honest sentence rather
    # than crashing or silently naming ``None`` as the stance.
    chamber = _concluded(
        ConsensusOutcome.CONSENSUS,
        {"a": Stance.PRO, "b": Stance.PRO, "c": Stance.PRO},
        winner=None,
        unparsed=[],
    )
    assert support_summary(chamber) == "unanimous: all 3 debaters"


def test_support_reports_a_contested_majority_naming_the_non_winning_stance() -> None:
    chamber = _concluded(
        ConsensusOutcome.MAJORITY,
        {"a": Stance.NEUTRAL, "b": Stance.NEUTRAL, "c": Stance.CON},
        winner=Stance.NEUTRAL,
        unparsed=[],
    )
    # Non-winners are all one stance (con) — must be named as "con", not
    # blanket "dissent": a debater who did not settle on the winning stance did
    # not necessarily oppose it.
    assert support_summary(chamber) == "contested: 2 of 3 debaters settled on neutral (1 con)"


def test_support_orders_a_mixed_non_winning_breakdown_by_enum_declaration() -> None:
    ada = make_participant("Ada", Stance.PRO)
    zeno = make_participant("Zeno", Stance.CON)
    kant = make_participant("Kant", Stance.NEUTRAL)
    otto = make_participant("Otto", Stance.PRO)
    chamber = make_chamber(ada, zeno, kant, otto)
    chamber.consensus = ConsensusResult(
        outcome=ConsensusOutcome.MAJORITY,
        statement="Statement.",
        headline="",
        winning_stance=Stance.PRO,
        final_stances={
            str(ada.id): Stance.PRO,
            str(zeno.id): Stance.CON,
            str(kant.id): Stance.NEUTRAL,
            str(otto.id): Stance.PRO,
        },
        unparsed=[],
    )
    # con is declared before neutral on the Stance enum, so it must appear
    # first regardless of insertion order or which has the higher count — this
    # is the case a count- or dict-ordered breakdown would get wrong.
    assert support_summary(chamber) == (
        "contested: 2 of 4 debaters settled on pro (1 con, 1 neutral)"
    )


def test_support_guards_a_majority_with_no_non_winning_stance() -> None:
    # decide_outcome never actually produces this (everyone agreeing yields
    # CONSENSUS, not MAJORITY), but support_summary must not emit a dangling
    # "()" if a MAJORITY outcome with no non-winning stance is ever reached.
    chamber = _concluded(
        ConsensusOutcome.MAJORITY,
        {"a": Stance.PRO, "b": Stance.PRO, "c": Stance.PRO},
        winner=Stance.PRO,
        unparsed=[],
    )
    assert support_summary(chamber) == "contested: 3 of 3 debaters settled on pro"


def test_support_reports_a_judge_verdict() -> None:
    chamber = _concluded(
        ConsensusOutcome.VERDICT,
        {"a": Stance.PRO, "b": Stance.CON, "c": Stance.NEUTRAL},
        winner=Stance.PRO,
        unparsed=[],
    )
    assert support_summary(chamber) == "judge-decided: pro ruled, no majority among 3 debaters"


def test_support_reports_a_judge_verdict_with_no_readable_winner() -> None:
    # Reproduces the self-contradiction the review flagged: a VERDICT whose
    # WINNER: line could not be read must not fall through to the shared
    # "unresolved" text — the judge *did* decide, only the winner was lost.
    chamber = _concluded(
        ConsensusOutcome.VERDICT,
        {"a": Stance.PRO, "b": Stance.CON, "c": Stance.NEUTRAL},
        winner=None,
        unparsed=[],
    )
    assert support_summary(chamber) == "judge-decided: the judge's ruling could not be read"


def test_support_reports_disagreement_as_unresolved() -> None:
    chamber = _concluded(
        ConsensusOutcome.DISAGREEMENT,
        {"a": Stance.PRO, "b": Stance.CON, "c": Stance.NEUTRAL},
        unparsed=[],
    )
    assert support_summary(chamber) == "unresolved: no position prevailed"


def test_support_excludes_a_muted_debater_from_the_denominator() -> None:
    chamber = _concluded(
        ConsensusOutcome.CONSENSUS,
        {"a": Stance.PRO, "b": Stance.PRO, "c": Stance.PRO},
        winner=Stance.PRO,
        unparsed=[],
    )
    chamber.participants[2].muted = True
    # A muted debater does not vote (FR-13), so it cannot pad the support count.
    assert support_summary(chamber) == "unanimous: all 2 debaters on pro"


def test_support_says_unmeasured_when_nothing_was_recorded() -> None:
    chamber = _concluded(ConsensusOutcome.DISAGREEMENT, {}, unparsed=[])
    chamber.consensus.final_stances = {}
    # Never "0 of 3", which reads as a measured result of zero support.
    assert support_summary(chamber) == (
        "unmeasured: no debater's final position could be read"
    )


def test_support_prefers_the_recorded_unparsed_set_over_a_stale_poll() -> None:
    # The final poll is not always written to stance_history (orchestrator.py:520
    # records a round once), so the last poll can name different failures than the
    # ones finalize() actually decided on. The recorded set wins.
    chamber = _concluded(
        ConsensusOutcome.CONSENSUS,
        {"a": Stance.PRO, "b": Stance.PRO, "c": Stance.PRO},
        winner=Stance.PRO,
        unparsed=[],
    )
    ada = chamber.participants[0]
    chamber.stance_history = [
        StancePoll(round_index=0, stances={}, unparsed=[str(ada.id)]),
    ]
    assert support_summary(chamber) == "unanimous: all 3 debaters on pro"


def test_support_falls_back_to_the_last_poll_for_a_historical_chamber() -> None:
    chamber = _concluded(
        ConsensusOutcome.CONSENSUS,
        {"a": Stance.PRO, "b": Stance.PRO, "c": Stance.PRO},
        winner=Stance.PRO,
        unparsed=None,
    )
    ada = chamber.participants[0]
    # Never measured anywhere, so deciding_stances drops it.
    chamber.stance_history = [StancePoll(round_index=0, stances={}, unparsed=[str(ada.id)])]
    assert support_summary(chamber) == "unanimous: all 2 debaters on pro"


def test_support_for_a_historical_chamber_without_history_counts_everyone() -> None:
    chamber = _concluded(
        ConsensusOutcome.CONSENSUS,
        {"a": Stance.PRO, "b": Stance.PRO, "c": Stance.PRO},
        winner=Stance.PRO,
        unparsed=None,
    )
    assert support_summary(chamber) == "unanimous: all 3 debaters on pro"


def test_decision_basis_per_outcome() -> None:
    stances = {"a": Stance.PRO, "b": Stance.PRO, "c": Stance.PRO}
    assert (
        decision_basis(_concluded(ConsensusOutcome.CONSENSUS, stances, winner=Stance.PRO))
        == "all debaters converged"
    )
    assert (
        decision_basis(_concluded(ConsensusOutcome.MAJORITY, stances, winner=Stance.PRO))
        == "majority of final positions"
    )
    assert (
        decision_basis(_concluded(ConsensusOutcome.VERDICT, stances, winner=Stance.PRO))
        == "moderator's verdict on argument strength"
    )


def test_decision_basis_names_the_rule_that_failed_to_resolve() -> None:
    for rule, expected in [
        (DecisionRule.UNANIMOUS, "unresolved under the unanimous rule"),
        (DecisionRule.MAJORITY, "unresolved under the majority rule"),
        (DecisionRule.JUDGE, "unresolved under the judge rule"),
    ]:
        chamber = _concluded(ConsensusOutcome.DISAGREEMENT, {"a": Stance.PRO})
        chamber.settings.decision_rule = rule
        assert decision_basis(chamber) == expected


def test_movements_reports_who_changed_between_first_and_last_measurement() -> None:
    chamber = _concluded(
        ConsensusOutcome.MAJORITY,
        {"a": Stance.NEUTRAL, "b": Stance.NEUTRAL, "c": Stance.NEUTRAL},
        winner=Stance.NEUTRAL,
        unparsed=[],
    )
    ada, zeno, kant = chamber.participants
    chamber.stance_history = [
        StancePoll(
            round_index=0,
            stances={str(ada.id): Stance.PRO, str(zeno.id): Stance.CON,
                     str(kant.id): Stance.NEUTRAL},
        ),
        StancePoll(
            round_index=1,
            stances={str(ada.id): Stance.NEUTRAL, str(zeno.id): Stance.CON,
                     str(kant.id): Stance.NEUTRAL},
        ),
    ]
    assert movements(chamber) == ("Ada (pro to neutral)",)


def test_movements_is_empty_without_history() -> None:
    chamber = _concluded(ConsensusOutcome.DISAGREEMENT, {"a": Stance.PRO})
    assert movements(chamber) == ()


def test_movements_needs_two_measurements() -> None:
    chamber = _concluded(ConsensusOutcome.DISAGREEMENT, {"a": Stance.PRO})
    ada = chamber.participants[0]
    chamber.stance_history = [StancePoll(round_index=0, stances={str(ada.id): Stance.PRO})]
    assert movements(chamber) == ()


def test_movements_ignores_polls_that_could_not_be_read() -> None:
    chamber = _concluded(ConsensusOutcome.DISAGREEMENT, {"a": Stance.PRO})
    ada = chamber.participants[0]
    # The second value was carried forward, not measured. Reporting it would
    # present a parse failure as someone changing their mind.
    chamber.stance_history = [
        StancePoll(round_index=0, stances={str(ada.id): Stance.PRO}),
        StancePoll(round_index=1, stances={str(ada.id): Stance.CON}, unparsed=[str(ada.id)]),
    ]
    assert movements(chamber) == ()


def test_movements_ignores_a_stance_that_moved_and_moved_back() -> None:
    chamber = _concluded(ConsensusOutcome.DISAGREEMENT, {"a": Stance.PRO})
    ada = chamber.participants[0]
    chamber.stance_history = [
        StancePoll(round_index=0, stances={str(ada.id): Stance.PRO}),
        StancePoll(round_index=1, stances={str(ada.id): Stance.CON}),
        StancePoll(round_index=2, stances={str(ada.id): Stance.PRO}),
    ]
    assert movements(chamber) == ()


def test_movements_ignores_polls_a_debater_was_absent_from() -> None:
    chamber = _concluded(ConsensusOutcome.DISAGREEMENT, {"a": Stance.PRO})
    ada, zeno = chamber.participants[0], chamber.participants[1]
    # Zeno joined late: an early poll has no entry for it at all, which is not a
    # measurement and must not become the start of a trajectory.
    chamber.stance_history = [
        StancePoll(round_index=0, stances={str(ada.id): Stance.PRO}),
        StancePoll(round_index=1, stances={str(ada.id): Stance.PRO,
                                           str(zeno.id): Stance.CON}),
    ]
    assert movements(chamber) == ()


def test_movements_flags_a_muted_debater() -> None:
    chamber = _concluded(ConsensusOutcome.DISAGREEMENT, {"a": Stance.PRO})
    ada = chamber.participants[0]
    ada.muted = True
    chamber.stance_history = [
        StancePoll(round_index=0, stances={str(ada.id): Stance.PRO}),
        StancePoll(round_index=1, stances={str(ada.id): Stance.NEUTRAL}),
    ]
    # Still polled (FR-13), so still reported — and where it ended up is
    # interesting precisely because it did not count.
    assert movements(chamber) == ("Ada (pro to neutral, muted)",)


def test_summarize_outcome_bundles_the_headline_with_the_derived_values() -> None:
    chamber = _concluded(
        ConsensusOutcome.CONSENSUS,
        {"a": Stance.PRO, "b": Stance.PRO, "c": Stance.PRO},
        winner=Stance.PRO,
        unparsed=[],
        headline="Mars should wait.",
    )
    summary = summarize_outcome(chamber)
    assert summary is not None
    assert summary.headline == "Mars should wait."
    assert summary.support == "unanimous: all 3 debaters on pro"
    assert summary.decided_by == "all debaters converged"
    assert summary.movements == ()


def test_support_summary_is_blank_without_a_consensus() -> None:
    chamber = make_chamber(make_participant("Ada", Stance.PRO))
    assert support_summary(chamber) == ""


def test_decision_basis_is_blank_without_a_consensus() -> None:
    chamber = make_chamber(make_participant("Ada", Stance.PRO))
    assert decision_basis(chamber) == ""


def test_movements_keeps_scanning_past_a_debater_who_never_moved() -> None:
    # Ada is first in chamber.participants and holds position throughout; if the
    # scan stopped at the first non-mover it would never reach Zeno's move.
    chamber = _concluded(ConsensusOutcome.DISAGREEMENT, {"a": Stance.PRO})
    ada, zeno = chamber.participants[0], chamber.participants[1]
    chamber.stance_history = [
        StancePoll(
            round_index=0,
            stances={str(ada.id): Stance.PRO, str(zeno.id): Stance.CON},
        ),
        StancePoll(
            round_index=1,
            stances={str(ada.id): Stance.PRO, str(zeno.id): Stance.NEUTRAL},
        ),
    ]
    assert movements(chamber) == ("Zeno (con to neutral)",)


def test_movements_reports_the_last_measurement_not_the_second() -> None:
    chamber = _concluded(ConsensusOutcome.DISAGREEMENT, {"a": Stance.PRO})
    ada = chamber.participants[0]
    chamber.stance_history = [
        StancePoll(round_index=0, stances={str(ada.id): Stance.PRO}),
        StancePoll(round_index=1, stances={str(ada.id): Stance.CON}),
        StancePoll(round_index=2, stances={str(ada.id): Stance.NEUTRAL}),
    ]
    assert movements(chamber) == ("Ada (pro to neutral)",)


def test_outcome_summary_is_frozen() -> None:
    summary = OutcomeSummary(
        headline="",
        support="",
        decided_by="",
        movements=(),
        caveat="",
        compliance_caveat="",
        noncompliance=(),
    )
    with pytest.raises(FrozenInstanceError):
        summary.headline = "changed"


def test_caveat_appears_when_a_deciding_stance_is_neutral() -> None:
    chamber = _concluded(
        ConsensusOutcome.MAJORITY,
        {"a": Stance.PRO, "b": Stance.PRO, "c": Stance.NEUTRAL},
        winner=Stance.PRO,
        unparsed=[],
    )
    summary = summarize_outcome(chamber)
    assert summary is not None
    assert summary.caveat == STANCE_CAVEAT


def test_caveat_appears_when_a_movement_ends_on_neutral() -> None:
    chamber = _concluded(
        ConsensusOutcome.MAJORITY,
        {"a": Stance.PRO, "b": Stance.PRO, "c": Stance.PRO},
        winner=Stance.PRO,
        unparsed=[],
    )
    ada = chamber.participants[0]
    chamber.stance_history = [
        StancePoll(round_index=0, stances={str(ada.id): Stance.CON}),
        StancePoll(round_index=1, stances={str(ada.id): Stance.NEUTRAL}),
    ]
    summary = summarize_outcome(chamber)
    assert summary is not None
    assert summary.caveat == STANCE_CAVEAT


def test_caveat_is_absent_when_no_neutral_is_involved() -> None:
    # The case that keeps the trigger honest: an always-on caveat would pass every
    # other test in this group.
    chamber = _concluded(
        ConsensusOutcome.MAJORITY,
        {"a": Stance.PRO, "b": Stance.PRO, "c": Stance.CON},
        winner=Stance.PRO,
        unparsed=[],
    )
    ada = chamber.participants[0]
    chamber.stance_history = [
        StancePoll(round_index=0, stances={str(ada.id): Stance.CON}),
        StancePoll(round_index=1, stances={str(ada.id): Stance.PRO}),
    ]
    summary = summarize_outcome(chamber)
    assert summary is not None
    assert summary.caveat == ""


def test_caveat_ignores_a_neutral_only_in_an_unparsed_poll() -> None:
    # A carried-forward value is not a measurement, so it must not trigger a
    # caveat about what the poll recorded.
    chamber = _concluded(
        ConsensusOutcome.MAJORITY,
        {"a": Stance.PRO, "b": Stance.PRO, "c": Stance.CON},
        winner=Stance.PRO,
        unparsed=[],
    )
    ada = chamber.participants[0]
    chamber.stance_history = [
        StancePoll(round_index=0, stances={str(ada.id): Stance.CON}),
        StancePoll(
            round_index=1,
            stances={str(ada.id): Stance.NEUTRAL},
            unparsed=[str(ada.id)],
        ),
    ]
    summary = summarize_outcome(chamber)
    assert summary is not None
    assert summary.caveat == ""


def test_caveat_needs_an_actual_movement_not_just_a_neutral_reading() -> None:
    chamber = _concluded(
        ConsensusOutcome.MAJORITY,
        {"a": Stance.PRO, "b": Stance.PRO, "c": Stance.CON},
        winner=Stance.PRO,
        unparsed=[],
    )
    ada = chamber.participants[0]
    # Neutral in the middle, but first and last measurements match, so `movements`
    # reports nothing and there is no label for the caveat to qualify.
    chamber.stance_history = [
        StancePoll(round_index=0, stances={str(ada.id): Stance.PRO}),
        StancePoll(round_index=1, stances={str(ada.id): Stance.NEUTRAL}),
        StancePoll(round_index=2, stances={str(ada.id): Stance.PRO}),
    ]
    summary = summarize_outcome(chamber)
    assert summary is not None
    assert summary.caveat == ""


def test_caveat_prose_is_pinned_verbatim() -> None:
    # Every other caveat test compares against STANCE_CAVEAT, which is a tautology:
    # mutating the constant mutates both sides. A substring check does not help
    # either — the wording is user-visible, so pin it exactly, here and nowhere else.
    chamber = _concluded(
        ConsensusOutcome.MAJORITY,
        {"a": Stance.PRO, "b": Stance.PRO, "c": Stance.NEUTRAL},
        winner=Stance.PRO,
        unparsed=[],
    )
    summary = summarize_outcome(chamber)
    assert summary is not None
    assert summary.caveat == (
        "Stance labels record how each debater answered a three-word poll, not what "
        "they argued. A neutral answer covers both holding no position and holding a "
        "compromise the poll has no word for; read the transcript for what a debater "
        "actually held."
    )


def test_caveat_does_not_vouch_for_the_moderator_statement() -> None:
    # An earlier wording ended "the statement below describes what actually changed".
    # A real debate then produced a statement claiming a debater had endorsed a
    # position his final turn explicitly rejected, so the caveat was directing readers
    # to a fabrication and asserting it was reliable. It points at the transcript now.
    chamber = _concluded(
        ConsensusOutcome.MAJORITY,
        {"a": Stance.PRO, "b": Stance.PRO, "c": Stance.NEUTRAL},
        winner=Stance.PRO,
        unparsed=[],
    )
    summary = summarize_outcome(chamber)
    assert summary is not None
    assert "transcript" in summary.caveat
    assert "statement" not in summary.caveat


def _no_neutral_majority():  # type: ignore[no-untyped-def]
    """A chamber whose deciding stances contain no neutral.

    The caveat's first check short-circuits on a neutral final stance, so the
    movement loop below it is only reachable when none of them is neutral.
    """
    return _concluded(
        ConsensusOutcome.MAJORITY,
        {"a": Stance.PRO, "b": Stance.PRO, "c": Stance.CON},
        winner=Stance.PRO,
        unparsed=[],
    )


def _history(chamber, trajectories):  # type: ignore[no-untyped-def]
    """Build stance_history from {participant index: [stance per round]}."""
    rounds = max(len(v) for v in trajectories.values())
    chamber.stance_history = [
        StancePoll(
            round_index=index,
            stances={
                str(chamber.participants[i].id): traj[index]
                for i, traj in trajectories.items()
                if index < len(traj)
            },
        )
        for index in range(rounds)
    ]
    return chamber


def test_caveat_compares_the_last_measurement_not_the_second() -> None:
    # pro, pro, neutral: first and SECOND are equal but first and LAST differ, so a
    # check reading measured[1] instead of measured[-1] would skip a real movement.
    chamber = _history(
        _no_neutral_majority(), {0: [Stance.PRO, Stance.PRO, Stance.NEUTRAL]}
    )
    summary = summarize_outcome(chamber)
    assert summary is not None
    assert summary.caveat == STANCE_CAVEAT


def test_caveat_checks_the_first_measurement_not_the_second() -> None:
    # neutral, pro, con: only the FIRST measurement is neutral, so a check reading
    # measured[1] in place of measured[0] would miss it.
    chamber = _history(
        _no_neutral_majority(), {0: [Stance.NEUTRAL, Stance.PRO, Stance.CON]}
    )
    summary = summarize_outcome(chamber)
    assert summary is not None
    assert summary.caveat == STANCE_CAVEAT


def test_caveat_checks_the_last_measurement_not_the_second() -> None:
    # con, pro, neutral: only the LAST measurement is neutral, so a check reading
    # measured[1] in place of measured[-1] would miss it.
    chamber = _history(
        _no_neutral_majority(), {0: [Stance.CON, Stance.PRO, Stance.NEUTRAL]}
    )
    summary = summarize_outcome(chamber)
    assert summary is not None
    assert summary.caveat == STANCE_CAVEAT


def test_caveat_keeps_scanning_past_a_debater_who_never_moved() -> None:
    # Ada holds her position; Zeno moves to neutral. A `break` in place of the
    # `continue` would stop at Ada and never see Zeno.
    chamber = _history(
        _no_neutral_majority(),
        {0: [Stance.PRO, Stance.PRO], 1: [Stance.CON, Stance.NEUTRAL]},
    )
    summary = summarize_outcome(chamber)
    assert summary is not None
    assert summary.caveat == STANCE_CAVEAT


# --- compliance fields on the summary (F9) ----------------------------------


def _two_debater_chamber(winner: Stance) -> tuple:  # type: ignore[type-arg]
    """Ada (pro) and Bob (con), concluded with ``winner`` prevailing."""
    ada = make_participant("Ada", Stance.PRO)
    bob = make_participant("Bob", Stance.CON)
    chamber = make_chamber(ada, bob)
    chamber.consensus = ConsensusResult(
        outcome=ConsensusOutcome.MAJORITY,
        statement="Statement.",
        winning_stance=winner,
        final_stances={str(ada.id): Stance.PRO, str(bob.id): Stance.PRO},
        unparsed=[],
    )
    return chamber, ada, bob


def test_summary_carries_the_compliance_caveat_and_lines() -> None:
    chamber, ada, bob = _two_debater_chamber(Stance.PRO)
    chamber.turns = [
        Turn(participant_id=ada.id, round_index=0, content="x", metadata={"argued": "pro"}),
        Turn(participant_id=bob.id, round_index=0, content="y", metadata={"argued": "pro"}),
    ]
    summary = summarize_outcome(chamber)
    assert summary is not None
    assert summary.compliance_caveat == UNOPPOSED_CAVEAT
    assert summary.noncompliance == (
        "Bob (assigned con) argued pro in 1 of 1 judged turns",
    )


def test_summary_compliance_fields_are_empty_when_nothing_was_judged() -> None:
    chamber, _ada, _bob = _two_debater_chamber(Stance.PRO)
    summary = summarize_outcome(chamber)
    assert summary is not None
    assert summary.compliance_caveat == ""
    assert summary.noncompliance == ()


def test_the_stance_caveat_and_the_compliance_caveat_are_separate_fields() -> None:
    """Two unrelated qualifications. Conflating them would make one of them
    unreachable whenever the other fires."""
    chamber, _ada, bob = _two_debater_chamber(Stance.PRO)
    chamber.turns = [
        Turn(participant_id=bob.id, round_index=0, content="y", metadata={"argued": "pro"}),
    ]
    summary = summarize_outcome(chamber)
    assert summary is not None
    assert summary.compliance_caveat != summary.caveat
