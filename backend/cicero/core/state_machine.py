"""The chamber lifecycle state machine (see FR-4).

Transitions are declared as data and enforced centrally so no other code can
move a chamber into an illegal state.
"""

from __future__ import annotations

from cicero.domain.enums import ChamberStatus
from cicero.domain.models import Chamber

#: Allowed target states for each current state. States absent as keys, or with
#: an empty target set (e.g. ARCHIVED), are terminal.
ALLOWED_TRANSITIONS: dict[ChamberStatus, frozenset[ChamberStatus]] = {
    ChamberStatus.DRAFT: frozenset({ChamberStatus.RUNNING, ChamberStatus.ARCHIVED}),
    ChamberStatus.RUNNING: frozenset({ChamberStatus.PAUSED, ChamberStatus.CONCLUDED}),
    ChamberStatus.PAUSED: frozenset(
        {ChamberStatus.RUNNING, ChamberStatus.CONCLUDED, ChamberStatus.ARCHIVED}
    ),
    ChamberStatus.CONCLUDED: frozenset({ChamberStatus.ARCHIVED}),
    ChamberStatus.ARCHIVED: frozenset(),
}


class InvalidTransition(ValueError):
    """Raised when a chamber is moved into a state it cannot reach."""


def can_transition(current: ChamberStatus, target: ChamberStatus) -> bool:
    """Return whether ``current`` may transition directly to ``target``."""
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def transition(chamber: Chamber, target: ChamberStatus) -> Chamber:
    """Move ``chamber`` to ``target``, enforcing the lifecycle rules.

    Returns the same chamber (mutated in place) for convenient chaining.

    Raises:
        InvalidTransition: if the move is not permitted.
    """
    if not can_transition(chamber.status, target):
        raise InvalidTransition(
            f"cannot move chamber from {chamber.status.value} to {target.value}"
        )
    chamber.status = target
    return chamber
