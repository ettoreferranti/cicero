"""End-to-end API tests using deterministic providers (no network)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from cicero.api.app import create_app
from cicero.api.debate_manager import DebateManager
from cicero.api.dependencies import (
    get_debate_manager,
    get_provider_factory,
    get_repository,
)
from cicero.persistence.memory import InMemoryChamberRepository
from tests.conftest import ConstantFactory, ScriptedProvider


@pytest.fixture
def client() -> Iterator[TestClient]:
    repo = InMemoryChamberRepository()
    factory = ConstantFactory(ScriptedProvider(stance_word="pro", moderator_reply="We agree."))
    manager = DebateManager()
    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_provider_factory] = lambda: factory
    app.dependency_overrides[get_debate_manager] = lambda: manager
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
    # Data endpoints keep the strict, locked-down policy.
    assert headers["Content-Security-Policy"] == "default-src 'none'; frame-ancestors 'none'"


def test_docs_ui_served_with_scoped_csp(client: TestClient) -> None:
    # The blank-docs bug: a strict CSP on /docs blocks Swagger UI's assets.
    resp = client.get("/docs")
    assert resp.status_code == 200
    csp = resp.headers["Content-Security-Policy"]
    assert "cdn.jsdelivr.net" in csp  # Swagger UI assets are permitted here
    assert "default-src 'none'" not in csp


def test_openapi_schema_available(client: TestClient) -> None:
    assert client.get("/openapi.json").status_code == 200


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

    resp = client.post(f"/chambers/{cid}/run", params={"wait": "true"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "concluded"
    assert body["consensus"]["outcome"] == "consensus"
    assert body["consensus"]["statement"] == "We agree."
    assert len(body["turns"]) >= 2


def test_run_async_returns_202(client: TestClient) -> None:
    cid = _create_chamber(client)
    _add_participant(client, cid, "Pro-A", "pro")
    _add_participant(client, cid, "Pro-B", "pro")
    resp = client.post(f"/chambers/{cid}/run")
    assert resp.status_code == 202
    assert resp.json()["status"] == "running"


def test_export_json_and_markdown(client: TestClient) -> None:
    cid = _create_chamber(client)
    _add_participant(client, cid, "Pro-A", "pro")
    _add_participant(client, cid, "Pro-B", "pro")
    client.post(f"/chambers/{cid}/run", params={"wait": "true"})

    as_json = client.get(f"/chambers/{cid}/export", params={"format": "json"})
    assert as_json.status_code == 200
    assert as_json.json()["topic"] == "Should we colonise Mars?"

    as_md = client.get(f"/chambers/{cid}/export", params={"format": "markdown"})
    assert as_md.status_code == 200
    assert as_md.headers["content-type"].startswith("text/markdown")
    assert "# Debate:" in as_md.text


def test_export_rejects_bad_format(client: TestClient) -> None:
    cid = _create_chamber(client)
    assert client.get(f"/chambers/{cid}/export", params={"format": "pdf"}).status_code == 400


def test_metrics_endpoint(client: TestClient) -> None:
    cid = _create_chamber(client)
    _add_participant(client, cid, "Pro-A", "pro")
    _add_participant(client, cid, "Pro-B", "pro")
    client.post(f"/chambers/{cid}/run", params={"wait": "true"})
    resp = client.get(f"/chambers/{cid}/metrics")
    assert resp.status_code == 200
    metrics = resp.json()
    assert len(metrics) == 2
    assert all("turns" in m and "prompt_tokens" in m for m in metrics)


def test_stop_without_running_debate_409(client: TestClient) -> None:
    cid = _create_chamber(client)
    assert client.post(f"/chambers/{cid}/stop").status_code == 409


def test_events_unknown_chamber_404(client: TestClient) -> None:
    resp = client.get("/chambers/00000000-0000-0000-0000-000000000000/events")
    assert resp.status_code == 404


def test_run_requires_two_participants(client: TestClient) -> None:
    cid = _create_chamber(client)
    _add_participant(client, cid, "Solo", "pro")
    resp = client.post(f"/chambers/{cid}/run")
    assert resp.status_code == 409


def test_cannot_add_participant_after_run(client: TestClient) -> None:
    cid = _create_chamber(client)
    _add_participant(client, cid, "A", "pro")
    _add_participant(client, cid, "B", "pro")
    assert client.post(f"/chambers/{cid}/run", params={"wait": "true"}).status_code == 200
    # Chamber is concluded now.
    resp = client.post(
        f"/chambers/{cid}/participants",
        json={"display_name": "C", "provider": "mock", "model": "m"},
    )
    assert resp.status_code == 409


def test_run_missing_chamber_returns_404(client: TestClient) -> None:
    assert client.post("/chambers/00000000-0000-0000-0000-000000000000/run").status_code == 404


def test_list_providers(client: TestClient) -> None:
    resp = client.get("/providers")
    assert resp.status_code == 200
    assert set(resp.json()) == {"mock", "ollama", "anthropic"}


def test_list_models_for_provider(client: TestClient) -> None:
    # The fixture's factory serves a ScriptedProvider for every type.
    resp = client.get("/providers/mock/models")
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "mock"
    assert body["models"] == ["scripted"]


def test_list_models_unknown_provider_422(client: TestClient) -> None:
    assert client.get("/providers/nonsense/models").status_code == 422


def test_list_models_provider_error_returns_502() -> None:
    from cicero.domain.enums import ProviderType
    from cicero.domain.models import Participant
    from cicero.providers import Provider, ProviderError

    class BrokenFactory:
        def get(self, participant: Participant) -> Provider:
            raise ProviderError("unconfigured")

        def get_for_type(self, provider_type: ProviderType) -> Provider:
            raise ProviderError("Anthropic API key is not configured")

    app = create_app()
    app.dependency_overrides[get_provider_factory] = lambda: BrokenFactory()
    with TestClient(app) as broken_client:
        resp = broken_client.get("/providers/anthropic/models")
    app.dependency_overrides.clear()
    assert resp.status_code == 502
    assert "not configured" in resp.json()["detail"]
