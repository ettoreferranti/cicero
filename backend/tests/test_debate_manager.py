"""Tests for the background debate manager and its event stream."""

from __future__ import annotations

import asyncio

from cicero.api.debate_manager import DebateManager
from cicero.api.events import DebateEventType
from cicero.core.budget import DebateBudget
from cicero.core.consensus import ConsensusEngine
from cicero.core.orchestrator import DebateEngine, NoteSource, TurnListener
from cicero.domain.enums import ProviderType, Stance
from cicero.persistence.memory import InMemoryChamberRepository
from cicero.providers.base import GenerateOptions, GenerateResult, Message, Provider
from tests.conftest import ScriptedProvider, StubFactory, make_chamber, make_participant

MOD_OPTS = GenerateOptions(model="mod")
BUDGET = DebateBudget(max_rounds=5, max_total_tokens=100_000)


class BlockingProvider(Provider):
    provider_type = ProviderType.MOCK

    async def generate(self, messages: list[Message], options: GenerateOptions) -> GenerateResult:
        await asyncio.sleep(30)  # long enough to be cancelled by stop()
        return GenerateResult(content="never")

    async def list_models(self) -> list[str]:
        return []


def _build(factory: StubFactory, repo: InMemoryChamberRepository, moderator_reply: str = "AGREED"):  # type: ignore[no-untyped-def]
    def build(
        listener: TurnListener | None, notes: NoteSource | None
    ) -> tuple[DebateEngine, DebateBudget]:
        moderator = ScriptedProvider(moderator_reply=moderator_reply)
        consensus = ConsensusEngine(factory, moderator, MOD_OPTS)
        engine = DebateEngine(factory, repo, consensus, listener=listener, notes=notes)
        return engine, BUDGET

    return build


async def test_stream_emits_turns_consensus_and_done() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.PRO)
    chamber = make_chamber(a, b)
    repo = InMemoryChamberRepository()
    factory = StubFactory(
        {a.id: ScriptedProvider(stance_word="pro"), b.id: ScriptedProvider(stance_word="pro")}
    )
    manager = DebateManager()
    manager.start(chamber, _build(factory, repo))

    events = [e async for e in manager.subscribe(chamber.id)]
    types = [e.type for e in events]

    assert types[0] is DebateEventType.STATUS  # first: running
    assert DebateEventType.TURN in types
    assert DebateEventType.CONSENSUS in types
    assert types[-1] is DebateEventType.DONE
    # Turn events carry the serialised turn.
    turn_events = [e for e in events if e.type is DebateEventType.TURN]
    assert turn_events and "turn" in turn_events[0].payload


async def test_late_subscriber_replays_history() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.PRO)
    chamber = make_chamber(a, b)
    repo = InMemoryChamberRepository()
    factory = StubFactory(
        {a.id: ScriptedProvider(stance_word="pro"), b.id: ScriptedProvider(stance_word="pro")}
    )
    manager = DebateManager()
    manager.start(chamber, _build(factory, repo))
    # Let the debate finish before subscribing.
    await asyncio.sleep(0.05)
    assert not manager.is_running(chamber.id)

    events = [e async for e in manager.subscribe(chamber.id)]
    assert events  # replayed from history
    assert events[-1].type is DebateEventType.DONE


async def test_stop_cancels_running_debate() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.PRO)
    chamber = make_chamber(a, b)
    repo = InMemoryChamberRepository()
    # Turns block; consensus/stance polls never reached.
    factory = StubFactory({a.id: BlockingProvider(), b.id: BlockingProvider()})
    manager = DebateManager()
    manager.start(chamber, _build(factory, repo))

    await asyncio.sleep(0)  # let the task reach the blocking generate
    assert manager.is_running(chamber.id)

    assert await manager.stop(chamber.id) is True
    assert manager.is_running(chamber.id) is False

    events = [e async for e in manager.subscribe(chamber.id)]
    statuses = [e.payload.get("status") for e in events if e.type is DebateEventType.STATUS]
    assert "stopped" in statuses


async def test_stop_returns_false_when_nothing_running() -> None:
    manager = DebateManager()
    a = make_participant("A", Stance.PRO)
    chamber = make_chamber(a)
    assert await manager.stop(chamber.id) is False


async def test_subscribe_unknown_chamber_yields_nothing() -> None:
    manager = DebateManager()
    a = make_participant("A", Stance.PRO)
    chamber = make_chamber(a)
    assert [e async for e in manager.subscribe(chamber.id)] == []


async def test_note_queued_for_running_debate_and_rejected_otherwise() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.PRO)
    chamber = make_chamber(a, b)
    repo = InMemoryChamberRepository()
    factory = StubFactory({a.id: BlockingProvider(), b.id: BlockingProvider()})
    manager = DebateManager()

    assert manager.add_note(chamber.id, "too early") is False  # nothing running

    manager.start(chamber, _build(factory, repo))
    await asyncio.sleep(0)
    try:
        assert manager.add_note(chamber.id, "stay on the economics") is True
    finally:
        await manager.stop(chamber.id)
    assert manager.add_note(chamber.id, "too late") is False  # finished


async def test_cannot_start_two_debates_for_same_chamber() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.PRO)
    chamber = make_chamber(a, b)
    repo = InMemoryChamberRepository()
    factory = StubFactory({a.id: BlockingProvider(), b.id: BlockingProvider()})
    manager = DebateManager()
    manager.start(chamber, _build(factory, repo))
    await asyncio.sleep(0)
    try:
        import pytest

        with pytest.raises(RuntimeError, match="already running"):
            manager.start(chamber, _build(factory, repo))
    finally:
        await manager.stop(chamber.id)
