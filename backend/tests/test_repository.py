"""Contract tests run against every repository implementation."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from cicero.domain import Chamber, ProviderType, Stance
from cicero.domain.models import Participant
from cicero.persistence import (
    ChamberRepository,
    InMemoryChamberRepository,
    SqlAlchemyChamberRepository,
)


@pytest.fixture(params=["memory", "sqlite"])
def repo(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ChamberRepository]:
    if request.param == "memory":
        yield InMemoryChamberRepository()
    else:
        yield SqlAlchemyChamberRepository(f"sqlite:///{tmp_path / 'test.db'}")


def _chamber(topic: str = "Should AI be regulated?") -> Chamber:
    return Chamber(
        topic=topic,
        participants=[
            Participant(
                display_name="Athena",
                provider=ProviderType.MOCK,
                model="mock-small",
                stance=Stance.PRO,
            )
        ],
    )


def test_add_and_get_roundtrip(repo: ChamberRepository) -> None:
    chamber = _chamber()
    repo.add(chamber)
    fetched = repo.get(chamber.id)
    assert fetched is not None
    assert fetched.id == chamber.id
    assert fetched.topic == chamber.topic
    assert fetched.participants[0].stance is Stance.PRO


def test_get_missing_returns_none(repo: ChamberRepository) -> None:
    assert repo.get(uuid4()) is None


def test_add_duplicate_raises(repo: ChamberRepository) -> None:
    chamber = _chamber()
    repo.add(chamber)
    with pytest.raises(KeyError) as exc:
        repo.add(chamber)
    assert exc.value.args[0] == f"chamber {chamber.id} already exists"


def test_update_persists_changes(repo: ChamberRepository) -> None:
    chamber = _chamber()
    repo.add(chamber)
    chamber.topic = "A new topic"
    repo.update(chamber)
    fetched = repo.get(chamber.id)
    assert fetched is not None
    assert fetched.topic == "A new topic"


def test_update_missing_raises(repo: ChamberRepository) -> None:
    chamber = _chamber()
    with pytest.raises(KeyError) as exc:
        repo.update(chamber)
    assert exc.value.args[0] == f"chamber {chamber.id} does not exist"


def test_delete_removes(repo: ChamberRepository) -> None:
    chamber = _chamber()
    repo.add(chamber)
    assert repo.delete(chamber.id) is True
    assert repo.get(chamber.id) is None


def test_delete_missing_returns_false(repo: ChamberRepository) -> None:
    assert repo.delete(uuid4()) is False


def test_list_returns_newest_first(repo: ChamberRepository) -> None:
    older = _chamber("older")
    newer = _chamber("newer")
    # Ensure a strict ordering regardless of clock resolution.
    older.created_at = older.created_at.replace(year=2020)
    newer.created_at = newer.created_at.replace(year=2030)
    repo.add(older)
    repo.add(newer)
    listed = repo.list()
    assert [c.topic for c in listed] == ["newer", "older"]


def test_get_returns_defensive_copy(repo: ChamberRepository) -> None:
    # Mutating a fetched chamber must not change persisted state until update().
    chamber = _chamber()
    repo.add(chamber)
    fetched = repo.get(chamber.id)
    assert fetched is not None
    fetched.topic = "mutated locally"
    again = repo.get(chamber.id)
    assert again is not None
    assert again.topic == chamber.topic


def test_pre_d7_chamber_with_style_still_loads(tmp_path: Path) -> None:
    """Chambers saved before `style` was renamed to `instructions` must survive.

    Chambers persist as a JSON document and every domain model forbids extra
    keys, so without the deprecated alias this row would fail validation on read
    and the chamber would be permanently unopenable — not a cosmetic break.
    """
    db = f"sqlite:///{tmp_path / 'legacy.db'}"
    repo = SqlAlchemyChamberRepository(db)
    chamber = _chamber()
    chamber.participants[0].tuning.instructions = "be aggressive"
    repo.add(chamber)

    # Rewrite the stored document the way a pre-D7 build would have written it.
    engine = create_engine(db, connect_args={"check_same_thread": False})
    with engine.begin() as conn:
        stored = conn.execute(
            text("SELECT data FROM chambers WHERE id = :id"), {"id": str(chamber.id)}
        ).scalar_one()
        legacy = stored.replace('"instructions":', '"style":')
        assert '"style":' in legacy  # the rewrite actually did something
        conn.execute(
            text("UPDATE chambers SET data = :data WHERE id = :id"),
            {"data": legacy, "id": str(chamber.id)},
        )

    fetched = SqlAlchemyChamberRepository(db).get(chamber.id)
    assert fetched is not None
    assert fetched.participants[0].tuning.instructions == "be aggressive"


def test_instructions_are_written_back_under_the_new_name(tmp_path: Path) -> None:
    """The alias is read-only: nothing re-emits the deprecated `style` key."""
    db = f"sqlite:///{tmp_path / 'writeback.db'}"
    repo = SqlAlchemyChamberRepository(db)
    chamber = _chamber()
    chamber.participants[0].tuning.instructions = "speak in rhyme"
    repo.add(chamber)

    engine = create_engine(db, connect_args={"check_same_thread": False})
    with engine.begin() as conn:
        stored = conn.execute(
            text("SELECT data FROM chambers WHERE id = :id"), {"id": str(chamber.id)}
        ).scalar_one()
    assert '"instructions":"speak in rhyme"' in stored
    assert '"style"' not in stored
