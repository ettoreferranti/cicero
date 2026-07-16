"""Contract tests run against every repository implementation."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest

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
