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


def test_chamber_settings_default_and_custom(client: TestClient) -> None:
    cid = _create_chamber(client)
    settings = client.get(f"/chambers/{cid}").json()["settings"]
    assert settings["max_rounds"] == 8
    assert settings["decision_rule"] == "judge"

    resp = client.post(
        "/chambers",
        json={
            "topic": "T",
            "settings": {
                "max_rounds": 3,
                "max_duration_seconds": 120,
                "decision_rule": "majority",
                "convergence_rounds": 1,
            },
        },
    )
    assert resp.status_code == 201
    created = resp.json()["settings"]
    assert created["max_rounds"] == 3
    assert created["max_duration_seconds"] == 120
    assert created["decision_rule"] == "majority"


def test_chamber_settings_validation(client: TestClient) -> None:
    bad = [
        {"max_rounds": 0},
        {"max_rounds": 1000},
        {"min_rounds": 5, "max_rounds": 2},
        {"max_duration_seconds": -1},
        {"decision_rule": "coin-flip"},
    ]
    for settings in bad:
        resp = client.post("/chambers", json={"topic": "T", "settings": settings})
        assert resp.status_code == 422


def test_update_settings_only_while_draft(client: TestClient) -> None:
    cid = _create_chamber(client)
    resp = client.put(f"/chambers/{cid}/settings", json={"max_rounds": 2, "min_rounds": 2})
    assert resp.status_code == 200
    assert resp.json()["settings"]["max_rounds"] == 2

    _add_participant(client, cid, "A", "pro")
    _add_participant(client, cid, "B", "pro")
    assert client.post(f"/chambers/{cid}/run", params={"wait": "true"}).status_code == 200
    assert client.put(f"/chambers/{cid}/settings", json={"max_rounds": 5}).status_code == 409


def test_run_respects_chamber_settings(client: TestClient) -> None:
    cid = _create_chamber(client)
    client.put(
        f"/chambers/{cid}/settings",
        json={"max_rounds": 1, "min_rounds": 1, "convergence_rounds": 0},
    )
    _add_participant(client, cid, "A", "pro")
    _add_participant(client, cid, "B", "con")
    body = client.post(f"/chambers/{cid}/run", params={"wait": "true"}).json()
    assert body["config"]["rounds_completed"] == 1


def test_consensus_reports_winning_stance(client: TestClient) -> None:
    cid = _create_chamber(client)
    _add_participant(client, cid, "A", "pro")
    _add_participant(client, cid, "B", "pro")
    body = client.post(f"/chambers/{cid}/run", params={"wait": "true"}).json()
    # The fixture's provider polls "pro" for everyone → unanimous consensus.
    assert body["consensus"]["outcome"] == "consensus"
    assert body["consensus"]["winning_stance"] == "pro"


def test_moderator_note_on_draft_and_concluded(client: TestClient) -> None:
    cid = _create_chamber(client)
    resp = client.post(f"/chambers/{cid}/notes", json={"content": "Keep it civil."})
    assert resp.status_code == 202
    assert resp.json()["status"] == "added"
    turns = client.get(f"/chambers/{cid}").json()["turns"]
    assert turns[0]["participant_id"] is None
    assert turns[0]["content"] == "Keep it civil."
    assert turns[0]["metadata"]["kind"] == "moderator_note"

    _add_participant(client, cid, "A", "pro")
    _add_participant(client, cid, "B", "pro")
    client.post(f"/chambers/{cid}/run", params={"wait": "true"})
    assert client.post(f"/chambers/{cid}/notes", json={"content": "late"}).status_code == 409


def test_compare_two_runs(client: TestClient) -> None:
    ids = []
    for _ in range(2):
        cid = _create_chamber(client)
        _add_participant(client, cid, "A", "pro")
        _add_participant(client, cid, "B", "pro")
        client.post(f"/chambers/{cid}/run", params={"wait": "true"})
        ids.append(cid)
    resp = client.get(f"/chambers/{ids[0]}/compare/{ids[1]}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["same_topic"] is True
    assert body["a"]["outcome"] == "consensus"
    assert body["b"]["chamber_id"] == ids[1]


def test_clone_chamber_copies_setup_but_not_the_run(client: TestClient) -> None:
    cid = _create_chamber(client)
    client.put(f"/chambers/{cid}/settings", json={"max_rounds": 3, "decision_rule": "majority"})
    _add_participant(client, cid, "A", "pro")
    _add_participant(client, cid, "B", "con")
    client.post(f"/chambers/{cid}/run", params={"wait": "true"})
    source = client.get(f"/chambers/{cid}").json()
    assert source["status"] == "concluded"

    resp = client.post(f"/chambers/{cid}/clone")
    assert resp.status_code == 201
    clone = resp.json()
    assert clone["id"] != cid
    assert clone["status"] == "draft"
    assert clone["topic"] == source["topic"]
    assert clone["settings"]["max_rounds"] == 3
    assert clone["settings"]["decision_rule"] == "majority"
    assert clone["turns"] == [] and clone["consensus"] is None
    names = [(p["display_name"], p["stance"]) for p in clone["participants"]]
    assert names == [("A", "pro"), ("B", "con")]
    # Participants are fresh entities, not shared with the source.
    source_ids = {p["id"] for p in source["participants"]}
    assert all(p["id"] not in source_ids for p in clone["participants"])
    # The clone is runnable again.
    assert client.post(f"/chambers/{clone['id']}/run", params={"wait": "true"}).status_code == 200


def test_clone_missing_chamber_404(client: TestClient) -> None:
    assert client.post("/chambers/00000000-0000-0000-0000-000000000000/clone").status_code == 404


def test_compare_missing_chamber_404(client: TestClient) -> None:
    cid = _create_chamber(client)
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/chambers/{cid}/compare/{missing}").status_code == 404


def test_auth_token_required_when_configured() -> None:
    from cicero.config import Settings

    token = "sekrit"  # noqa: S105 (test-only value)
    app = create_app(Settings(api_auth_token=token, _env_file=None))  # type: ignore[call-arg]
    with TestClient(app) as anon:
        assert anon.get("/health").status_code == 200  # health stays open
        assert anon.get("/chambers").status_code == 401
        assert (
            anon.get("/chambers", headers={"Authorization": "Bearer wrong"}).status_code == 401
        )
        ok = anon.get("/chambers", headers={"Authorization": "Bearer sekrit"})
        assert ok.status_code == 200


def test_rate_limit_returns_429() -> None:
    from cicero.config import Settings

    app = create_app(Settings(rate_limit_per_minute=3, _env_file=None))  # type: ignore[call-arg]
    with TestClient(app) as limited:
        statuses = [limited.get("/chambers").status_code for _ in range(5)]
    assert statuses[:3] == [200, 200, 200]
    assert statuses[3] == statuses[4] == 429


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
