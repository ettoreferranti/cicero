"""End-to-end API tests using deterministic providers (no network)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from cicero.api.app import create_app
from cicero.api.dependencies import get_provider_factory, get_repository
from cicero.persistence.memory import InMemoryChamberRepository
from tests.conftest import ConstantFactory, ScriptedProvider


@pytest.fixture
def client() -> Iterator[TestClient]:
    repo = InMemoryChamberRepository()
    factory = ConstantFactory(ScriptedProvider(stance_word="pro", moderator_reply="We agree."))
    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_provider_factory] = lambda: factory
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _create_chamber(client: TestClient, topic: str = "Should we colonise Mars?") -> str:
    resp = client.post("/chambers", json={"topic": topic})
    assert resp.status_code == 201
    return resp.json()["id"]


def _add_participant(client: TestClient, cid: str, name: str, stance: str) -> None:
    resp = client.post(
        f"/chambers/{cid}/participants",
        json={"display_name": name, "provider": "mock", "model": "m", "stance": stance},
    )
    assert resp.status_code == 201


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_security_headers_present(client: TestClient) -> None:
    headers = client.get("/health").headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert "default-src 'none'" in headers["Content-Security-Policy"]


def test_create_list_get_delete_chamber(client: TestClient) -> None:
    cid = _create_chamber(client)
    assert any(c["id"] == cid for c in client.get("/chambers").json())
    assert client.get(f"/chambers/{cid}").json()["topic"] == "Should we colonise Mars?"
    assert client.delete(f"/chambers/{cid}").status_code == 204
    assert client.get(f"/chambers/{cid}").status_code == 404


def test_create_chamber_rejects_empty_topic(client: TestClient) -> None:
    assert client.post("/chambers", json={"topic": ""}).status_code == 422


def test_create_chamber_rejects_unknown_field(client: TestClient) -> None:
    resp = client.post("/chambers", json={"topic": "T", "surprise": 1})
    assert resp.status_code == 422


def test_add_participant_defaults_to_neutral(client: TestClient) -> None:
    cid = _create_chamber(client)
    resp = client.post(
        f"/chambers/{cid}/participants",
        json={"display_name": "Athena", "provider": "mock", "model": "m"},
    )
    assert resp.status_code == 201
    assert resp.json()["participants"][0]["stance"] == "neutral"


def test_run_debate_reaches_consensus(client: TestClient) -> None:
    cid = _create_chamber(client)
    _add_participant(client, cid, "Pro-A", "pro")
    _add_participant(client, cid, "Pro-B", "pro")

    resp = client.post(f"/chambers/{cid}/run")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "concluded"
    assert body["consensus"]["outcome"] == "consensus"
    assert body["consensus"]["statement"] == "We agree."
    assert len(body["turns"]) >= 2


def test_run_requires_two_participants(client: TestClient) -> None:
    cid = _create_chamber(client)
    _add_participant(client, cid, "Solo", "pro")
    resp = client.post(f"/chambers/{cid}/run")
    assert resp.status_code == 409


def test_cannot_add_participant_after_run(client: TestClient) -> None:
    cid = _create_chamber(client)
    _add_participant(client, cid, "A", "pro")
    _add_participant(client, cid, "B", "pro")
    assert client.post(f"/chambers/{cid}/run").status_code == 200
    # Chamber is concluded now.
    resp = client.post(
        f"/chambers/{cid}/participants",
        json={"display_name": "C", "provider": "mock", "model": "m"},
    )
    assert resp.status_code == 409


def test_run_missing_chamber_returns_404(client: TestClient) -> None:
    assert client.post("/chambers/00000000-0000-0000-0000-000000000000/run").status_code == 404
