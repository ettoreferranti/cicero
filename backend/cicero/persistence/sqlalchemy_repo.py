"""SQLAlchemy-backed repository (SQLite by default — approved D-4).

Chambers are stored as a validated JSON document keyed by id. This keeps the
domain model the single source of truth (no ORM/domain drift) while still giving
durable, queryable persistence. A relational mapping can replace this later
without changing the :class:`ChamberRepository` contract.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from cicero.domain.models import Chamber
from cicero.persistence.repository import ChamberRepository


class _Base(DeclarativeBase):
    pass


class _ChamberRow(_Base):
    __tablename__ = "chambers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # Sortable ISO-8601 UTC timestamp for "newest first" ordering.
    created_at: Mapped[str] = mapped_column(String(40), index=True)
    data: Mapped[str] = mapped_column(Text)


class SqlAlchemyChamberRepository(ChamberRepository):
    """Durable repository backed by any SQLAlchemy-supported database."""

    def __init__(self, database_url: str) -> None:
        # check_same_thread is only relevant to SQLite; harmless elsewhere.
        connect_args = (
            {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        )
        self._engine = create_engine(database_url, connect_args=connect_args)
        _Base.metadata.create_all(self._engine)

    def add(self, chamber: Chamber) -> Chamber:
        with Session(self._engine) as session:
            if session.get(_ChamberRow, str(chamber.id)) is not None:
                raise KeyError(f"chamber {chamber.id} already exists")
            session.add(self._to_row(chamber))
            session.commit()
        return chamber

    def get(self, chamber_id: UUID) -> Chamber | None:
        with Session(self._engine) as session:
            row = session.get(_ChamberRow, str(chamber_id))
            return self._to_chamber(row) if row is not None else None

    def list(self) -> list[Chamber]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(_ChamberRow).order_by(_ChamberRow.created_at.desc())
            ).all()
            return [self._to_chamber(row) for row in rows]

    def update(self, chamber: Chamber) -> Chamber:
        with Session(self._engine) as session:
            row = session.get(_ChamberRow, str(chamber.id))
            if row is None:
                raise KeyError(f"chamber {chamber.id} does not exist")
            row.data = chamber.model_dump_json()
            row.created_at = chamber.created_at.isoformat()
            session.commit()
        return chamber

    def delete(self, chamber_id: UUID) -> bool:
        with Session(self._engine) as session:
            row = session.get(_ChamberRow, str(chamber_id))
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    @staticmethod
    def _to_row(chamber: Chamber) -> _ChamberRow:
        return _ChamberRow(
            id=str(chamber.id),
            created_at=chamber.created_at.isoformat(),
            data=chamber.model_dump_json(),
        )

    @staticmethod
    def _to_chamber(row: _ChamberRow) -> Chamber:
        return Chamber.model_validate_json(row.data)
