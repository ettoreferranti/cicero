"""Tests for the derived outcome summary (F5)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from cicero.core.outcome import (
    OutcomeSummary,
    decision_basis,
    movements,
    summarize_outcome,
    support_summary,
)
from cicero.domain.enums import ConsensusOutcome, DecisionRule, Stance
from cicero.domain.models import ConsensusResult, StancePoll
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
    assert support_summary(chamber) == "unanimous — all 3 debaters on pro"


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
    assert support_summary(chamber) == "unanimous — all 3 debaters"


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
    assert support_summary(chamber) == "contested — 2 of 3 debaters settled on neutral (1 con)"


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
        "contested — 2 of 4 debaters settled on pro (1 con, 1 neutral)"
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
    assert support_summary(chamber) == "contested — 3 of 3 debaters settled on pro"


def test_support_reports_a_judge_verdict() -> None:
    chamber = _concluded(
        ConsensusOutcome.VERDICT,
        {"a": Stance.PRO, "b": Stance.CON, "c": Stance.NEUTRAL},
        winner=Stance.PRO,
        unparsed=[],
    )
    assert support_summary(chamber) == "judge-decided — pro ruled, no majority among 3 debaters"


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
    assert support_summary(chamber) == "judge-decided — the judge's ruling could not be read"


def test_support_reports_disagreement_as_unresolved() -> None:
    chamber = _concluded(
        ConsensusOutcome.DISAGREEMENT,
        {"a": Stance.PRO, "b": Stance.CON, "c": Stance.NEUTRAL},
        unparsed=[],
    )
    assert support_summary(chamber) == "unresolved — no position prevailed"


def test_support_excludes_a_muted_debater_from_the_denominator() -> None:
    chamber = _concluded(
        ConsensusOutcome.CONSENSUS,
        {"a": Stance.PRO, "b": Stance.PRO, "c": Stance.PRO},
        winner=Stance.PRO,
        unparsed=[],
    )
    chamber.participants[2].muted = True
    # A muted debater does not vote (FR-13), so it cannot pad the support count.
    assert support_summary(chamber) == "unanimous — all 2 debaters on pro"


def test_support_says_unmeasured_when_nothing_was_recorded() -> None:
    chamber = _concluded(ConsensusOutcome.DISAGREEMENT, {}, unparsed=[])
    chamber.consensus.final_stances = {}
    # Never "0 of 3", which reads as a measured result of zero support.
    assert support_summary(chamber) == (
        "unmeasured — no debater's final position could be read"
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
    assert support_summary(chamber) == "unanimous — all 3 debaters on pro"


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
    assert support_summary(chamber) == "unanimous — all 2 debaters on pro"


def test_support_for_a_historical_chamber_without_history_counts_everyone() -> None:
    chamber = _concluded(
        ConsensusOutcome.CONSENSUS,
        {"a": Stance.PRO, "b": Stance.PRO, "c": Stance.PRO},
        winner=Stance.PRO,
        unparsed=None,
    )
    assert support_summary(chamber) == "unanimous — all 3 debaters on pro"


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
    assert movements(chamber) == ("Ada (pro→neutral)",)


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
    assert movements(chamber) == ("Ada (pro→neutral, muted)",)


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
    assert summary.support == "unanimous — all 3 debaters on pro"
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
    assert movements(chamber) == ("Zeno (con→neutral)",)


def test_movements_reports_the_last_measurement_not_the_second() -> None:
    chamber = _concluded(ConsensusOutcome.DISAGREEMENT, {"a": Stance.PRO})
    ada = chamber.participants[0]
    chamber.stance_history = [
        StancePoll(round_index=0, stances={str(ada.id): Stance.PRO}),
        StancePoll(round_index=1, stances={str(ada.id): Stance.CON}),
        StancePoll(round_index=2, stances={str(ada.id): Stance.NEUTRAL}),
    ]
    assert movements(chamber) == ("Ada (pro→neutral)",)


def test_outcome_summary_is_frozen() -> None:
    summary = OutcomeSummary(headline="", support="", decided_by="", movements=())
    with pytest.raises(FrozenInstanceError):
        summary.headline = "changed"
