"""Recover debates interrupted by a crash or restart (J3, NFR-R-3).

A chamber left in ``running`` has no live task after a restart — it would be
wedged forever. Parking it in ``paused`` makes it resumable via
``POST /chambers/{id}/resume``. Runs once when the repository is first opened.
"""

from __future__ import annotations

from cicero.core.state_machine import transition
from cicero.domain.enums import ChamberStatus
from cicero.domain.models import Chamber
from cicero.persistence.repository import ChamberRepository


def recover_interrupted_debates(repo: ChamberRepository) -> list[Chamber]:
    """Move every ``running`` chamber to ``paused``; returns those recovered."""
    recovered: list[Chamber] = []
    for chamber in repo.list():
        if chamber.status is ChamberStatus.RUNNING:
            transition(chamber, ChamberStatus.PAUSED)
            repo.update(chamber)
            recovered.append(chamber)
    return recovered
