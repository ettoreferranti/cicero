"""Derived, human-readable outcome facts (F5, FR-23).

The stance word alone is not a result: ``CONVERGE_GUIDANCE`` invites debaters to
propose a compromise, and the one-word poll has no way to name one, so a
compromise collapses to ``neutral``. The headline (written by the moderator, on
``ConsensusResult``) says what was concluded; this module says how firmly it was
held, how it was decided, and who moved to get there.

Everything here is **derived**, not stored, so a chamber concluded before F5
existed gains all three values without re-running. Pure and deterministic — part
of the mutation-testing gate.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from cicero.core.roster import deciding_stances
from cicero.domain.enums import ConsensusOutcome, Stance
from cicero.domain.models import Chamber

UNMEASURED = "unmeasured — no debater's final position could be read"
UNRESOLVED = "unresolved — no position prevailed"

_BASIS: dict[ConsensusOutcome, str] = {
    ConsensusOutcome.CONSENSUS: "all debaters converged",
    ConsensusOutcome.MAJORITY: "majority of final positions",
    ConsensusOutcome.VERDICT: "moderator's verdict on argument strength",
}


@dataclass(frozen=True)
class OutcomeSummary:
    """Everything a reader needs at a glance, above the full statement."""

    headline: str
    support: str
    decided_by: str
    movements: tuple[str, ...]


def _effective_unparsed(chamber: Chamber) -> tuple[str, ...]:
    """The unparsed set the outcome was decided on.

    Prefers the set recorded on the result, which is exact. Chambers concluded
    before that field existed fall back to the last recorded poll — an
    approximation, because a debate that ended on repetition or max-rounds took a
    final poll that ``_record_poll`` did not persist. Approximating beats showing
    nothing for every debate already run.
    """
    consensus = chamber.consensus
    if consensus is not None and consensus.unparsed is not None:
        return tuple(consensus.unparsed)
    if chamber.stance_history:
        return tuple(chamber.stance_history[-1].unparsed)
    return ()


def support_summary(chamber: Chamber) -> str:
    """How firmly the winning position was held, in the reader's words."""
    consensus = chamber.consensus
    if consensus is None:
        return ""
    deciding = deciding_stances(
        chamber, consensus.final_stances, _effective_unparsed(chamber)
    )
    if not deciding:
        return UNMEASURED
    total = len(deciding)
    winner = consensus.winning_stance
    if consensus.outcome is ConsensusOutcome.CONSENSUS:
        # ``winner`` should always be set here (is_consensus implies a single
        # stance), but a chamber concluded before that invariant held is still
        # read through this code path, so fall back rather than lie about it.
        if winner is None:
            return f"unanimous — all {total} debaters"
        return f"unanimous — all {total} debaters on {winner.value}"
    if consensus.outcome is ConsensusOutcome.VERDICT:
        # Checked ahead of the shared "winner is None" guard below: a VERDICT
        # whose WINNER: line could not be read must not be reported as
        # "unresolved" — the judge did decide, it just could not be recorded.
        if winner is None:
            return "judge-decided — the judge's ruling could not be read"
        return f"judge-decided — {winner.value} ruled, no majority among {total} debaters"
    if winner is None:
        return UNRESOLVED
    counts = Counter(deciding.values())
    held = counts[winner]
    # Name what the non-winners actually held instead of calling all of them
    # "dissent" — a debater who ended on NEUTRAL declined to take a side (or
    # proposed the compromise the headline states), which is not the same claim
    # as opposing the winner. Ordered by the enum's own declaration order so the
    # sentence is deterministic, not by count or dict insertion order.
    breakdown = ", ".join(
        f"{counts[stance]} {stance.value}"
        for stance in Stance
        if stance is not winner and counts[stance] > 0
    )
    if not breakdown:
        # Unreachable for MAJORITY in practice (everyone agreeing yields
        # CONSENSUS, not MAJORITY), but if it ever is reached there is no
        # non-winning stance to name — say so plainly rather than emitting a
        # dangling "()".
        return f"contested — {held} of {total} debaters settled on {winner.value}"
    return f"contested — {held} of {total} debaters settled on {winner.value} ({breakdown})"


def decision_basis(chamber: Chamber) -> str:
    """Which rule produced the outcome."""
    consensus = chamber.consensus
    if consensus is None:
        return ""
    basis = _BASIS.get(consensus.outcome)
    if basis is not None:
        return basis
    return f"unresolved under the {chamber.settings.decision_rule.value} rule"


def movements(chamber: Chamber) -> tuple[str, ...]:
    """Who changed position between their first and last *measured* poll.

    Polls where a debater's reply could not be read are skipped for that debater:
    the value there was carried forward, and reporting it would present a parse
    failure as someone changing their mind — the exact confusion
    ``StancePoll.unparsed`` exists to prevent.
    """
    moved: list[str] = []
    for participant in chamber.participants:
        key = str(participant.id)
        measured = [
            poll.stances[key]
            for poll in chamber.stance_history
            if key in poll.stances and key not in poll.unparsed
        ]
        if len(measured) < 2 or measured[0] is measured[-1]:
            continue
        # Muted debaters are still polled (FR-13), so they still have a
        # trajectory — flagged, because it did not count toward the outcome.
        suffix = ", muted" if participant.muted else ""
        moved.append(
            f"{participant.display_name} "
            f"({measured[0].value}→{measured[-1].value}{suffix})"
        )
    return tuple(moved)


def summarize_outcome(chamber: Chamber) -> OutcomeSummary | None:
    """The full derived summary, or ``None`` when the debate has no outcome yet."""
    consensus = chamber.consensus
    if consensus is None:
        return None
    return OutcomeSummary(
        headline=consensus.headline,
        support=support_summary(chamber),
        decided_by=decision_basis(chamber),
        movements=movements(chamber),
    )
