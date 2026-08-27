"""End-to-end API tests using deterministic providers (no network)."""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from cicero.api.app import create_app
from cicero.api.debate_manager import DebateManager
from cicero.api.dependencies import (
    get_debate_manager,
    get_provider_factory,
    get_repository,
)
from cicero.api.schemas import DebateSettingsIn
from cicero.domain.enums import ChamberStatus, ProviderType, Stance
from cicero.domain.models import (
    Chamber,
    DebateSettings,
    Moderator,
    Participant,
    ParticipantTuning,
    Turn,
)
from cicero.persistence.memory import InMemoryChamberRepository
from cicero.providers import ProviderError
from cicero.providers.mock import MockProvider
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


def _create_chamber(
    client: TestClient,
    topic: str = "Should we colonise Mars?",
    settings: dict[str, object] | None = None,
) -> str:
    payload: dict[str, object] = {"topic": topic}
    if settings is not None:
        payload["settings"] = settings
    resp = client.post("/chambers", json=payload)
    assert resp.status_code == 201
    return resp.json()["id"]


#: A chamber that may conclude after a single round. Stepping and resuming are
#: about turn *mechanics*, so those tests pin the round floor rather than
#: inheriting the default and paying for two more rounds of scripted argument.
_STOPS_AFTER_ONE_ROUND: dict[str, object] = {"min_rounds": 1}


def _add_participant(client: TestClient, cid: str, name: str, stance: str) -> None:
    resp = client.post(
        f"/chambers/{cid}/participants",
        json={"display_name": name, "provider": "mock", "model": "scripted", "stance": stance},
    )
    assert resp.status_code == 201


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_config_reports_web_access_capability() -> None:
    from cicero.config import Settings

    off = create_app(Settings(_env_file=None))  # type: ignore[call-arg]
    with TestClient(off) as c:
        assert c.get("/config").json() == {"web_access_enabled": False}

    on = create_app(Settings(web_access_enabled=True, _env_file=None))  # type: ignore[call-arg]
    with TestClient(on) as c:
        assert c.get("/config").json() == {"web_access_enabled": True}


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
        json={"display_name": "Athena", "provider": "mock", "model": "scripted"},
    )
    assert resp.status_code == 201
    assert resp.json()["participants"][0]["stance"] == "neutral"


def test_add_participant_rejects_a_model_the_provider_cannot_serve(
    client: TestClient,
) -> None:
    cid = _create_chamber(client)
    resp = client.post(
        f"/chambers/{cid}/participants",
        json={"display_name": "Ghost", "provider": "mock", "model": "no-such-model"},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    # The error names what the provider *can* serve, so it is actionable.
    assert "no-such-model" in detail
    assert "scripted" in detail
    assert client.get(f"/chambers/{cid}").json()["participants"] == []


def test_edit_participant_revalidates_only_when_the_model_changes(
    client: TestClient,
) -> None:
    cid = _create_chamber(client)
    _add_participant(client, cid, "Ada", "pro")
    pid = client.get(f"/chambers/{cid}").json()["participants"][0]["id"]

    bad = client.patch(f"/chambers/{cid}/participants/{pid}", json={"model": "nope"})
    assert bad.status_code == 422

    # A rename touches no provider, so it is not gated on provider reachability.
    renamed = client.patch(
        f"/chambers/{cid}/participants/{pid}", json={"display_name": "Ada L."}
    )
    assert renamed.status_code == 200
    assert renamed.json()["participants"][0]["display_name"] == "Ada L."


def test_add_participant_reports_an_unreachable_provider() -> None:
    class DeadFactory:
        def get(self, participant):  # type: ignore[no-untyped-def]
            raise ProviderError("Ollama request failed: ConnectError")

        def get_for_type(self, provider_type):  # type: ignore[no-untyped-def]
            raise ProviderError("Ollama request failed: ConnectError")

    repo = InMemoryChamberRepository()
    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_provider_factory] = DeadFactory
    with TestClient(app) as client:
        cid = _create_chamber(client)
        resp = client.post(
            f"/chambers/{cid}/participants",
            json={"display_name": "Ada", "provider": "ollama", "model": "llama3"},
        )
        # Connectivity is the other half of FR-12: a provider that cannot be
        # reached is a 502, distinct from a model that does not exist (422).
        assert resp.status_code == 502
        assert "ConnectError" in resp.json()["detail"]
    app.dependency_overrides.clear()


def test_add_participant_accepts_tuning(client: TestClient) -> None:
    cid = _create_chamber(client)
    resp = client.post(
        f"/chambers/{cid}/participants",
        json={
            "display_name": "Ada",
            "provider": "mock",
            "model": "scripted",
            "stance": "pro",
            "tuning": {
                "temperature": 0.2,
                "max_tokens": 1500,
                "persona": "a cautious economist",
                "instructions": "terse",
            },
        },
    )
    assert resp.status_code == 201
    tuning = resp.json()["participants"][0]["tuning"]
    assert tuning == {
        "temperature": 0.2,
        "max_tokens": 1500,
        "persona": "a cautious economist",
        "instructions": "terse",
        # Added by issue #28. Kept as an exact assertion on purpose: the tuning
        # block is part of the API response shape, and an accidental change to it
        # should fail here rather than surface in a client.
        "allow_reasoning": True,
    }
    # Out-of-range tuning is rejected, not clamped.
    bad = client.post(
        f"/chambers/{cid}/participants",
        json={
            "display_name": "Zeno",
            "provider": "mock",
            "model": "scripted",
            "tuning": {"temperature": 5},
        },
    )
    assert bad.status_code == 422


def test_edit_chamber_while_draft(client: TestClient) -> None:
    cid = _create_chamber(client)
    resp = client.patch(f"/chambers/{cid}", json={"topic": "Should we colonise Venus?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["topic"] == "Should we colonise Venus?"
    assert body["category"] == ""  # untouched fields are left alone

    # A partial edit leaves the previous topic in place.
    resp = client.patch(f"/chambers/{cid}", json={"category": "space", "description": "d"})
    body = resp.json()
    assert (body["topic"], body["category"], body["description"]) == (
        "Should we colonise Venus?",
        "space",
        "d",
    )

    assert client.patch(f"/chambers/{cid}", json={"topic": ""}).status_code == 422
    assert client.patch(f"/chambers/{cid}", json={"surprise": 1}).status_code == 422
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.patch(f"/chambers/{missing}", json={"topic": "T"}).status_code == 404


def test_edit_and_remove_participants_while_draft(client: TestClient) -> None:
    cid = _create_chamber(client)
    _add_participant(client, cid, "Ada", "pro")
    _add_participant(client, cid, "Zeno", "con")
    roster = client.get(f"/chambers/{cid}").json()["participants"]
    ada, zeno = roster[0]["id"], roster[1]["id"]

    # Retarget one debater at a different model, leaving the rest untouched.
    resp = client.patch(
        f"/chambers/{cid}/participants/{ada}",
        json={"model": "scripted-large", "stance": "con"},
    )
    assert resp.status_code == 200
    edited = next(p for p in resp.json()["participants"] if p["id"] == ada)
    assert edited["model"] == "scripted-large"
    assert edited["stance"] == "con"
    assert edited["display_name"] == "Ada"  # unsent fields survive
    assert edited["tuning"]["temperature"] == 0.7

    # Tuning replaces the whole block.
    resp = client.patch(
        f"/chambers/{cid}/participants/{ada}", json={"tuning": {"temperature": 0.1}}
    )
    tuning = next(p for p in resp.json()["participants"] if p["id"] == ada)["tuning"]
    assert tuning["temperature"] == 0.1
    # Reset to the schema default, not merged with the previous value.
    assert tuning["max_tokens"] == ParticipantTuning().max_tokens

    # Removal returns the updated roster and is idempotent-safe (404 on repeat).
    resp = client.delete(f"/chambers/{cid}/participants/{zeno}")
    assert resp.status_code == 200
    assert [p["id"] for p in resp.json()["participants"]] == [ada]
    assert client.delete(f"/chambers/{cid}/participants/{zeno}").status_code == 404
    assert client.patch(f"/chambers/{cid}/participants/{zeno}", json={}).status_code == 404

    # A draft may drop below two participants; /run is what enforces the minimum.
    assert client.delete(f"/chambers/{cid}/participants/{ada}").status_code == 200
    assert client.get(f"/chambers/{cid}").json()["participants"] == []
    assert client.post(f"/chambers/{cid}/run").status_code == 409


def test_mute_and_unmute_a_participant(client: TestClient) -> None:
    cid = _create_chamber(client)
    _add_participant(client, cid, "Ada", "pro")
    _add_participant(client, cid, "Zeno", "con")
    _add_participant(client, cid, "Hypatia", "con")
    pid = client.get(f"/chambers/{cid}").json()["participants"][2]["id"]

    muted = client.post(f"/chambers/{cid}/participants/{pid}/mute", json={"muted": True})
    assert muted.status_code == 200
    assert muted.json()["status"] == "muted"
    roster = client.get(f"/chambers/{cid}").json()["participants"]
    assert [p["muted"] for p in roster] == [False, False, True]

    unmuted = client.post(f"/chambers/{cid}/participants/{pid}/mute", json={"muted": False})
    assert unmuted.json()["status"] == "unmuted"
    assert client.get(f"/chambers/{cid}").json()["participants"][2]["muted"] is False


def test_mute_refuses_to_drop_below_two_active_debaters(client: TestClient) -> None:
    cid = _create_chamber(client)
    _add_participant(client, cid, "Ada", "pro")
    _add_participant(client, cid, "Zeno", "con")
    pid = client.get(f"/chambers/{cid}").json()["participants"][0]["id"]

    # Two participants, so muting either would leave a debate of one.
    resp = client.post(f"/chambers/{cid}/participants/{pid}/mute", json={"muted": True})
    assert resp.status_code == 409
    assert "two unmuted" in resp.json()["detail"]
    assert client.get(f"/chambers/{cid}").json()["participants"][0]["muted"] is False


def test_mute_validates_its_target_and_payload(client: TestClient) -> None:
    cid = _create_chamber(client)
    _add_participant(client, cid, "Ada", "pro")
    pid = client.get(f"/chambers/{cid}").json()["participants"][0]["id"]
    missing = "00000000-0000-0000-0000-000000000000"

    no_chamber = client.post(
        f"/chambers/{missing}/participants/{pid}/mute", json={"muted": True}
    )
    assert no_chamber.status_code == 404
    no_participant = client.post(
        f"/chambers/{cid}/participants/{missing}/mute", json={"muted": True}
    )
    assert no_participant.status_code == 404
    assert client.post(f"/chambers/{cid}/participants/{pid}/mute", json={}).status_code == 422


def test_muting_a_concluded_chamber_still_records_the_flag(client: TestClient) -> None:
    # Muting is not gated on `draft` the way roster edits are: an evaluator may
    # want to mark a debater after the fact, and it costs nothing.
    cid = _create_chamber(client)
    for name, stance in (("A", "pro"), ("B", "pro"), ("C", "pro")):
        _add_participant(client, cid, name, stance)
    assert client.post(f"/chambers/{cid}/run", params={"wait": "true"}).status_code == 200
    pid = client.get(f"/chambers/{cid}").json()["participants"][2]["id"]
    resp = client.post(f"/chambers/{cid}/participants/{pid}/mute", json={"muted": True})
    assert resp.status_code == 200


def test_chamber_and_roster_are_frozen_once_the_debate_has_run(client: TestClient) -> None:
    cid = _create_chamber(client)
    _add_participant(client, cid, "Ada", "pro")
    _add_participant(client, cid, "Zeno", "con")
    pid = client.get(f"/chambers/{cid}").json()["participants"][0]["id"]
    assert client.post(f"/chambers/{cid}/run", params={"wait": "true"}).status_code == 200

    assert client.patch(f"/chambers/{cid}", json={"topic": "T"}).status_code == 409
    edit = client.patch(f"/chambers/{cid}/participants/{pid}", json={"stance": "con"})
    assert edit.status_code == 409
    assert client.delete(f"/chambers/{cid}/participants/{pid}").status_code == 409


def test_clone_can_be_edited_before_its_rerun(client: TestClient) -> None:
    cid = _create_chamber(client)
    _add_participant(client, cid, "Ada", "pro")
    _add_participant(client, cid, "Zeno", "con")
    assert client.post(f"/chambers/{cid}/run", params={"wait": "true"}).status_code == 200

    clone = client.post(f"/chambers/{cid}/clone").json()
    clone_id = clone["id"]
    # The clone is a fresh draft, so the whole roster is editable again — change
    # one variable, drop a debater, add a replacement, then rerun.
    assert client.patch(f"/chambers/{clone_id}", json={"category": "rerun"}).status_code == 200
    assert (
        client.patch(
            f"/chambers/{clone_id}/participants/{clone['participants'][0]['id']}",
            json={"model": "scripted-large"},
        ).status_code
        == 200
    )
    assert (
        client.delete(
            f"/chambers/{clone_id}/participants/{clone['participants'][1]['id']}"
        ).status_code
        == 200
    )
    _add_participant(client, clone_id, "Hypatia", "con")

    rerun = client.post(f"/chambers/{clone_id}/run", params={"wait": "true"})
    assert rerun.status_code == 200
    body = rerun.json()
    assert body["category"] == "rerun"
    names = [(p["display_name"], p["model"]) for p in body["participants"]]
    assert names == [("Ada", "scripted-large"), ("Hypatia", "scripted")]
    # The source chamber is untouched by the clone's edits.
    source = client.get(f"/chambers/{cid}").json()
    assert [p["display_name"] for p in source["participants"]] == ["Ada", "Zeno"]
    assert source["category"] == ""


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
    assert as_json.json()["chamber"]["topic"] == "Should we colonise Mars?"

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


def test_step_advances_one_turn_at_a_time(client: TestClient) -> None:
    cid = _create_chamber(client, settings=_STOPS_AFTER_ONE_ROUND)
    _add_participant(client, cid, "Pro-A", "pro")
    _add_participant(client, cid, "Pro-B", "pro")

    first = client.post(f"/chambers/{cid}/step")
    assert first.status_code == 200
    body = first.json()
    # One turn taken; the chamber parks as paused, ready for the next step.
    assert body["status"] == "paused"
    assert len(body["turns"]) == 1
    assert body["consensus"] is None
    assert body["turns"][0]["participant_id"] == body["participants"][0]["id"]

    second = client.post(f"/chambers/{cid}/step")
    assert second.status_code == 200
    body = second.json()
    # The second step closes round 0, where both agree → the debate concludes.
    assert body["status"] == "concluded"
    assert len(body["turns"]) == 2
    assert body["consensus"]["outcome"] == "consensus"
    # Stepping a finished debate is refused.
    assert client.post(f"/chambers/{cid}/step").status_code == 409


def test_step_then_resume_runs_to_conclusion(client: TestClient) -> None:
    cid = _create_chamber(client, settings=_STOPS_AFTER_ONE_ROUND)
    _add_participant(client, cid, "Pro-A", "pro")
    _add_participant(client, cid, "Pro-B", "pro")

    assert client.post(f"/chambers/{cid}/step").status_code == 200
    resumed = client.post(f"/chambers/{cid}/resume", params={"wait": "true"})
    assert resumed.status_code == 200
    body = resumed.json()
    assert body["status"] == "concluded"
    # The stepped turn was kept, not replayed.
    a_id = body["participants"][0]["id"]
    assert len([t for t in body["turns"] if t["participant_id"] == a_id]) == 1


def test_step_requires_two_participants_and_a_real_chamber(client: TestClient) -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.post(f"/chambers/{missing}/step").status_code == 404

    cid = _create_chamber(client)
    _add_participant(client, cid, "Solo", "pro")
    assert client.post(f"/chambers/{cid}/step").status_code == 409


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
        json={"display_name": "C", "provider": "mock", "model": "scripted"},
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


def test_post_chamber_with_measure_compliance_false(client: TestClient) -> None:
    resp = client.post(
        "/chambers",
        json={
            "topic": "T",
            "settings": {
                "measure_compliance": False,
            },
        },
    )
    assert resp.status_code == 201
    created = resp.json()["settings"]
    assert created["measure_compliance"] is False


def test_put_settings_with_measure_compliance_roundtrip(client: TestClient) -> None:
    cid = _create_chamber(client)
    resp = client.put(
        f"/chambers/{cid}/settings",
        json={
            "max_rounds": 2,
            "min_rounds": 2,
            "max_total_tokens": 200_000,
            "max_duration_seconds": None,
            "decision_rule": "judge",
            "convergence_rounds": 2,
            "web_evidence": False,
            "stop_on_repetition": True,
            "repetition_threshold": 0.95,
            "measure_compliance": False,
        },
    )
    assert resp.status_code == 200
    settings = resp.json()["settings"]
    assert settings["measure_compliance"] is False
    assert settings["max_rounds"] == 2


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


def test_resume_requires_paused_chamber(client: TestClient) -> None:
    cid = _create_chamber(client)
    _add_participant(client, cid, "A", "pro")
    _add_participant(client, cid, "B", "pro")
    assert client.post(f"/chambers/{cid}/resume").status_code == 409  # still a draft


def test_resume_continues_an_interrupted_debate() -> None:
    from uuid import UUID

    from cicero.core.state_machine import transition
    from cicero.domain.enums import ChamberStatus
    from cicero.domain.models import Turn

    repo = InMemoryChamberRepository()
    factory = ConstantFactory(ScriptedProvider(stance_word="pro", moderator_reply="We agree."))
    manager = DebateManager()
    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_provider_factory] = lambda: factory
    app.dependency_overrides[get_debate_manager] = lambda: manager
    with TestClient(app) as client:
        cid = _create_chamber(client, settings=_STOPS_AFTER_ONE_ROUND)
        _add_participant(client, cid, "A", "pro")
        _add_participant(client, cid, "B", "pro")

        # Simulate a debate interrupted after A's opening turn, then recovered.
        chamber = repo.get(UUID(cid))
        assert chamber is not None
        transition(chamber, ChamberStatus.RUNNING)
        chamber.turns.append(
            Turn(
                participant_id=chamber.participants[0].id,
                round_index=0,
                content="Opening argument.",
                metadata={"prompt_tokens": 5, "completion_tokens": 5},
            )
        )
        transition(chamber, ChamberStatus.PAUSED)
        repo.update(chamber)

        # /run refuses a paused chamber; /resume picks it up.
        assert client.post(f"/chambers/{cid}/run", params={"wait": "true"}).status_code == 409
        resp = client.post(f"/chambers/{cid}/resume", params={"wait": "true"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "concluded"
        # A's interrupted turn was kept, not repeated.
        a_id = body["participants"][0]["id"]
        a_turns = [t for t in body["turns"] if t["participant_id"] == a_id]
        assert len(a_turns) == 1 and a_turns[0]["content"] == "Opening argument."
        # Resuming a concluded chamber is refused.
        assert client.post(f"/chambers/{cid}/resume").status_code == 409
    app.dependency_overrides.clear()


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


def test_blank_auth_token_leaves_the_api_open() -> None:
    from cicero.config import Settings

    # Regression: `API_AUTH_TOKEN=` (an empty value, which is what compose and
    # .env files inject when nothing was supplied) used to enable auth with a
    # token nobody could send, 401-ing everything except /health.
    app = create_app(Settings(api_auth_token="", _env_file=None))  # type: ignore[call-arg]
    # This test writes, so it must own its storage: without the override it
    # resolves the real repository and persists into whatever DATABASE_URL
    # points at. (The session fixture in conftest is the backstop.)
    app.dependency_overrides[get_repository] = lambda: InMemoryChamberRepository()
    with TestClient(app) as anon:
        assert anon.get("/health").status_code == 200
        assert anon.get("/chambers").status_code == 200
        assert anon.post("/chambers", json={"topic": "Open?"}).status_code == 201
    app.dependency_overrides.clear()


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
    assert body["models"] == ["scripted", "scripted-large"]


def test_list_models_unknown_provider_422(client: TestClient) -> None:
    assert client.get("/providers/nonsense/models").status_code == 422


def test_list_models_provider_error_returns_502() -> None:
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


def test_outcome_endpoint_returns_the_derived_summary() -> None:
    # The shared `client` fixture's ScriptedProvider moderator_reply ("We
    # agree.") carries no HEADLINE directive, so it can never produce the
    # sentence this test pins down. Only the real MockProvider (Task 9) emits
    # that — its offline moderator branch is what this test has to exercise,
    # so it needs its own client wired to that provider, not the shared one.
    repo = InMemoryChamberRepository()
    factory = ConstantFactory(
        MockProvider(models=["scripted", "scripted-large"], poll_answer="pro")
    )
    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_provider_factory] = lambda: factory
    app.dependency_overrides[get_debate_manager] = lambda: DebateManager()
    with TestClient(app) as mock_client:
        cid = _create_chamber(mock_client)
        _add_participant(mock_client, cid, "Pro-A", "pro")
        _add_participant(mock_client, cid, "Pro-B", "pro")
        mock_client.post(f"/chambers/{cid}/run", params={"wait": "true"})

        resp = mock_client.get(f"/chambers/{cid}/outcome")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    # The mock moderator's fixed reply (Task 9) is what makes this deterministic.
    assert body["headline"] == "The chamber reached a deterministic mock outcome."
    assert body["support"] == "unanimous: all 2 debaters on pro"
    assert body["decided_by"] == "all debaters converged"
    assert body["movements"] == []
    # Both mock debaters answer "pro", so no stance label here can be mistaken for
    # a characterisation and the caveat must stay empty — the endpoint serves the
    # field either way.
    assert body["caveat"] == ""


def test_outcome_endpoint_serves_the_compliance_fields(client: TestClient) -> None:
    repo = InMemoryChamberRepository()
    factory = ConstantFactory(
        MockProvider(models=["scripted", "scripted-large"], poll_answer="pro")
    )
    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_provider_factory] = lambda: factory
    app.dependency_overrides[get_debate_manager] = lambda: DebateManager()
    with TestClient(app) as mock_client:
        cid = _create_chamber(mock_client)
        _add_participant(mock_client, cid, "Pro-A", "pro")
        _add_participant(mock_client, cid, "Pro-B", "pro")
        mock_client.post(f"/chambers/{cid}/run", params={"wait": "true"})

        resp = mock_client.get(f"/chambers/{cid}/outcome")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert "compliance_caveat" in body
    assert isinstance(body["noncompliance"], list)


def test_outcome_endpoint_404s_before_the_debate_concludes(client: TestClient) -> None:
    cid = _create_chamber(client)
    _add_participant(client, cid, "Pro-A", "pro")
    _add_participant(client, cid, "Pro-B", "pro")
    resp = client.get(f"/chambers/{cid}/outcome")
    assert resp.status_code == 404


def test_outcome_endpoint_404s_for_an_unknown_chamber(client: TestClient) -> None:
    resp = client.get(f"/chambers/{uuid4()}/outcome")
    assert resp.status_code == 404


def test_configured_moderator_is_used_instead_of_the_first_participant() -> None:
    """The arbiter comes from the chamber's own config, not from roster order."""
    repo = InMemoryChamberRepository()
    seen: list[str] = []

    class _Recording(MockProvider):
        async def generate(self, messages, options):  # type: ignore[no-untyped-def]
            seen.append(options.model)
            return await super().generate(messages, options)

    factory = ConstantFactory(_Recording(models=["scripted", "moderator-model"]))
    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_provider_factory] = lambda: factory
    app.dependency_overrides[get_debate_manager] = lambda: DebateManager()
    with TestClient(app) as client:
        resp = client.post(
            "/chambers",
            json={
                "topic": "Should we colonise Mars?",
                "moderator": {"provider": "mock", "model": "moderator-model"},
            },
        )
        assert resp.status_code == 201
        cid = resp.json()["id"]
        _add_participant(client, cid, "Pro-A", "pro")
        _add_participant(client, cid, "Pro-B", "pro")
        client.post(f"/chambers/{cid}/run", params={"wait": "true"})
    app.dependency_overrides.clear()

    # Debater turns run on "scripted"; only the moderator call uses its own model.
    assert "moderator-model" in seen


def test_participant_tuning_accepts_reasoning_control(client: TestClient) -> None:
    """The setting has to be reachable from the API, which is the whole point of
    issue #28: the flag already existed on GenerateOptions, but nothing outside
    the engine could ask for it."""
    cid = _create_chamber(client)
    resp = client.post(
        f"/chambers/{cid}/participants",
        json={
            "display_name": "Quiet",
            "provider": "mock",
            "model": "scripted",
            "stance": "pro",
            "tuning": {"allow_reasoning": False},
        },
    )
    assert resp.status_code == 201
    tuning = resp.json()["participants"][0]["tuning"]
    assert tuning["allow_reasoning"] is False
    # Untouched fields keep their defaults rather than being reset by the partial
    # tuning block.
    assert tuning["max_tokens"] == ParticipantTuning().max_tokens


def test_participant_tuning_defaults_to_allowing_reasoning(client: TestClient) -> None:
    cid = _create_chamber(client)
    _add_participant(client, cid, "Ada", "pro")
    chamber = client.get(f"/chambers/{cid}").json()
    assert chamber["participants"][0]["tuning"]["allow_reasoning"] is True


def test_reasoning_control_survives_a_participant_edit(client: TestClient) -> None:
    """PATCH replaces the whole tuning block, so a caller that sends tuning
    without this field gets the default back — worth pinning, because silently
    re-enabling reasoning would truncate turns again with nothing to show why."""
    cid = _create_chamber(client)
    _add_participant(client, cid, "Ada", "pro")
    pid = client.get(f"/chambers/{cid}").json()["participants"][0]["id"]
    resp = client.patch(
        f"/chambers/{cid}/participants/{pid}",
        json={"tuning": {"allow_reasoning": False, "max_tokens": 2400}},
    )
    assert resp.status_code == 200
    tuning = resp.json()["participants"][0]["tuning"]
    assert tuning["allow_reasoning"] is False
    assert tuning["max_tokens"] == 2400


def test_clone_keeps_the_configured_moderator() -> None:
    """A clone is the documented rerun path (FR-5), paired with /compare for
    run-vs-run comparison — so it must not change the arbiter behind the run.

    The moderator is also the compliance judge, so dropping it means the clone's
    ``argued`` verdicts come from a different model than the source's; and under
    ``decision_rule: judge`` it breaks ties, so the outcome can move for reasons
    unrelated to whatever the rerun set out to vary.

    The moderator here is deliberately NOT the first participant's model, and its
    temperature is deliberately non-default: the ``moderator is None`` fallback
    builds ``Moderator(provider=..., model=participants[0].model)`` with field
    defaults, so a weaker assertion passes by accident.
    """
    repo = InMemoryChamberRepository()
    factory = ConstantFactory(MockProvider(models=["scripted", "moderator-model"]))
    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_provider_factory] = lambda: factory
    app.dependency_overrides[get_debate_manager] = lambda: DebateManager()
    with TestClient(app) as client:
        resp = client.post(
            "/chambers",
            json={
                "topic": "Should we colonise Mars?",
                "moderator": {
                    "provider": "mock",
                    "model": "moderator-model",
                    "max_tokens": 8192,
                    "temperature": 0.0,
                },
            },
        )
        assert resp.status_code == 201
        cid = resp.json()["id"]
        _add_participant(client, cid, "Pro-A", "pro")
        _add_participant(client, cid, "Con-B", "con")

        clone = client.post(f"/chambers/{cid}/clone").json()
    app.dependency_overrides.clear()

    assert clone["moderator"] is not None, "the clone fell back to participants[0]"
    assert clone["moderator"] == {
        "provider": "mock",
        "model": "moderator-model",
        "max_tokens": 8192,
        "temperature": 0.0,
    }


def test_clone_of_a_chamber_without_a_moderator_still_has_none(client: TestClient) -> None:
    """Pre-F8 chambers, and anyone who omits the field, keep the first-participant
    fallback. ``None`` must stay ``None`` rather than being materialised into a
    concrete moderator at clone time — that would freeze roster order into the
    copy, which is the coupling F8 removed."""
    cid = _create_chamber(client)
    _add_participant(client, cid, "Pro-A", "pro")
    _add_participant(client, cid, "Con-B", "con")
    clone = client.post(f"/chambers/{cid}/clone").json()
    assert clone["moderator"] is None


def test_build_engine_wires_the_moderators_compliance_judge() -> None:
    """``_build_engine`` must construct and pass a ``ComplianceJudge`` that uses
    the moderator's own model — deleting ``judge=judge`` from the wiring in
    ``chambers.py`` leaves every other test in the suite green, so this test
    exists to pin the one path users actually run."""
    repo = InMemoryChamberRepository()
    calls: list[tuple[str, str]] = []

    class _Recording(MockProvider):
        async def generate(self, messages, options):  # type: ignore[no-untyped-def]
            system = messages[0].content if messages else ""
            calls.append((options.model, system))
            return await super().generate(messages, options)

    factory = ConstantFactory(_Recording(models=["scripted", "moderator-model"]))
    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_provider_factory] = lambda: factory
    app.dependency_overrides[get_debate_manager] = lambda: DebateManager()
    with TestClient(app) as client:
        resp = client.post(
            "/chambers",
            json={
                "topic": "Should we colonise Mars?",
                "moderator": {"provider": "mock", "model": "moderator-model"},
            },
        )
        assert resp.status_code == 201
        cid = resp.json()["id"]
        _add_participant(client, cid, "Pro-A", "pro")
        _add_participant(client, cid, "Pro-B", "pro")
        client.post(f"/chambers/{cid}/run", params={"wait": "true"})
    app.dependency_overrides.clear()

    # The compliance judge's system prompt ("You are an impartial reader...") is
    # distinct from the moderator's own ("...impartial MODERATOR..."), so this
    # isolates judge calls specifically, not just any use of the moderator model.
    judge_calls = [
        (model, system) for model, system in calls if "impartial reader" in system.lower()
    ]
    assert judge_calls
    assert all(model == "moderator-model" for model, _ in judge_calls)


def test_moderator_model_is_validated_at_creation(client: TestClient) -> None:
    # Without this a wrong moderator model surfaces only after the whole debate
    # has run and been paid for.
    resp = client.post(
        "/chambers",
        json={"topic": "t", "moderator": {"provider": "mock", "model": "nope"}},
    )
    assert resp.status_code == 422
    assert "nope" in resp.text


def test_moderator_is_editable_while_draft(client: TestClient) -> None:
    cid = _create_chamber(client)
    resp = client.patch(
        f"/chambers/{cid}",
        json={"moderator": {"provider": "mock", "model": "scripted-large"}},
    )
    assert resp.status_code == 200
    body = resp.json()["moderator"]
    assert body["model"] == "scripted-large"
    # Unset tuning falls through to the domain defaults rather than being pinned
    # by the wire schema.
    assert body["max_tokens"] == 4096


def test_moderator_is_frozen_once_the_debate_has_run(client: TestClient) -> None:
    cid = _create_chamber(client)
    _add_participant(client, cid, "Pro-A", "pro")
    _add_participant(client, cid, "Pro-B", "pro")
    client.post(f"/chambers/{cid}/run", params={"wait": "true"})
    resp = client.patch(
        f"/chambers/{cid}", json={"moderator": {"provider": "mock", "model": "scripted"}}
    )
    assert resp.status_code == 409


def test_debate_settings_wire_schema_mirrors_the_domain_model() -> None:
    # DebateSettingsIn is a hand-maintained mirror of DebateSettings, kept
    # separate so the API can impose its own hard caps (NFR-SEC-8). Nothing
    # ties the two field sets together: when the domain model gains a field
    # and the mirror doesn't, model_config = ConfigDict(extra="forbid") makes
    # the wire schema reject a settings payload that includes it, and the
    # whole settings form starts returning 422 — a regression invisible to
    # every test that exercises either model in isolation.
    assert set(DebateSettingsIn.model_fields) == set(DebateSettings.model_fields)


def _concluded_chamber_with_stale_compliance(
    repo: InMemoryChamberRepository, stale: dict[str, object]
) -> Chamber:
    """A concluded one-turn chamber whose turn already carries a judgement."""
    debater = Participant(
        display_name="Ada", provider=ProviderType.MOCK, model="mock-small",
        stance=Stance.PRO,
    )
    chamber = Chamber(
        topic="Should we colonise Mars?",
        status=ChamberStatus.CONCLUDED,
        participants=[debater],
        moderator=Moderator(provider=ProviderType.MOCK, model="mock-small"),
    )
    chamber.turns = [
        Turn(
            participant_id=debater.id,
            round_index=0,
            content="Colonising Mars is worth the cost. I support the motion.",
            metadata=dict(stale),
        )
    ]
    return repo.add(chamber)


def _rejudge_client(repo: InMemoryChamberRepository, provider: MockProvider) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_provider_factory] = lambda: ConstantFactory(provider)
    app.dependency_overrides[get_debate_manager] = lambda: DebateManager()
    return TestClient(app)


def test_rejudge_rewrites_compliance_on_a_concluded_chamber() -> None:
    """The point of the endpoint: a chamber judged by an older protocol is
    brought up to date without re-running the debate."""
    repo = InMemoryChamberRepository()
    chamber = _concluded_chamber_with_stale_compliance(repo, {"argued": "con"})
    with _rejudge_client(repo, MockProvider(compliance_answer="pro")) as client:
        resp = client.post(f"/chambers/{chamber.id}/compliance/rejudge")
    assert resp.status_code == 200
    turn = resp.json()["turns"][0]
    assert turn["metadata"]["argued"] == "pro"


def test_rejudge_clears_a_stale_verdict_it_can_no_longer_read() -> None:
    """Re-judging replaces, it does not merge. A turn whose new reply is
    unreadable must lose its old verdict, or the chamber keeps one that this run
    did not reproduce."""
    repo = InMemoryChamberRepository()
    chamber = _concluded_chamber_with_stale_compliance(repo, {"argued": "con"})
    unreadable = MockProvider(scripted=["I could not say either way."])
    with _rejudge_client(repo, unreadable) as client:
        resp = client.post(f"/chambers/{chamber.id}/compliance/rejudge")
    assert resp.status_code == 200
    assert "argued" not in resp.json()["turns"][0]["metadata"]


def test_rejudge_refuses_a_chamber_that_is_not_concluded() -> None:
    """A running debate is being written by the engine; a concurrent rewrite of
    its turns is how one gets corrupted."""
    repo = InMemoryChamberRepository()
    chamber = _concluded_chamber_with_stale_compliance(repo, {})
    chamber.status = ChamberStatus.RUNNING
    repo.update(chamber)
    with _rejudge_client(repo, MockProvider()) as client:
        resp = client.post(f"/chambers/{chamber.id}/compliance/rejudge")
    assert resp.status_code == 409


def test_settings_schema_mirrors_the_round_floor() -> None:
    # DebateSettingsIn is a hand-maintained mirror (see the model_fields test),
    # so the floor and its give-way rule have to be mirrored too — otherwise a
    # chamber created through the API keeps the old one-round behaviour.
    assert DebateSettingsIn().min_rounds == 3
    assert DebateSettingsIn(max_rounds=2).min_rounds == 2
    with pytest.raises(ValidationError, match="min_rounds cannot exceed max_rounds"):
        DebateSettingsIn(max_rounds=2, min_rounds=3)
