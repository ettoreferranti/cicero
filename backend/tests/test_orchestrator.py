"""Integration tests for the debate engine, driven by deterministic providers."""

from __future__ import annotations

import pytest

from cicero.core.budget import DebateBudget
from cicero.core.consensus import ConsensusEngine
from cicero.core.orchestrator import DebateEngine
from cicero.domain.enums import ChamberStatus, ConsensusOutcome, ProviderType, Stance
from cicero.persistence.memory import InMemoryChamberRepository
from cicero.providers.base import (
    GenerateOptions,
    GenerateResult,
    Message,
    Provider,
    ProviderError,
)
from tests.conftest import ScriptedProvider, StubFactory, make_chamber, make_participant

MOD_OPTS = GenerateOptions(model="mod")


class FailingProvider(Provider):
    provider_type = ProviderType.MOCK

    async def generate(self, messages: list[Message], options: GenerateOptions) -> GenerateResult:
        raise ProviderError("provider is down")

    async def list_models(self) -> list[str]:
        return []


class CyclingProvider(Provider):
    """Returns a different stance on each successive poll (never stabilises)."""

    provider_type = ProviderType.MOCK

    def __init__(self, stance_sequence: list[str]) -> None:
        self._seq = stance_sequence
        self._i = 0

    async def generate(self, messages: list[Message], options: GenerateOptions) -> GenerateResult:
        if "reply with exactly one word" in messages[-1].content.lower():
            word = self._seq[self._i % len(self._seq)]
            self._i += 1
            return GenerateResult(content=word, prompt_tokens=1, completion_tokens=1)
        return GenerateResult(content="argument", prompt_tokens=5, completion_tokens=5)

    async def list_models(self) -> list[str]:
        return ["cycling"]


def _engine(factory: StubFactory, repo: InMemoryChamberRepository) -> DebateEngine:
    consensus = ConsensusEngine(factory, ScriptedProvider(moderator_reply="STATEMENT."), MOD_OPTS)
    return DebateEngine(factory, repo, consensus)


async def test_requires_two_participants() -> None:
    p = make_participant("Solo", Stance.PRO)
    chamber = make_chamber(p)
    repo = InMemoryChamberRepository()
    engine = _engine(StubFactory({p.id: ScriptedProvider()}), repo)
    with pytest.raises(ValueError, match=r"^a debate needs at least two participants$"):
        await engine.run(chamber, DebateBudget(max_rounds=2, max_total_tokens=1000))


async def test_debate_reaches_consensus_and_concludes() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.PRO)
    chamber = make_chamber(a, b)
    repo = InMemoryChamberRepository()
    factory = StubFactory(
        {a.id: ScriptedProvider(stance_word="pro"), b.id: ScriptedProvider(stance_word="pro")}
    )
    engine = _engine(factory, repo)

    result = await engine.run(chamber, DebateBudget(max_rounds=5, max_total_tokens=100_000))

    assert result.status is ChamberStatus.CONCLUDED
    assert result.consensus is not None
    assert result.consensus.outcome is ConsensusOutcome.CONSENSUS
    assert result.consensus.statement == "STATEMENT."
    assert result.config["stop_reason"] == "consensus"
    # One round of turns (2 participants) was enough to detect consensus.
    assert len(result.turns) == 2
    assert result.config["rounds_completed"] == 1
    assert result.config["tokens_used"] == 20  # 2 turns x (5 + 5)
    # Successful turns carry provider + token metadata.
    meta = result.turns[0].metadata
    assert set(meta) >= {"provider", "prompt_tokens", "completion_tokens"}
    # Persisted.
    stored = repo.get(chamber.id)
    assert stored is not None and stored.status is ChamberStatus.CONCLUDED


async def test_debate_runs_to_max_rounds_on_disagreement() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    repo = InMemoryChamberRepository()
    factory = StubFactory(
        {a.id: ScriptedProvider(stance_word="pro"), b.id: ScriptedProvider(stance_word="con")}
    )
    engine = _engine(factory, repo)

    # min_rounds == max_rounds prevents an early stability stop.
    budget = DebateBudget(max_rounds=2, max_total_tokens=100_000, min_rounds=2)
    result = await engine.run(chamber, budget)

    assert result.consensus is not None
    assert result.consensus.outcome is ConsensusOutcome.DISAGREEMENT
    assert result.config["stop_reason"] == "max_rounds"
    assert result.config["rounds_completed"] == 2
    assert len(result.turns) == 4  # 2 participants x 2 rounds


async def test_debate_stops_early_on_stable_stances() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    repo = InMemoryChamberRepository()
    factory = StubFactory(
        {a.id: ScriptedProvider(stance_word="pro"), b.id: ScriptedProvider(stance_word="con")}
    )
    engine = _engine(factory, repo)

    result = await engine.run(chamber, DebateBudget(max_rounds=9, max_total_tokens=100_000))

    assert result.config["stop_reason"] == "stances_stable"
    # Stable after round 2 (previous poll == current poll): stops there, not at max.
    assert result.config["rounds_completed"] == 2
    assert len(result.turns) == 4
    assert result.consensus is not None
    assert result.consensus.outcome is ConsensusOutcome.DISAGREEMENT


async def test_changing_stances_prevent_early_stability_stop() -> None:
    # Stances flip every round, so they never match the previous poll: the debate
    # must run to max_rounds instead of stopping early.
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    repo = InMemoryChamberRepository()
    factory = StubFactory(
        {
            a.id: CyclingProvider(["pro", "con", "pro", "con"]),
            b.id: CyclingProvider(["con", "pro", "con", "pro"]),
        }
    )
    engine = _engine(factory, repo)

    result = await engine.run(chamber, DebateBudget(max_rounds=4, max_total_tokens=100_000))

    assert result.config["stop_reason"] == "max_rounds"
    assert result.config["rounds_completed"] == 4
    assert len(result.turns) == 8


async def test_token_budget_stops_debate() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.PRO)
    chamber = make_chamber(a, b)
    repo = InMemoryChamberRepository()
    factory = StubFactory(
        {a.id: ScriptedProvider(stance_word="pro"), b.id: ScriptedProvider(stance_word="pro")}
    )
    engine = _engine(factory, repo)

    # Each turn uses 10 tokens; a 5-token budget stops after the first turn.
    result = await engine.run(chamber, DebateBudget(max_rounds=9, max_total_tokens=5))

    assert result.config["stop_reason"] == "token_budget"
    assert len(result.turns) == 1


async def test_failing_provider_does_not_crash_debate() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.PRO)
    chamber = make_chamber(a, b)
    repo = InMemoryChamberRepository()
    factory = StubFactory({a.id: ScriptedProvider(stance_word="pro"), b.id: FailingProvider()})
    engine = _engine(factory, repo)

    result = await engine.run(chamber, DebateBudget(max_rounds=2, max_total_tokens=100_000))

    assert result.status is ChamberStatus.CONCLUDED
    # B's failed turns are recorded with an error and empty content.
    b_turns = [t for t in result.turns if t.participant_id == b.id]
    assert b_turns and all(t.content == "" for t in b_turns)
    assert all("error" in t.metadata for t in b_turns)
    assert all("provider" in t.metadata for t in b_turns)


async def test_listener_receives_each_turn() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.PRO)
    chamber = make_chamber(a, b)
    repo = InMemoryChamberRepository()
    factory = StubFactory(
        {a.id: ScriptedProvider(stance_word="pro"), b.id: ScriptedProvider(stance_word="pro")}
    )
    seen = []

    class Listener:
        async def on_turn(self, ch, turn) -> None:  # type: ignore[no-untyped-def]
            seen.append(turn.id)

    consensus = ConsensusEngine(factory, ScriptedProvider(), MOD_OPTS)
    engine = DebateEngine(factory, repo, consensus, listener=Listener())
    result = await engine.run(chamber, DebateBudget(max_rounds=5, max_total_tokens=100_000))
    assert len(seen) == len(result.turns)
