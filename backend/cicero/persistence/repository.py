"""The repository interface (persistence boundary).

The debate core depends on :class:`ChamberRepository`, never on a concrete store
(NFR-M-3). Concrete implementations live alongside this module (see
``memory.py`` and ``sqlalchemy_repo.py``).
"""

from __future__ import annotations

import abc
from uuid import UUID

from cicero.domain.models import Chamber


class ChamberRepository(abc.ABC):
    """Persistence boundary for chambers."""

    @abc.abstractmethod
    def add(self, chamber: Chamber) -> Chamber:
        """Persist a new chamber. Raises ``KeyError`` if the id already exists."""

    @abc.abstractmethod
    def get(self, chamber_id: UUID) -> Chamber | None:
        """Return the chamber with ``chamber_id``, or ``None``."""

    @abc.abstractmethod
    def list(self) -> list[Chamber]:
        """Return all chambers, newest first."""

    @abc.abstractmethod
    def update(self, chamber: Chamber) -> Chamber:
        """Persist changes to an existing chamber. Raises ``KeyError`` if absent."""

    @abc.abstractmethod
    def delete(self, chamber_id: UUID) -> bool:
        """Delete a chamber. Returns ``True`` if a chamber was removed."""
