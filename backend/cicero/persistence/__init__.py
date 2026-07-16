"""Persistence layer: repository interface and implementations."""

from cicero.persistence.memory import InMemoryChamberRepository
from cicero.persistence.repository import ChamberRepository
from cicero.persistence.sqlalchemy_repo import SqlAlchemyChamberRepository


def create_repository(database_url: str | None = None) -> ChamberRepository:
    """Return a repository for ``database_url``.

    With no URL, an in-memory repository is returned (tests/ephemeral use).
    """
    if database_url is None:
        return InMemoryChamberRepository()
    return SqlAlchemyChamberRepository(database_url)


__all__ = [
    "ChamberRepository",
    "InMemoryChamberRepository",
    "SqlAlchemyChamberRepository",
    "create_repository",
]
