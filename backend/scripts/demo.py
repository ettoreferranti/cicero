#!/usr/bin/env python3
"""End-to-end release demo (J4 — see requirements.md §9).

Drives a **running Cicero API over HTTP** through the whole release path and
checks the release-level acceptance criteria as it goes:

    create a chamber -> add >=2 participants across >=2 providers -> run the
    debate with live streaming -> reach a consensus / disagreement outcome ->
    read metrics -> export JSON + Markdown

Every step prints what it did and records a PASS/FAIL/SKIP row against the
requirement it evidences; the script exits non-zero if anything failed, so it
doubles as a CI smoke test (``tests/test_demo_e2e.py`` runs it end to end).

By default it starts its own API on a free port against a throwaway SQLite
database and picks the roster automatically: any provider that is actually
reachable (a local Ollama, or Anthropic when ``ANTHROPIC_API_KEY`` is set) is
used, otherwise the deterministic offline mock provider stands in.

Usage:
    python scripts/demo.py                       # self-hosted, auto-detected roster
    python scripts/demo.py --compare             # also demo run comparison (FR-32)
    python scripts/demo.py --base-url http://127.0.0.1:8000
    python scripts/demo.py --strict-providers    # fail unless >=2 real providers
    python scripts/demo.py --participant Ada:ollama:llama3:pro \
                           --participant Zeno:anthropic:claude-sonnet-5:con
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

DEFAULT_TOPIC = "Should an AI research lab publish its model weights openly?"
_STANCES = ("pro", "con", "neutral")
_LIVE_PROVIDERS = ("ollama", "anthropic")
_MOCK_MODELS = ("mock-small", "mock-large")
_ROSTER_NAMES = ("Ada", "Zeno")
_ROSTER_STANCES = ("pro", "con")
_FIELD_WIDTH = 58


# --------------------------------------------------------------------------- #
# Result reporting
# --------------------------------------------------------------------------- #


@dataclass
class Report:
    """The acceptance checklist the demo builds up as it runs."""

    rows: list[tuple[str, str, str]] = field(default_factory=list)

    def check(self, requirement: str, label: str, ok: bool, detail: str = "") -> bool:
        """Record a PASS/FAIL row and return ``ok`` so callers can branch."""
        self._add("PASS" if ok else "FAIL", requirement, label, detail)
        return ok

    def skip(self, requirement: str, label: str, detail: str) -> None:
        """Record a criterion this run deliberately did not exercise."""
        self._add("SKIP", requirement, label, detail)

    def _add(self, status: str, requirement: str, label: str, detail: str) -> None:
        self.rows.append((status, f"{requirement} — {label}", detail))
        suffix = f": {detail}" if detail else ""
        print(f"  [{status}] {requirement} — {label}{suffix}")

    @property
    def failures(self) -> list[tuple[str, str, str]]:
        return [row for row in self.rows if row[0] == "FAIL"]

    def render(self) -> str:
        lines = ["", "=" * 78, "Release acceptance checklist (requirements.md §9)", "=" * 78]
        for status, label, detail in self.rows:
            suffix = f"  ({detail})" if detail else ""
            lines.append(f"{status:<5} {label[:_FIELD_WIDTH]:<{_FIELD_WIDTH}}{suffix}")
        passed = sum(1 for row in self.rows if row[0] == "PASS")
        skipped = sum(1 for row in self.rows if row[0] == "SKIP")
        lines.append("-" * 78)
        lines.append(
            f"{passed} passed, {len(self.failures)} failed, {skipped} skipped"
            f" — {'DEMO PASSED' if not self.failures else 'DEMO FAILED'}"
        )
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Roster
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ParticipantSpec:
    """One debater, as given on the command line or auto-detected."""

    display_name: str
    provider: str
    model: str
    stance: str

    @classmethod
    def parse(cls, raw: str) -> ParticipantSpec:
        # Split the ends off, not on every colon: Ollama model names carry a tag
        # ("mistral:latest", "llama3.1:8b"), so the model is whatever is left in
        # the middle. A plain 4-way split cannot express those at all.
        head, _, rest = raw.partition(":")
        provider, _, rest = rest.partition(":")
        model, _, stance = rest.rpartition(":")
        name, provider, model, stance = (
            head.strip(),
            provider.strip(),
            model.strip(),
            stance.strip(),
        )
        if not all((name, provider, model, stance)):
            raise argparse.ArgumentTypeError(
                f"expected NAME:PROVIDER:MODEL:STANCE, got {raw!r}"
            )
        if stance not in _STANCES:
            raise argparse.ArgumentTypeError(
                f"stance must be one of {', '.join(_STANCES)}, got {stance!r}"
            )
        return cls(name, provider, model, stance)

    def payload(self) -> dict[str, str]:
        return {
            "display_name": self.display_name,
            "provider": self.provider,
            "model": self.model,
            "stance": self.stance,
        }


def detect_roster(client: httpx.Client) -> list[ParticipantSpec]:
    """Build a two-debater roster, preferring providers that answer right now."""
    available: list[tuple[str, str]] = []
    for provider in _LIVE_PROVIDERS:
        try:
            response = client.get(f"/providers/{provider}/models", timeout=10.0)
        except httpx.HTTPError:
            continue
        if response.status_code != 200:
            continue
        models = response.json().get("models") or []
        if models:
            available.append((provider, models[0]))
            print(f"  detected live provider: {provider} ({models[0]})")

    while len(available) < len(_ROSTER_NAMES):
        available.append(("mock", _MOCK_MODELS[len(available) % len(_MOCK_MODELS)]))

    return [
        ParticipantSpec(name, provider, model, stance)
        for name, stance, (provider, model) in zip(
            _ROSTER_NAMES, _ROSTER_STANCES, available, strict=False
        )
    ]


# --------------------------------------------------------------------------- #
# Self-hosted server
# --------------------------------------------------------------------------- #


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_health(base_url: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{base_url}/health", timeout=2.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise SystemExit(f"the API never became healthy at {base_url}")


@contextmanager
def serve() -> Iterator[str]:
    """Run the API in a background thread against a throwaway SQLite database."""
    import uvicorn  # imported lazily: only needed when the demo hosts the API

    with tempfile.TemporaryDirectory(prefix="cicero-demo-") as tmp:
        os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp) / 'demo.db'}"
        from cicero.api.app import create_app  # imported after DATABASE_URL is set

        port = _free_port()
        config = uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{port}"
        print(f"Started a demo API at {base_url} (throwaway database)")
        try:
            _wait_for_health(base_url)
            yield base_url
        finally:
            server.should_exit = True
            thread.join(timeout=10)


# --------------------------------------------------------------------------- #
# Debate steps
# --------------------------------------------------------------------------- #


def _participant_names(chamber: dict[str, Any]) -> dict[str, str]:
    """Map participant id -> display name, so streamed turns read like a chat."""
    return {
        str(participant["id"]): str(participant["display_name"])
        for participant in chamber.get("participants", [])
    }


def _create_chamber(client: httpx.Client, args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    settings: dict[str, Any] = {
        "max_rounds": args.max_rounds,
        "min_rounds": args.min_rounds,
        "decision_rule": args.decision_rule,
        "convergence_rounds": args.convergence_rounds,
        "web_evidence": args.web_evidence,
    }
    if args.max_duration is not None:
        settings["max_duration_seconds"] = args.max_duration
    response = client.post(
        "/chambers",
        json={
            "topic": args.topic,
            "category": "demo",
            "description": "Cicero end-to-end release demo.",
            "settings": settings,
        },
    )
    response.raise_for_status()
    body: dict[str, Any] = response.json()
    return str(body["id"]), body


def _stream_debate(
    client: httpx.Client, chamber_id: str, timeout: float, names: dict[str, str]
) -> list[dict[str, Any]]:
    """Consume the SSE stream until ``done``, printing turns as they arrive.

    The manager replays the events emitted before we connected, so starting the
    run first and subscribing afterwards loses nothing.
    """
    events: list[dict[str, Any]] = []
    stream_timeout = httpx.Timeout(timeout, connect=10.0)
    with client.stream(
        "GET", f"/chambers/{chamber_id}/events", timeout=stream_timeout
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            event: dict[str, Any] = json.loads(line[len("data: ") :])
            events.append(event)
            _print_event(event, names)
            if event.get("type") == "done":
                break
    return events


def _speaker(turn: dict[str, Any], names: dict[str, str]) -> str:
    participant_id = turn.get("participant_id")
    if participant_id is None:
        kind = (turn.get("metadata") or {}).get("kind", "system")
        return f"<{kind}>"
    return names.get(str(participant_id), str(participant_id))


def _print_event(event: dict[str, Any], names: dict[str, str]) -> None:
    kind = event.get("type")
    payload = event.get("payload") or {}
    if kind == "turn":
        turn = payload.get("turn") or {}
        content = " ".join(str(turn.get("content", "")).split())
        citations = turn.get("citations") or []
        suffix = f"  [{len(citations)} citation(s)]" if citations else ""
        round_no = int(turn.get("round_index", 0)) + 1
        print(f"    round {round_no} | {_speaker(turn, names)}: {content[:140]}{suffix}")
    elif kind == "status":
        print(f"    -- status: {payload.get('status')}")
    elif kind == "consensus":
        consensus = payload.get("consensus") or {}
        print(
            f"    -- outcome: {consensus.get('outcome')} "
            f"(winner: {consensus.get('winning_stance')})"
        )
    elif kind == "error":
        print(f"    -- error: {payload.get('message')}")


def _step_debate(client: httpx.Client, chamber_id: str, report: Report) -> int:
    """Take a single turn under manual control, then hand back to the run loop."""
    stepped = client.post(f"/chambers/{chamber_id}/step")
    body: dict[str, Any] = stepped.json() if stepped.status_code == 200 else {}
    turns: list[dict[str, Any]] = body.get("turns", [])
    spoken = [turn for turn in turns if turn.get("participant_id")]
    report.check(
        "FR-19",
        "step control ran one turn and paused the debate",
        stepped.status_code == 200 and len(spoken) == 1 and body.get("status") == "paused",
        f"HTTP {stepped.status_code}, status={body.get('status')}, turns={len(spoken)}",
    )
    return len(spoken)


def _run_debate(
    client: httpx.Client,
    chamber_id: str,
    report: Report,
    timeout: float,
    names: dict[str, str],
    paused: bool = False,
) -> list[dict[str, Any]]:
    # A stepped debate is already past `draft`, so it continues via /resume.
    action = "resume" if paused else "run"
    started = client.post(f"/chambers/{chamber_id}/{action}")
    report.check(
        "FR-19",
        f"debate {'resumes' if paused else 'starts'} asynchronously",
        started.status_code == 202,
        f"HTTP {started.status_code}",
    )
    if started.status_code != 202:
        return []
    print("  streaming the debate live:")
    return _stream_debate(client, chamber_id, timeout, names)


def _export(client: httpx.Client, chamber_id: str, out_dir: Path, report: Report) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    as_json = client.get(f"/chambers/{chamber_id}/export", params={"format": "json"})
    json_path = out_dir / f"chamber-{chamber_id}.json"
    if as_json.status_code == 200:
        json_path.write_text(json.dumps(as_json.json(), indent=2), encoding="utf-8")
    report.check(
        "FR-31",
        "JSON export written",
        as_json.status_code == 200 and json_path.exists(),
        str(json_path),
    )

    as_markdown = client.get(f"/chambers/{chamber_id}/export", params={"format": "markdown"})
    md_path = out_dir / f"chamber-{chamber_id}.md"
    if as_markdown.status_code == 200:
        md_path.write_text(as_markdown.text, encoding="utf-8")
    report.check(
        "FR-31",
        "Markdown export written",
        as_markdown.status_code == 200 and "## Transcript" in as_markdown.text,
        str(md_path),
    )


def _compare_runs(
    client: httpx.Client, chamber_id: str, report: Report, timeout: float
) -> None:
    """Clone the chamber, rerun it, and compare the two runs (H4 / FR-32)."""
    print("\n[8] Cloning the chamber and rerunning it for a run-vs-run comparison")
    clone = client.post(f"/chambers/{chamber_id}/clone")
    if not report.check(
        "FR-32", "chamber cloned for a second run", clone.status_code == 201
    ):
        return
    clone_body = clone.json()
    clone_id = str(clone_body["id"])

    # A clone is a fresh draft, so the rerun can be retargeted first (FR-3) —
    # this is what makes the comparison a controlled one.
    edited = client.patch(f"/chambers/{clone_id}", json={"category": "rerun"})
    roster = clone_body["participants"]
    retuned = client.patch(
        f"/chambers/{clone_id}/participants/{roster[0]['id']}",
        json={"tuning": {"temperature": 0.2}},
    )
    clone_body = retuned.json() if retuned.status_code == 200 else clone_body
    report.check(
        "FR-3",
        "cloned draft edited before its rerun",
        edited.status_code == 200
        and edited.json().get("category") == "rerun"
        and retuned.status_code == 200,
        f"HTTP {edited.status_code}/{retuned.status_code}",
    )

    _run_debate(client, clone_id, report, timeout, _participant_names(clone_body))

    comparison = client.get(f"/chambers/{chamber_id}/compare/{clone_id}")
    body = comparison.json() if comparison.status_code == 200 else {}
    report.check(
        "FR-32",
        "two runs compared side by side",
        comparison.status_code == 200 and bool(body.get("same_topic")),
        f"{body.get('a', {}).get('outcome')} vs {body.get('b', {}).get('outcome')}",
    )


# --------------------------------------------------------------------------- #
# The demo itself
# --------------------------------------------------------------------------- #


def _decide(stances: dict[str, str], rule: str) -> tuple[str, str | None]:
    """Independent re-implementation of the decision rule (see FR-22).

    Deliberately *not* imported from ``cicero.core.consensus``: an acceptance
    check that calls the code under test can only ever agree with it. Written
    from the requirement instead, so the two disagreeing is a real signal.
    """
    values = list(stances.values())
    if values and len(set(values)) == 1:
        return "consensus", values[0]
    if rule == "unanimous":
        return "disagreement", None
    ranked = Counter(values).most_common()
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return ("verdict", None) if rule == "judge" else ("disagreement", None)
    return "majority", ranked[0][0]


_JUDGE_WORDS = {
    "pro": "pro",
    "for": "pro",
    "yes": "pro",
    "support": "pro",
    "con": "con",
    "against": "con",
    "no": "con",
    "oppose": "con",
    "opposed": "con",
    "neutral": "neutral",
    "undecided": "neutral",
    "unsure": "neutral",
}


def _read_one_word(reply: str) -> str | None:
    """Minimal local parser, kept independent of cicero.core.consensus.

    An acceptance check that calls the parser under test cannot disagree with
    it, and disagreement is the entire point of this check.
    """
    words = [word for word in re.findall(r"[a-z]+", reply.lower()) if word in _JUDGE_WORDS]
    return _JUDGE_WORDS[words[0]] if words else None


def _judge_stances(
    body: dict[str, Any], roster: list[ParticipantSpec], report: Report
) -> None:
    """Ask an impartial model what each debater ended up arguing (opt-in).

    Every other check verifies the record is *self-consistent*. This is the only
    one that asks whether it is *true*: a tally can agree with itself perfectly
    while describing a debate that did not happen. It is non-deterministic —
    models disagree about genuinely ambiguous positions — so it fails only on
    systematic divergence (a strict majority of debaters), and never runs unless
    asked for.
    """
    try:
        from cicero.config import get_settings
        from cicero.core.export import to_markdown
        from cicero.domain.enums import ProviderType
        from cicero.domain.models import Chamber
        from cicero.providers import GenerateOptions, Message, ProviderError, Role
        from cicero.providers.factory import SettingsProviderFactory
    except ImportError as exc:  # pragma: no cover - only when run outside the repo
        report.skip("§9", "stance record judged by an impartial model", f"import failed: {exc}")
        return

    chamber = Chamber.model_validate(body)
    transcript = to_markdown(chamber)
    judge_spec = roster[0]
    factory = SettingsProviderFactory(get_settings())
    recorded = {
        str(pid): str(value)
        for pid, value in ((body.get("consensus") or {}).get("final_stances") or {}).items()
    }

    provider = factory.get_for_type(ProviderType(judge_spec.provider))

    async def ask(name: str) -> str | None:
        messages = [
            Message(
                role=Role.SYSTEM,
                content=(
                    "You are an impartial reader of a debate transcript. You take "
                    "no side; you only report what a named debater ended up arguing."
                ),
            ),
            Message(
                role=Role.USER,
                content=(
                    f"{transcript}\n\nMotion: {chamber.topic}\n\n"
                    f"Reading only the transcript above, what position did "
                    f"{name} hold by the end? Reply with exactly one word - pro, "
                    f"con, or neutral - where pro means they supported the "
                    f"motion and con means they opposed it. Reply with only that word."
                ),
            ),
        ]
        options = GenerateOptions(
            model=judge_spec.model,
            max_tokens=512,
            temperature=0.0,
            allow_reasoning=False,
        )
        try:
            result = await provider.generate(messages, options)
        except ProviderError:
            return None
        return _read_one_word(result.content)

    async def judge_everyone() -> list[str | None]:
        # One event loop for the whole pass: the provider holds a shared HTTP
        # client, and a fresh asyncio.run() per call closes the loop underneath
        # it ("Event loop is closed" on the second participant).
        try:
            return [await ask(p.display_name) for p in chamber.participants]
        finally:
            aclose = getattr(provider, "aclose", None)
            if aclose is not None:
                await aclose()

    verdicts = asyncio.run(judge_everyone())

    disagreements: list[str] = []
    unreadable = 0
    for participant, verdict in zip(chamber.participants, verdicts, strict=True):
        mine = recorded.get(str(participant.id))
        if verdict is None:
            unreadable += 1
            continue
        if mine is not None and verdict != mine:
            disagreements.append(f"{participant.display_name}: said {mine}, reads as {verdict}")

    if unreadable == len(chamber.participants):
        report.skip("§9", "stance record judged by an impartial model", "judge unavailable")
        return

    total = len(chamber.participants) - unreadable
    report.check(
        "§9",
        "stance record matches what an impartial reader sees",
        len(disagreements) <= total // 2,
        "; ".join(disagreements) or f"agreed on all {total}",
    )


def _check_stances_were_actually_measured(body: dict[str, Any], report: Report) -> None:
    """Guard the class of bug where the tally is not evidence at all.

    Every check above this asks "was something recorded". These ask whether the
    recorded thing means anything — which is what was missing when a substring
    parser silently inverted concessions, and again when a reasoning model
    answered every poll with empty content.
    """
    history: list[dict[str, Any]] = body.get("stance_history") or []
    consensus: dict[str, Any] = body.get("consensus") or {}

    # 1. A poll nobody could read is a defect, always. This is the direct guard
    #    on the reasoning-model bug: qwen3 spent its whole token budget thinking
    #    and returned empty content, three rounds running, and nothing failed.
    unreadable = sum(len(entry.get("unparsed") or []) for entry in history)
    report.check(
        "FR-25",
        "every stance poll was readable",
        unreadable == 0,
        f"{unreadable} unreadable poll(s) across {len(history)} round(s)",
    )

    # 2. Debaters who *start* opposed cannot unanimously agree without at least
    #    one of them leaving their assigned position. If the record shows
    #    everyone still sitting on their starting role, it is echoing the roster
    #    rather than measuring anyone — the signature of the parser bug, where
    #    an unreadable reply was silently replaced by the declared stance.
    #    Narrow (it can only fire on a consensus outcome) but sound: for a
    #    unanimous end from a divided start, movement is a tautology.
    declared = {
        str(p.get("id")): str(p.get("stance")) for p in body.get("participants") or []
    }
    final_stances = {
        pid: str(value) for pid, value in (consensus.get("final_stances") or {}).items()
    }
    outcome = consensus.get("outcome")
    if outcome == "consensus" and len(set(declared.values())) > 1:
        movers = [pid for pid, stance in final_stances.items() if declared.get(pid) != stance]
        report.check(
            "FR-25",
            "unanimous agreement from opposed starts shows movement",
            bool(movers),
            f"started {sorted(set(declared.values()))}, "
            f"ended {sorted(set(final_stances.values()))}, {len(movers)} moved",
        )
    else:
        report.skip(
            "FR-25",
            "movement implied by unanimity",
            f"only checkable on a consensus from a divided start (was {outcome!r})",
        )

    # 3. The published outcome must follow from the published stances. Would not
    #    have caught either bug above — both produced an outcome faithful to the
    #    (wrong) stances — but it is what stops the tally and the artifact
    #    drifting apart, which is how the contradiction became visible.
    rule = str(((body.get("settings") or {}).get("decision_rule")) or "judge")
    muted = any(p.get("muted") for p in body.get("participants") or [])
    if final_stances and not muted and unreadable == 0:
        expected_outcome, expected_winner = _decide(final_stances, rule)
        actual_winner = consensus.get("winning_stance")
        agrees = expected_outcome == outcome and (
            expected_winner is None or expected_winner == actual_winner
        )
        report.check(
            "FR-22/23",
            "outcome follows from the recorded stances",
            agrees,
            f"rule={rule}: expected {expected_outcome}/{expected_winner}, "
            f"got {outcome}/{actual_winner}",
        )
    else:
        report.skip(
            "FR-22/23",
            "outcome recomputed from stances",
            "needs an unmuted, fully-readable poll",
        )


def run_demo(
    client: httpx.Client, roster: list[ParticipantSpec], args: argparse.Namespace
) -> Report:
    report = Report()

    print("\n[1] Checking the API is up and hardened")
    health = client.get("/health")
    report.check("NFR-O-1", "API is healthy", health.status_code == 200, health.text.strip())
    listing = client.get("/chambers")
    report.check(
        "NFR-SEC-9",
        "security headers on API responses",
        "Content-Security-Policy" in listing.headers
        and listing.headers.get("X-Content-Type-Options") == "nosniff",
    )
    capabilities = client.get("/config")
    web_enabled = bool(capabilities.json().get("web_access_enabled")) if (
        capabilities.status_code == 200
    ) else False
    print(f"  web evidence available on this server: {web_enabled}")

    print("\n[2] Creating a chamber")
    chamber_id, chamber = _create_chamber(client, args)
    report.check("FR-1/2", "chamber created and persisted", bool(chamber_id), args.topic)
    report.check(
        "FR-16",
        "per-chamber debate settings applied",
        chamber["settings"]["max_rounds"] == args.max_rounds
        and chamber["settings"]["decision_rule"] == args.decision_rule,
        f"max_rounds={args.max_rounds}, rule={args.decision_rule}",
    )

    print("\n[3] Adding participants")
    names: dict[str, str] = {}
    for spec in roster:
        added = client.post(f"/chambers/{chamber_id}/participants", json=spec.payload())
        added.raise_for_status()
        names = _participant_names(added.json())
        print(f"  {spec.display_name}: {spec.provider}/{spec.model} arguing {spec.stance}")
    # A model the provider cannot serve is caught here, not mid-debate (FR-12).
    rejected = client.post(
        f"/chambers/{chamber_id}/participants",
        json={
            "display_name": "Ghost",
            "provider": roster[0].provider,
            "model": "definitely-not-a-real-model",
            "stance": "neutral",
        },
    )
    report.check(
        "FR-12",
        "unavailable model rejected at add time",
        rejected.status_code == 422,
        f"HTTP {rejected.status_code}",
    )

    # Muting is refused when it would leave fewer than two active debaters —
    # the guard that keeps a "debate" from becoming a monologue (FR-13).
    roster_now = client.get(f"/chambers/{chamber_id}").json()["participants"]
    if len(roster_now) == 2:
        refused = client.post(
            f"/chambers/{chamber_id}/participants/{roster_now[0]['id']}/mute",
            json={"muted": True},
        )
        report.check(
            "FR-13",
            "muting refused when it would leave one debater",
            refused.status_code == 409,
            f"HTTP {refused.status_code}",
        )
    else:
        report.skip("FR-13", "mute guard", "needs a two-debater roster")

    providers_used = sorted({spec.provider for spec in roster})
    report.check(
        "FR-6/7",
        "participants added with stances",
        len(roster) >= 2,
        ", ".join(f"{s.display_name}({s.stance})" for s in roster),
    )
    if len(providers_used) >= 2:
        report.check("§9", "debate spans >=2 providers", True, ", ".join(providers_used))
    elif args.strict_providers:
        report.check("§9", "debate spans >=2 providers", False, ", ".join(providers_used))
    else:
        report.skip(
            "§9",
            "debate spans >=2 providers",
            f"only {providers_used[0]} available — rerun with a live provider "
            "or --strict-providers for release sign-off",
        )

    print("\n[4] Injecting a moderator note before the debate starts")
    note = client.post(
        f"/chambers/{chamber_id}/notes",
        json={"content": "Moderator: keep arguments concrete and cite evidence where you can."},
    )
    report.check(
        "FR-21", "moderator note accepted", note.status_code in (200, 202), note.text.strip()
    )

    print("\n[5] Stepping the debate one turn by hand")
    stepped = _step_debate(client, chamber_id, report)

    print("\n[6] Running the rest of the debate")
    events = _run_debate(client, chamber_id, report, args.timeout, names, paused=stepped > 0)
    turn_events = [event for event in events if event.get("type") == "turn"]
    report.check(
        "FR-18", "turns streamed live over SSE", len(turn_events) > 0, f"{len(turn_events)} turns"
    )
    report.check(
        "FR-14/15",
        "debate ran as a turn-based group chat",
        stepped + len(turn_events) >= len(roster),
        f"{stepped + len(turn_events)} turns across {len(roster)} debaters",
    )

    print("\n[7] Reading back the concluded chamber")
    final = client.get(f"/chambers/{chamber_id}")
    final.raise_for_status()
    body: dict[str, Any] = final.json()
    consensus = body.get("consensus") or {}
    report.check(
        "FR-4", "chamber reached a terminal state", body.get("status") == "concluded",
        str(body.get("status")),
    )
    turns: list[dict[str, Any]] = body.get("turns", [])
    report.check(
        "FR-17",
        "every turn persisted with metadata",
        bool(turns) and all(turn.get("metadata") is not None for turn in turns),
        f"{len(turns)} turns stored",
    )
    engine_errors = [event for event in events if event.get("type") == "error"]
    turn_errors = [turn for turn in turns if "error" in (turn.get("metadata") or {})]
    report.check(
        "NFR-R-1",
        "no provider or engine errors during the debate",
        not engine_errors and not turn_errors,
        f"{len(engine_errors)} engine, {len(turn_errors)} turn",
    )
    outcome = consensus.get("outcome")
    report.check(
        "FR-22",
        "debate produced an outcome",
        outcome in {"consensus", "majority", "verdict", "disagreement"},
        str(outcome),
    )
    statement = str(consensus.get("statement") or "")
    report.check(
        "FR-23/24",
        "outcome carries a statement",
        bool(statement.strip()),
        f"{len(statement)} chars",
    )
    headline = str(consensus.get("headline") or "")
    report.check(
        "FR-23",
        "outcome carries a one-sentence headline",
        bool(headline.strip()),
        f"{headline[:60]!r}" if headline else "missing",
    )
    if outcome == "disagreement":
        report.check(
            "FR-24",
            "disagreement summary has no winner",
            consensus.get("winning_stance") is None,
        )
    else:
        report.check(
            "FR-23",
            "a winning stance was recorded",
            consensus.get("winning_stance") in {"pro", "con", "neutral"},
            str(consensus.get("winning_stance")),
        )
    report.check(
        "FR-25",
        "final stances recorded per participant",
        len(consensus.get("final_stances") or {}) == len(roster),
    )
    history: list[dict[str, Any]] = body.get("stance_history") or []
    rounds_polled = [entry.get("round_index") for entry in history]
    # Movement is measured against the *assigned* stance, not against the first
    # poll: with min_rounds > 1 the first poll already reflects a debater who
    # has crossed the floor, and comparing polls to each other reports that as
    # "nobody moved".
    starting = {str(p.get("id")): str(p.get("stance")) for p in body.get("participants") or []}
    movers = [
        pid
        for pid, assigned in starting.items()
        if any((entry.get("stances") or {}).get(pid, assigned) != assigned for entry in history)
    ]
    report.check(
        "FR-25",
        "stance history recorded per round",
        bool(history) and rounds_polled == sorted(set(rounds_polled)),
        f"{len(history)} poll(s) across rounds {rounds_polled}, {len(movers)} debater(s) moved",
    )
    _check_stances_were_actually_measured(body, report)
    if args.judge_stances:
        _judge_stances(body, roster, report)
    else:
        report.skip(
            "§9",
            "stance record judged by an impartial model",
            "pass --judge-stances (non-deterministic, so never gates CI)",
        )

    stop_reason = (body.get("config") or {}).get("stop_reason")
    report.check("FR-16", "a stop condition ended the debate", bool(stop_reason), str(stop_reason))

    citations = sum(len(turn.get("citations") or []) for turn in turns)
    if args.web_evidence and web_enabled:
        report.check("FR-28", "web sources cited in the transcript", citations > 0, f"{citations}")
    else:
        report.skip("FR-26/28", "web evidence cited", "web evidence off for this run")

    metrics = client.get(f"/chambers/{chamber_id}/metrics")
    rows = metrics.json() if metrics.status_code == 200 else []
    report.check(
        "FR-33",
        "per-participant metrics available",
        metrics.status_code == 200 and len(rows) == len(roster),
        ", ".join(
            f"{row['display_name']}={row['prompt_tokens'] + row['completion_tokens']}tok"
            for row in rows
        ),
    )

    _export(client, chamber_id, Path(args.out), report)

    if args.compare:
        _compare_runs(client, chamber_id, report, args.timeout)
    else:
        report.skip("FR-32", "run-vs-run comparison", "pass --compare to demo it")

    print(f"\nFinal statement:\n  {' '.join(statement.split())[:600]}")
    return report


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cicero end-to-end release demo (J4).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Use an already-running API instead of starting one (e.g. http://127.0.0.1:8000).",
    )
    parser.add_argument("--token", default=None, help="Bearer token, if the API requires one.")
    parser.add_argument("--topic", default=DEFAULT_TOPIC, help="The topic to debate.")
    parser.add_argument(
        "--participant",
        dest="participants",
        action="append",
        type=ParticipantSpec.parse,
        metavar="NAME:PROVIDER:MODEL:STANCE",
        help="Add a debater (repeatable). Omit to auto-detect available providers.",
    )
    parser.add_argument("--max-rounds", type=int, default=3, help="Round budget (default: 3).")
    parser.add_argument("--min-rounds", type=int, default=1, help="Minimum rounds (default: 1).")
    parser.add_argument(
        "--convergence-rounds",
        type=int,
        default=1,
        help="Closing rounds steered toward common ground (default: 1).",
    )
    parser.add_argument(
        "--decision-rule",
        default="judge",
        choices=("unanimous", "majority", "judge"),
        help="How the debate resolves (default: judge).",
    )
    parser.add_argument(
        "--max-duration", type=float, default=None, help="Wall-clock cap, in seconds."
    )
    parser.add_argument(
        "--web-evidence", action="store_true", help="Opt this chamber into web evidence."
    )
    parser.add_argument(
        "--compare", action="store_true", help="Also clone + rerun the debate and compare (FR-32)."
    )
    parser.add_argument(
        "--strict-providers",
        action="store_true",
        help="Fail unless the debate spans >=2 distinct providers (release sign-off).",
    )
    parser.add_argument(
        "--judge-stances",
        action="store_true",
        help=(
            "Have an impartial model re-read the transcript and independently "
            "report each debater's final position, then compare that with what "
            "the debaters self-reported. Catches a stance record that is "
            "internally consistent but unfaithful to the argument. Off by "
            "default: it is non-deterministic, so it must never gate CI."
        ),
    )
    parser.add_argument(
        "--out", default="demo-output", help="Directory for exports (default: demo-output)."
    )
    parser.add_argument(
        "--timeout", type=float, default=600.0, help="Debate stream timeout, in seconds."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}

    @contextmanager
    def _api() -> Iterator[str]:
        if args.base_url:
            yield args.base_url.rstrip("/")
        else:
            with serve() as base_url:
                yield base_url

    with _api() as base_url, httpx.Client(
        base_url=base_url, headers=headers, timeout=60.0
    ) as client:
        print(f"Cicero end-to-end demo against {base_url}")
        roster = args.participants or detect_roster(client)
        if len(roster) < 2:
            raise SystemExit("a debate needs at least two participants")
        report = run_demo(client, roster, args)

    print(report.render())
    return 1 if report.failures else 0


if __name__ == "__main__":
    sys.exit(main())
