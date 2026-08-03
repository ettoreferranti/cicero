"""Who is actually debating right now — the muting rules (FR-13, backlog D6).

Muting is deliberately *not* removal: a muted debater keeps its turns in the
transcript, stays in the chamber, and is still polled for its stance, so the
stance history (FR-25) has no hole in it. What it loses is the two things that
make it a participant in the argument — it stops taking turns, and it stops
counting toward the decision rule.

That second part means muting can change an outcome: excluding one debater can
turn a split into a consensus, or a clean majority into a judge's verdict. That
is the intended power of the control, not a side effect.

Pure and dependency-light (domain models only), so both the engine and the
consensus layer can use it without an import cycle, and it sits in the mutation
gate.
"""

from __future__ import annotations

from cicero.domain.enums import Stance
from cicero.domain.models import Chamber, Participant


def active_participants(chamber: Chamber) -> list[Participant]:
    """The debaters currently taking turns — muted ones sit out."""
    return [participant for participant in chamber.participants if not participant.muted]


def active_stances(chamber: Chamber, stances: dict[str, Stance]) -> dict[str, Stance]:
    """``stances`` restricted to the debaters that still carry a vote.

    Falls back to the full set when every debater is muted, so a fully muted
    chamber still resolves to *something* rather than an empty tally that would
    read as "no agreement" for the wrong reason.
    """
    active = {str(participant.id) for participant in active_participants(chamber)}
    deciding = {pid: stance for pid, stance in stances.items() if pid in active}
    return deciding or stances
