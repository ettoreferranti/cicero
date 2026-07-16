"""An in-memory repository — the default for tests and ephemeral use.

Deterministic, no I/O, and defensively copies stored chambers so callers cannot
mutate persisted state by holding a reference. This module carries real behaviour
(copying, ordering, existence checks) and is part of the mutation-testing gate.
"""

from __future__ import annotations

from copy import deepcopy
from uuid import UUID

from cicero.domain.models import Chamber
from cicero.persistence.repository import ChamberRepository


class InMemoryChamberRepository(ChamberRepository):
    """A dict-backed repository storing deep copies of chambers."""

    def __init__(self) -> None:
        self._store: dict[UUID, Chamber] = {}

    def add(self, chamber: Chamber) -> Chamber:
        if chamber.id in self._store:
            raise KeyError(f"chamber {chamber.id} already exists")
        self._store[chamber.id] = deepcopy(chamber)
        return chamber

    def get(self, chamber_id: UUID) -> Chamber | None:
        stored = self._store.get(chamber_id)
        return deepcopy(stored) if stored is not None else None

    def list(self) -> list[Chamber]:
        return [
            deepcopy(c)
            for c in sorted(
                self._store.values(), key=lambda c: c.created_at, reverse=True
            )
        ]

    def update(self, chamber: Chamber) -> Chamber:
        if chamber.id not in self._store:
            raise KeyError(f"chamber {chamber.id} does not exist")
        self._store[chamber.id] = deepcopy(chamber)
        return chamber

    def delete(self, chamber_id: UUID) -> bool:
        return self._store.pop(chamber_id, None) is not None
