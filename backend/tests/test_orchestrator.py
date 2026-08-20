"""Integration tests for the debate engine, driven by deterministic providers."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from cicero.core.budget import DebateBudget
from cicero.core.compliance import ARGUED_KEY, ComplianceJudge
from cicero.core.consensus import ConsensusEngine
from cicero.core.orchestrator import (
    REASONING_KEY,
    DebateEngine,
    TurnLimit,
    participants_spoken,
    resume_round,
    round_complete,
    tokens_spent,
)
from cicero.core.state_machine import transition
from cicero.domain.enums import (
    ChamberStatus,
    ConsensusOutcome,
    DecisionRule,
    ProviderType,
    Stance,
)
from cicero.domain.models import Chamber, Citation, Participant, Turn
from cicero.persistence.memory import InMemoryChamberRepository
from cicero.providers.base import (
    GenerateOptions,
    GenerateResult,
    Message,
    Provider,
    ProviderError,
)
from cicero.providers.mock import MockProvider
from tests.conftest import (
    RepeatingProvider,
    ScriptedProvider,
    StubFactory,
    _new_point,
    make_chamber,
    make_participant,
)

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
        self._turns = 0

    async def generate(self, messages: list[Message], options: GenerateOptions) -> GenerateResult:
        if "reply with exactly one word" in messages[-1].content.lower():
            word = self._seq[self._i % len(self._seq)]
            self._i += 1
            return GenerateResult(content=word, prompt_tokens=1, completion_tokens=1)
        # Distinct each turn: a debater repeating itself now ends the debate,
        # and this fixture exists to test the *stance* stop, not that one.
        self._turns += 1
        return GenerateResult(
            content=f"argument {_new_point(self._turns)}", prompt_tokens=5, completion_tokens=5
        )

    async def list_models(self) -> list[str]:
        return ["cycling"]


def _engine(
    factory: StubFactory,
    repo: InMemoryChamberRepository,
    judge: ComplianceJudge | None = None,
) -> DebateEngine:
    consensus = ConsensusEngine(factory, ScriptedProvider(moderator_reply="STATEMENT."), MOD_OPTS)
    return DebateEngine(factory, repo, consensus, judge=judge)


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
    chamber.settings.decision_rule = DecisionRule.UNANIMOUS
    engine = _engine(factory, repo)

    # min_rounds == max_rounds prevents an early stability stop.
    budget = DebateBudget(max_rounds=2, max_total_tokens=100_000, min_rounds=2)
    result = await engine.run(chamber, budget)

    assert result.consensus is not None
    assert result.consensus.outcome is ConsensusOutcome.DISAGREEMENT
    assert result.config["stop_reason"] == "max_rounds"
    assert result.config["rounds_completed"] == 2
    assert len(result.turns) == 4  # 2 participants x 2 rounds


async def test_a_stance_stalemate_pulls_the_convergence_phase_forward() -> None:
    """Stable stances still fast-forward convergence — they just no longer stop.

    Previously this scenario ended the debate with STANCES_STABLE. It must not: these
    debaters make a genuinely new point every turn, and the poll being unchanged says
    nothing about whether they have finished arguing (F7). What survives is the
    stalemate handling — the engine stops burning adversarial rounds and moves into
    the convergence phase early.
    """
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    repo = InMemoryChamberRepository()
    factory = StubFactory(
        {a.id: ScriptedProvider(stance_word="pro"), b.id: ScriptedProvider(stance_word="con")}
    )
    engine = _engine(factory, repo)

    result = await engine.run(chamber, DebateBudget(max_rounds=5, max_total_tokens=100_000))

    assert result.config["stop_reason"] == "max_rounds"
    assert result.config["rounds_completed"] == 5
    assert result.turns[0].metadata["phase"] == "open"
    # Without the stalemate fast-forward, convergence_rounds=2 of max_rounds=5 would
    # make round 2 (turns 4-5) still adversarial. The stalemate at round 1 pulls
    # converge_start to 2, so it is prompted as convergence instead.
    assert result.turns[4].metadata["phase"] == "converge"
    assert result.consensus is not None
    # Default rule is JUDGE: a pro/con tie goes to the moderator's verdict.
    assert result.consensus.outcome is ConsensusOutcome.VERDICT


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


class StubGatherer:
    """Deterministic evidence gatherer (no network)."""

    def __init__(self, citations: list[Citation] | None = None, fail: bool = False) -> None:
        self._citations = citations or []
        self._fail = fail
        self.calls = 0

    async def gather(self, topic: str) -> list[Citation]:
        self.calls += 1
        if self._fail:
            raise RuntimeError("search backend down")
        return list(self._citations)


class OneShotNotes:
    """Note source that yields its notes on the first drain only."""

    def __init__(self, notes: list[str]) -> None:
        self._notes = notes

    def drain(self) -> list[str]:
        notes, self._notes = self._notes, []
        return notes


def _two_pro_participants():  # type: ignore[no-untyped-def]
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.PRO)
    factory = StubFactory(
        {a.id: ScriptedProvider(stance_word="pro"), b.id: ScriptedProvider(stance_word="pro")}
    )
    return a, b, factory


async def test_evidence_injected_as_cited_system_turn() -> None:
    a, b, factory = _two_pro_participants()
    chamber = make_chamber(a, b)
    chamber.settings.web_evidence = True
    citation = Citation(url="https://example.org/mars", title="Mars study", excerpt="Dust is bad.")
    gatherer = StubGatherer([citation])
    repo = InMemoryChamberRepository()
    consensus = ConsensusEngine(factory, ScriptedProvider(), MOD_OPTS)
    engine = DebateEngine(factory, repo, consensus, evidence=gatherer)

    result = await engine.run(chamber, DebateBudget(max_rounds=3, max_total_tokens=100_000))

    first = result.turns[0]
    assert first.participant_id is None
    assert first.metadata["kind"] == "evidence"
    assert first.citations == [citation]
    assert "Dust is bad." in first.content
    assert "https://example.org/mars" in first.content
    assert gatherer.calls == 1


async def test_evidence_skipped_without_opt_in_and_on_failure() -> None:
    a, b, factory = _two_pro_participants()
    repo = InMemoryChamberRepository()
    consensus = ConsensusEngine(factory, ScriptedProvider(), MOD_OPTS)

    # Not opted in: the gatherer must not even be called.
    chamber = make_chamber(a, b)
    gatherer = StubGatherer([Citation(url="https://example.org", excerpt="x")])
    engine = DebateEngine(factory, repo, consensus, evidence=gatherer)
    result = await engine.run(chamber, DebateBudget(max_rounds=2, max_total_tokens=100_000))
    assert gatherer.calls == 0
    assert all(t.participant_id is not None for t in result.turns)

    # Opted in but gathering fails: the debate still runs, with no evidence turn.
    chamber2 = make_chamber(*[make_participant(n, Stance.PRO) for n in ("C", "D")])
    chamber2.settings.web_evidence = True
    factory2 = StubFactory(
        {p.id: ScriptedProvider(stance_word="pro") for p in chamber2.participants}
    )
    consensus2 = ConsensusEngine(factory2, ScriptedProvider(), MOD_OPTS)
    engine2 = DebateEngine(factory2, repo, consensus2, evidence=StubGatherer(fail=True))
    result2 = await engine2.run(chamber2, DebateBudget(max_rounds=2, max_total_tokens=100_000))
    assert result2.status is ChamberStatus.CONCLUDED
    assert all(t.participant_id is not None for t in result2.turns)


async def test_moderator_notes_drained_into_transcript() -> None:
    a, b, factory = _two_pro_participants()
    chamber = make_chamber(a, b)
    repo = InMemoryChamberRepository()
    consensus = ConsensusEngine(factory, ScriptedProvider(), MOD_OPTS)
    engine = DebateEngine(
        factory, repo, consensus, notes=OneShotNotes(["Address the costs."])
    )

    result = await engine.run(chamber, DebateBudget(max_rounds=3, max_total_tokens=100_000))

    note_turns = [t for t in result.turns if t.participant_id is None]
    assert len(note_turns) == 1
    assert note_turns[0].content == "Address the costs."
    assert note_turns[0].metadata["kind"] == "moderator_note"
    # Injected before the first participant turn of the round.
    assert result.turns[0] is note_turns[0]


class SearchingProvider(ScriptedProvider):
    """Replies with a SEARCH request on its first turn, then a real argument."""

    def __init__(self, query: str = "mars costs", always: bool = False) -> None:
        super().__init__(stance_word="pro", argument="Based on the evidence, pro.")
        self._query = query
        self._always = always

    async def generate(self, messages, options):  # type: ignore[no-untyped-def]
        text = messages[-1].content.lower()
        if "reply with exactly one word" in text:
            return await super().generate(messages, options)
        self.turn_calls += 1
        if self._always or self.turn_calls == 1:
            return GenerateResult(
                content=f"SEARCH: {self._query}", prompt_tokens=2, completion_tokens=2
            )
        return GenerateResult(content=self.argument, prompt_tokens=5, completion_tokens=5)


async def test_text_protocol_search_attaches_citations_to_the_turn() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.PRO)
    chamber = make_chamber(a, b)
    chamber.settings.web_evidence = True
    citation = Citation(url="https://example.org/x", title="X", excerpt="Fact.")
    gatherer = StubGatherer([citation])
    searcher = SearchingProvider()
    factory = StubFactory({a.id: searcher, b.id: ScriptedProvider(stance_word="pro")})
    repo = InMemoryChamberRepository()
    consensus = ConsensusEngine(factory, ScriptedProvider(), MOD_OPTS)
    engine = DebateEngine(factory, repo, consensus, evidence=gatherer)

    result = await engine.run(chamber, DebateBudget(max_rounds=2, max_total_tokens=100_000))

    # Turn 0 is the upfront evidence brief; turn 1 is A's researched argument.
    a_turn = result.turns[1]
    assert a_turn.participant_id == a.id
    assert a_turn.content == "Based on the evidence, pro."
    assert a_turn.citations == [citation]
    assert a_turn.metadata["searches"] == ["mars costs"]
    # Token accounting covers both generate calls (2+2 then 5+5).
    assert a_turn.metadata["prompt_tokens"] == 7
    assert a_turn.metadata["completion_tokens"] == 7
    assert searcher.turn_calls == 2
    # B never searched: no citations, no searches metadata.
    b_turn = result.turns[2]
    assert b_turn.citations == [] and "searches" not in b_turn.metadata


async def test_text_protocol_search_capped_per_turn() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.PRO)
    chamber = make_chamber(a, b)
    chamber.settings.web_evidence = True
    gatherer = StubGatherer([])
    stubborn = SearchingProvider(query="again", always=True)
    factory = StubFactory({a.id: stubborn, b.id: ScriptedProvider(stance_word="pro")})
    repo = InMemoryChamberRepository()
    consensus = ConsensusEngine(factory, ScriptedProvider(), MOD_OPTS)
    engine = DebateEngine(factory, repo, consensus, evidence=gatherer)

    result = await engine.run(chamber, DebateBudget(max_rounds=1, max_total_tokens=100_000))

    a_turn = next(t for t in result.turns if t.participant_id == a.id)
    # One search executed, one "no more searches" nudge, then the reply stands.
    assert a_turn.metadata["searches"] == ["again"]
    assert stubborn.turn_calls == 3
    assert a_turn.content == "SEARCH: again"


async def test_research_disabled_leaves_search_text_verbatim() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.PRO)
    chamber = make_chamber(a, b)  # web_evidence stays False
    gatherer = StubGatherer([Citation(url="https://example.org", excerpt="x")])
    searcher = SearchingProvider()
    factory = StubFactory({a.id: searcher, b.id: ScriptedProvider(stance_word="pro")})
    repo = InMemoryChamberRepository()
    consensus = ConsensusEngine(factory, ScriptedProvider(), MOD_OPTS)
    engine = DebateEngine(factory, repo, consensus, evidence=gatherer)

    result = await engine.run(chamber, DebateBudget(max_rounds=1, max_total_tokens=100_000))

    a_turn = next(t for t in result.turns if t.participant_id == a.id)
    assert a_turn.content == "SEARCH: mars costs"  # treated as an ordinary reply
    assert gatherer.calls == 0
    assert searcher.turn_calls == 1


async def test_native_search_provider_is_routed_to_tool_loop() -> None:
    class NativeProvider(ScriptedProvider):
        supports_native_search = True

        def __init__(self) -> None:
            super().__init__(stance_word="pro")
            self.native_calls = 0

        async def generate_with_search(self, messages, options, search, max_searches=1):  # type: ignore[no-untyped-def]
            self.native_calls += 1
            await search("native query")
            return GenerateResult(
                content="Tool-informed argument.", prompt_tokens=4, completion_tokens=4
            )

    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.PRO)
    chamber = make_chamber(a, b)
    chamber.settings.web_evidence = True
    citation = Citation(url="https://example.org/n", title="N", excerpt="Native fact.")
    gatherer = StubGatherer([citation])
    native = NativeProvider()
    factory = StubFactory({a.id: native, b.id: ScriptedProvider(stance_word="pro")})
    repo = InMemoryChamberRepository()
    consensus = ConsensusEngine(factory, ScriptedProvider(), MOD_OPTS)
    engine = DebateEngine(factory, repo, consensus, evidence=gatherer)

    result = await engine.run(chamber, DebateBudget(max_rounds=1, max_total_tokens=100_000))

    a_turn = next(t for t in result.turns if t.participant_id == a.id)
    assert native.native_calls == 1
    assert a_turn.content == "Tool-informed argument."
    assert a_turn.metadata["searches"] == ["native query"]
    assert a_turn.citations == [citation]


def _turn(participant_id, round_index: int, tokens: int = 10):  # type: ignore[no-untyped-def]
    half = tokens // 2
    return Turn(
        participant_id=participant_id,
        round_index=round_index,
        content="said something",
        metadata={"prompt_tokens": half, "completion_tokens": tokens - half},
    )


def test_resume_round_and_spoken_and_tokens() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    assert resume_round(chamber) == 0  # fresh debate

    # System turns (notes/evidence) never affect the resume point.
    chamber.turns.append(Turn(participant_id=None, round_index=0, content="note"))
    assert resume_round(chamber) == 0

    chamber.turns.append(_turn(a.id, 0))
    chamber.turns.append(_turn(b.id, 0))
    assert resume_round(chamber) == 1  # round 0 fully spoken

    chamber.turns.append(_turn(a.id, 1))
    assert resume_round(chamber) == 1  # round 1 partial → finish it
    assert participants_spoken(chamber, 1) == {a.id}

    # Token seeding ignores junk metadata.
    chamber.turns.append(
        Turn(
            participant_id=b.id,
            round_index=1,
            content="x",
            metadata={"prompt_tokens": True, "completion_tokens": -3},
        )
    )
    assert tokens_spent(chamber) == 30  # three clean 10-token turns

    # Unknown speakers do not block round completion detection forever.
    ghost_only = make_chamber(a, b)
    ghost_only.turns.append(_turn(uuid4(), 0))
    assert resume_round(ghost_only) == 0


async def test_resume_finishes_partial_round_without_duplicates() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.PRO)
    chamber = make_chamber(a, b)
    # Simulate an interrupted run: round 0 complete, round 1 has only A.
    chamber.turns.extend([_turn(a.id, 0), _turn(b.id, 0), _turn(a.id, 1)])
    transition(chamber, ChamberStatus.RUNNING)
    transition(chamber, ChamberStatus.PAUSED)

    provider_a = ScriptedProvider(stance_word="pro")
    provider_b = ScriptedProvider(stance_word="pro")
    factory = StubFactory({a.id: provider_a, b.id: provider_b})
    repo = InMemoryChamberRepository()
    engine = _engine(factory, repo)

    result = await engine.run(chamber, DebateBudget(max_rounds=5, max_total_tokens=100_000))

    assert result.status is ChamberStatus.CONCLUDED
    # Only B spoke on resume: A's round-1 turn was not repeated.
    assert provider_a.turn_calls == 0
    assert provider_b.turn_calls == 1
    assert len([t for t in result.turns if t.participant_id == a.id]) == 2
    # Consensus detected right after the completed round.
    assert result.config["stop_reason"] == "consensus"
    assert result.config["rounds_completed"] == 2
    # Prior spend (3 turns x 10) plus B's new turn (5+5) count against budget.
    assert result.config["tokens_used"] == 40


async def test_resume_with_exhausted_budget_concludes_without_new_turns() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    chamber.turns.extend([_turn(a.id, 0), _turn(b.id, 0)])
    transition(chamber, ChamberStatus.RUNNING)
    transition(chamber, ChamberStatus.PAUSED)

    provider_a = ScriptedProvider(stance_word="pro")
    provider_b = ScriptedProvider(stance_word="con")
    factory = StubFactory({a.id: provider_a, b.id: provider_b})
    engine = _engine(factory, InMemoryChamberRepository())

    # 20 tokens already spent >= 15 budget → no further turns, straight to verdict.
    result = await engine.run(chamber, DebateBudget(max_rounds=5, max_total_tokens=15))

    assert result.status is ChamberStatus.CONCLUDED
    assert provider_a.turn_calls == 0 and provider_b.turn_calls == 0
    assert result.config["stop_reason"] == "token_budget"


async def test_resume_does_not_regather_evidence() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.PRO)
    chamber = make_chamber(a, b)
    chamber.settings.web_evidence = True
    chamber.turns.append(
        Turn(
            participant_id=None,
            round_index=0,
            content="Earlier evidence.",
            metadata={"kind": "evidence"},
        )
    )
    transition(chamber, ChamberStatus.RUNNING)
    transition(chamber, ChamberStatus.PAUSED)

    gatherer = StubGatherer([Citation(url="https://example.org", excerpt="x")])
    factory = StubFactory(
        {a.id: ScriptedProvider(stance_word="pro"), b.id: ScriptedProvider(stance_word="pro")}
    )
    repo = InMemoryChamberRepository()
    consensus = ConsensusEngine(factory, ScriptedProvider(), MOD_OPTS)
    engine = DebateEngine(factory, repo, consensus, evidence=gatherer)

    result = await engine.run(chamber, DebateBudget(max_rounds=2, max_total_tokens=100_000))

    assert result.status is ChamberStatus.CONCLUDED
    evidence_turns = [t for t in result.turns if t.metadata.get("kind") == "evidence"]
    assert len(evidence_turns) == 1  # the pre-existing one only
    assert gatherer.calls == 0  # brief not regathered, and no turn searched


async def test_stance_history_records_each_poll_once() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    chamber.settings.decision_rule = DecisionRule.UNANIMOUS
    repo = InMemoryChamberRepository()
    factory = StubFactory(
        {
            a.id: CyclingProvider(["pro", "con", "pro"]),
            b.id: CyclingProvider(["con", "con", "con"]),
        }
    )
    engine = _engine(factory, repo)

    # min_rounds=1 → a poll after every round. A holds out for one round, then
    # crosses to con, which is exactly the movement FR-25 exists to capture.
    result = await engine.run(chamber, DebateBudget(max_rounds=3, max_total_tokens=100_000))

    assert [poll.round_index for poll in result.stance_history] == [0, 1]
    assert result.stance_history[0].stances == {str(a.id): Stance.PRO, str(b.id): Stance.CON}
    assert result.stance_history[1].stances == {str(a.id): Stance.CON, str(b.id): Stance.CON}
    # Round 1 found consensus and ended the debate; the final poll measured a
    # round already recorded, so it is not duplicated.
    assert result.config["stop_reason"] == "consensus"
    stored = repo.get(chamber.id)
    assert stored is not None and len(stored.stance_history) == 2


async def test_stance_history_is_recorded_when_no_round_poll_ran() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    repo = InMemoryChamberRepository()
    factory = StubFactory(
        {a.id: ScriptedProvider(stance_word="pro"), b.id: ScriptedProvider(stance_word="con")}
    )
    engine = _engine(factory, repo)

    # min_rounds == max_rounds: the loop never reaches its early-stop poll, so
    # the only measurement is the final one — and it is still recorded.
    budget = DebateBudget(max_rounds=2, max_total_tokens=100_000, min_rounds=2)
    result = await engine.run(chamber, budget)

    assert len(result.stance_history) == 1
    assert result.stance_history[0].round_index == 1  # after the last round
    assert result.stance_history[0].stances == {str(a.id): Stance.PRO, str(b.id): Stance.CON}


async def test_stepping_does_not_double_record_stance_polls() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    chamber.settings.decision_rule = DecisionRule.UNANIMOUS
    repo = InMemoryChamberRepository()
    factory = StubFactory(
        {a.id: ScriptedProvider(stance_word="pro"), b.id: ScriptedProvider(stance_word="con")}
    )
    engine = _engine(factory, repo)
    budget = DebateBudget(max_rounds=2, max_total_tokens=100_000)

    while chamber.status is not ChamberStatus.CONCLUDED:
        chamber = await engine.run(chamber, budget, TurnLimit(1))

    # Each step is its own run, but a round is still recorded exactly once.
    rounds = [poll.round_index for poll in chamber.stance_history]
    assert rounds == sorted(set(rounds))


class OneShotMutes:
    """Mute source that yields its changes on the first drain only."""

    def __init__(self, changes):  # type: ignore[no-untyped-def]
        self._changes = changes

    def drain(self):  # type: ignore[no-untyped-def]
        changes, self._changes = self._changes, {}
        return changes


async def test_muted_participant_stops_taking_turns() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    c = make_participant("C", Stance.CON)
    chamber = make_chamber(a, b, c)
    chamber.settings.decision_rule = DecisionRule.UNANIMOUS
    repo = InMemoryChamberRepository()
    providers = {
        a.id: ScriptedProvider(stance_word="pro"),
        b.id: ScriptedProvider(stance_word="con"),
        c.id: ScriptedProvider(stance_word="con"),
    }
    factory = StubFactory(providers)
    consensus = ConsensusEngine(factory, ScriptedProvider(moderator_reply="S."), MOD_OPTS)
    engine = DebateEngine(
        factory, repo, consensus, mutes=OneShotMutes({b.id: True})
    )

    budget = DebateBudget(max_rounds=2, max_total_tokens=100_000, min_rounds=2)
    result = await engine.run(chamber, budget)

    # B was muted before round 0 ran, so it never spoke...
    assert providers[b.id].turn_calls == 0
    assert not any(turn.participant_id == b.id for turn in result.turns)
    # ...but it is still on the roster, and still polled for its stance.
    assert result.participant_by_id(b.id) is not None
    assert providers[b.id].poll_calls > 0
    assert str(b.id) in result.stance_history[-1].stances
    # The rounds still complete: two active debaters x two rounds.
    assert len(result.turns) == 4
    assert result.config["rounds_completed"] == 2


async def test_muting_changes_the_decision_rule_tally() -> None:
    # A pro, B and C con. Unmuted that is a 1-2 split; muting both con debaters
    # leaves a single pro voter, which is unanimous.
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    c = make_participant("C", Stance.CON)
    chamber = make_chamber(a, b, c)
    chamber.settings.decision_rule = DecisionRule.UNANIMOUS
    repo = InMemoryChamberRepository()
    factory = StubFactory(
        {
            a.id: ScriptedProvider(stance_word="pro"),
            b.id: ScriptedProvider(stance_word="con"),
            c.id: ScriptedProvider(stance_word="con"),
        }
    )
    consensus = ConsensusEngine(factory, ScriptedProvider(moderator_reply="S."), MOD_OPTS)
    engine = DebateEngine(
        factory, repo, consensus, mutes=OneShotMutes({b.id: True, c.id: True})
    )

    result = await engine.run(chamber, DebateBudget(max_rounds=2, max_total_tokens=100_000))

    assert result.consensus is not None
    assert result.consensus.outcome is ConsensusOutcome.CONSENSUS
    assert result.consensus.winning_stance is Stance.PRO
    # The muted debaters' stances are still on the record, just not counted.
    assert set(result.consensus.final_stances) == {str(a.id), str(b.id), str(c.id)}
    assert result.consensus.final_stances[str(b.id)] is Stance.CON


async def test_unmuting_brings_a_debater_back() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.PRO)
    chamber = make_chamber(a, b)
    b.muted = True  # started muted
    repo = InMemoryChamberRepository()
    providers = {
        a.id: ScriptedProvider(stance_word="pro"),
        b.id: ScriptedProvider(stance_word="pro"),
    }
    factory = StubFactory(providers)
    consensus = ConsensusEngine(factory, ScriptedProvider(moderator_reply="S."), MOD_OPTS)
    engine = DebateEngine(
        factory, repo, consensus, mutes=OneShotMutes({b.id: False})
    )

    result = await engine.run(chamber, DebateBudget(max_rounds=1, max_total_tokens=100_000))

    assert result.participant_by_id(b.id) is not None
    assert result.participant_by_id(b.id).muted is False  # type: ignore[union-attr]
    assert providers[b.id].turn_calls == 1


async def test_round_completion_ignores_muted_debaters_on_resume() -> None:
    # Round 0 has only A's turn, and B is muted: the round is complete, so the
    # resume point is round 1 rather than "finish round 0".
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    chamber.turns.append(_turn(a.id, 0))
    b.muted = True
    assert round_complete(chamber, 0)
    assert resume_round(chamber) == 1

    b.muted = False
    assert not round_complete(chamber, 0)
    assert resume_round(chamber) == 0


async def test_debate_ends_when_everyone_starts_repeating() -> None:
    # Both debaters make one fresh point, then restate it forever. Without this
    # stop the debate burns its remaining rounds at full price saying nothing —
    # observed with qwen3 re-emitting its previous turn verbatim.
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    chamber.settings.decision_rule = DecisionRule.UNANIMOUS
    repo = InMemoryChamberRepository()
    providers = {
        a.id: RepeatingProvider(stance_word="pro", fresh_turns=1),
        b.id: RepeatingProvider(stance_word="con", fresh_turns=1),
    }
    engine = _engine(StubFactory(providers), repo)

    budget = DebateBudget(max_rounds=8, max_total_tokens=100_000, min_rounds=8)
    result = await engine.run(chamber, budget)

    assert result.config["stop_reason"] == "repetition"
    # Round 0 was fresh, round 1 repeated it: stopped there, not at round 8.
    assert result.config["rounds_completed"] == 2
    assert len(result.turns) == 4
    # The repeated turns say so; the first ones do not.
    assert [turn.metadata.get("repeated") for turn in result.turns] == [None, None, True, True]


async def test_one_debater_still_making_progress_keeps_the_debate_alive() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    chamber.settings.decision_rule = DecisionRule.UNANIMOUS
    repo = InMemoryChamberRepository()
    providers = {
        a.id: RepeatingProvider(stance_word="pro", fresh_turns=1),  # gives up at once
        b.id: RepeatingProvider(stance_word="con", fresh_turns=99),  # keeps arguing
    }
    engine = _engine(StubFactory(providers), repo)

    budget = DebateBudget(max_rounds=3, max_total_tokens=100_000, min_rounds=3)
    result = await engine.run(chamber, budget)

    # A repeats from round 1, but B is still adding points, so the debate runs.
    assert result.config["stop_reason"] == "max_rounds"
    assert result.config["rounds_completed"] == 3


async def test_repetition_stop_can_be_switched_off() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    chamber.settings.decision_rule = DecisionRule.UNANIMOUS
    chamber.settings.stop_on_repetition = False
    repo = InMemoryChamberRepository()
    providers = {
        a.id: RepeatingProvider(stance_word="pro", fresh_turns=1),
        b.id: RepeatingProvider(stance_word="con", fresh_turns=1),
    }
    engine = _engine(StubFactory(providers), repo)

    budget = DebateBudget(max_rounds=3, max_total_tokens=100_000, min_rounds=3)
    result = await engine.run(chamber, budget)

    assert result.config["stop_reason"] == "max_rounds"
    assert result.config["rounds_completed"] == 3
    # Still *recorded*, just not acted on: the flag is diagnostic either way.
    assert any(turn.metadata.get("repeated") for turn in result.turns)


async def test_a_strict_threshold_ignores_a_reworded_turn() -> None:
    # threshold 1.0 means byte-identical only. RepeatingProvider's turns differ
    # by a whole sentence while it still has fresh points, so nothing is flagged
    # until it genuinely starts restating.
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    chamber.settings.repetition_threshold = 1.0
    chamber.settings.decision_rule = DecisionRule.UNANIMOUS
    repo = InMemoryChamberRepository()
    providers = {
        a.id: RepeatingProvider(stance_word="pro", fresh_turns=2),
        b.id: RepeatingProvider(stance_word="con", fresh_turns=2),
    }
    engine = _engine(StubFactory(providers), repo)

    budget = DebateBudget(max_rounds=6, max_total_tokens=100_000, min_rounds=6)
    result = await engine.run(chamber, budget)

    assert result.config["stop_reason"] == "repetition"
    assert result.config["rounds_completed"] == 3  # two fresh rounds, then a repeat


def test_round_complete_requires_every_participant() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    assert not round_complete(chamber, 0)
    chamber.turns.append(_turn(a.id, 0))
    assert not round_complete(chamber, 0)
    chamber.turns.append(_turn(b.id, 0))
    assert round_complete(chamber, 0)
    assert not round_complete(chamber, 1)  # nobody has spoken in the next round


def test_turn_limit_counts_down_and_rejects_empty_steps() -> None:
    with pytest.raises(ValueError, match=r"^a step must run at least one turn$"):
        TurnLimit(0)
    limit = TurnLimit(2)
    assert limit.remaining == 2 and not limit.reached
    limit.consume()
    assert limit.remaining == 1 and not limit.reached  # one turn of two spent
    limit.consume()
    assert limit.remaining == 0 and limit.reached
    limit.consume()
    assert limit.reached  # stays reached once the allowance is spent


async def test_step_runs_one_turn_then_parks_mid_round() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    repo = InMemoryChamberRepository()
    provider_a = ScriptedProvider(stance_word="pro")
    provider_b = ScriptedProvider(stance_word="con")
    engine = _engine(StubFactory({a.id: provider_a, b.id: provider_b}), repo)
    budget = DebateBudget(max_rounds=3, max_total_tokens=100_000, min_rounds=2)

    result = await engine.run(chamber, budget, TurnLimit(1))

    # Only the first participant spoke, and the debate is resumable, not over.
    assert result.status is ChamberStatus.PAUSED
    assert result.consensus is None
    assert [t.participant_id for t in result.turns] == [a.id]
    assert provider_a.turn_calls == 1 and provider_b.turn_calls == 0
    assert provider_a.poll_calls == 0  # no stance poll mid-round
    stored = repo.get(chamber.id)
    assert stored is not None and stored.status is ChamberStatus.PAUSED

    # The next step finishes round 0 and parks on the round boundary: min_rounds
    # blocks an early stop and rounds are not exhausted.
    result = await engine.run(result, budget, TurnLimit(1))
    assert result.status is ChamberStatus.PAUSED
    assert [t.participant_id for t in result.turns] == [a.id, b.id]
    assert provider_a.turn_calls == 1 and provider_b.turn_calls == 1


async def test_stepping_walks_a_debate_to_the_same_conclusion() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    chamber.settings.decision_rule = DecisionRule.UNANIMOUS
    repo = InMemoryChamberRepository()
    provider_a = ScriptedProvider(stance_word="pro")
    provider_b = ScriptedProvider(stance_word="con")
    engine = _engine(StubFactory({a.id: provider_a, b.id: provider_b}), repo)
    budget = DebateBudget(max_rounds=2, max_total_tokens=100_000, min_rounds=2)

    steps = 0
    while chamber.status is not ChamberStatus.CONCLUDED:
        chamber = await engine.run(chamber, budget, TurnLimit(1))
        steps += 1
        assert steps <= 10  # guard against a stepping loop that never converges

    # One step per turn — nobody spoke twice in a round — and the outcome matches
    # the same budget run straight through (see the max-rounds test above).
    assert steps == 4
    assert len(chamber.turns) == 4
    assert provider_a.turn_calls == 2 and provider_b.turn_calls == 2
    assert chamber.config["stop_reason"] == "max_rounds"
    assert chamber.config["rounds_completed"] == 2
    assert chamber.consensus is not None
    assert chamber.consensus.outcome is ConsensusOutcome.DISAGREEMENT


async def test_step_concludes_when_its_turn_closes_a_round_on_consensus() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.PRO)
    chamber = make_chamber(a, b)
    repo = InMemoryChamberRepository()
    factory = StubFactory(
        {a.id: ScriptedProvider(stance_word="pro"), b.id: ScriptedProvider(stance_word="pro")}
    )
    engine = _engine(factory, repo)
    budget = DebateBudget(max_rounds=5, max_total_tokens=100_000)

    parked = await engine.run(chamber, budget, TurnLimit(1))
    assert parked.status is ChamberStatus.PAUSED

    concluded = await engine.run(parked, budget, TurnLimit(1))

    # The second step completed round 0, where the stance poll found consensus:
    # the debate ends there rather than parking again.
    assert concluded.status is ChamberStatus.CONCLUDED
    assert concluded.config["stop_reason"] == "consensus"
    assert concluded.config["rounds_completed"] == 1
    assert len(concluded.turns) == 2
    assert concluded.consensus is not None


async def test_step_that_exhausts_the_budget_concludes_instead_of_parking() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.PRO)
    chamber = make_chamber(a, b)
    repo = InMemoryChamberRepository()
    factory = StubFactory(
        {a.id: ScriptedProvider(stance_word="pro"), b.id: ScriptedProvider(stance_word="pro")}
    )
    engine = _engine(factory, repo)

    # The single stepped turn costs 10 tokens against a 5-token budget.
    result = await engine.run(
        chamber, DebateBudget(max_rounds=5, max_total_tokens=5), TurnLimit(1)
    )

    assert result.status is ChamberStatus.CONCLUDED
    assert result.config["stop_reason"] == "token_budget"
    assert len(result.turns) == 1


async def test_step_can_advance_a_debate_paused_mid_round() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.PRO)
    c = make_participant("C", Stance.PRO)
    chamber = make_chamber(a, b, c)
    # An interrupted run left round 0 with only A's turn.
    chamber.turns.append(_turn(a.id, 0))
    transition(chamber, ChamberStatus.RUNNING)
    transition(chamber, ChamberStatus.PAUSED)

    repo = InMemoryChamberRepository()
    providers = {p.id: ScriptedProvider(stance_word="pro") for p in (a, b, c)}
    engine = _engine(StubFactory(providers), repo)
    budget = DebateBudget(max_rounds=4, max_total_tokens=100_000)

    result = await engine.run(chamber, budget, TurnLimit(1))

    # Only B spoke: A's turn was neither repeated nor skipped past.
    assert result.status is ChamberStatus.PAUSED
    assert [t.participant_id for t in result.turns] == [a.id, b.id]
    assert providers[a.id].turn_calls == 0
    assert providers[b.id].turn_calls == 1
    assert providers[c.id].turn_calls == 0


async def test_step_can_be_followed_by_a_full_run() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.PRO)
    chamber = make_chamber(a, b)
    repo = InMemoryChamberRepository()
    provider_a = ScriptedProvider(stance_word="pro")
    provider_b = ScriptedProvider(stance_word="pro")
    engine = _engine(StubFactory({a.id: provider_a, b.id: provider_b}), repo)
    budget = DebateBudget(max_rounds=5, max_total_tokens=100_000)

    parked = await engine.run(chamber, budget, TurnLimit(1))
    assert parked.status is ChamberStatus.PAUSED

    result = await engine.run(parked, budget)  # resume, no limit

    assert result.status is ChamberStatus.CONCLUDED
    assert len(result.turns) == 2
    assert provider_a.turn_calls == 1  # the stepped turn was not repeated
    assert provider_b.turn_calls == 1


async def test_cancelled_debate_parks_as_paused() -> None:
    class Blocking(ScriptedProvider):
        async def generate(self, messages, options):  # type: ignore[no-untyped-def]
            await asyncio.sleep(30)
            return await super().generate(messages, options)

    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.PRO)
    chamber = make_chamber(a, b)
    repo = InMemoryChamberRepository()
    factory = StubFactory({a.id: Blocking(), b.id: Blocking()})
    engine = _engine(factory, repo)

    task = asyncio.create_task(
        engine.run(chamber, DebateBudget(max_rounds=3, max_total_tokens=100_000))
    )
    await asyncio.sleep(0)  # let it reach the blocking generate
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert chamber.status is ChamberStatus.PAUSED
    stored = repo.get(chamber.id)
    assert stored is not None and stored.status is ChamberStatus.PAUSED


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


class LateMutes:
    """Mute source whose change arrives after the last round boundary.

    Models an operator clicking Mute during the final round: the boundary drain
    has already happened, so only an end-of-debate drain can see it.
    """

    def __init__(self, changes):  # type: ignore[no-untyped-def]
        self._changes = changes
        self.drains = 0

    def drain(self):  # type: ignore[no-untyped-def]
        self.drains += 1
        if self.drains == 1:
            return {}  # the round-0 boundary: nothing requested yet
        changes, self._changes = self._changes, {}
        return changes


async def test_mute_requested_in_the_last_round_is_not_silently_dropped() -> None:
    """The API answers "queued" — a promise it must keep even if no round follows.

    Dropped, the operator saw no muted debater, no way to undo one, and no error:
    the request simply evaporated with the debate task.
    """
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    repo = InMemoryChamberRepository()
    factory = StubFactory(
        {a.id: ScriptedProvider(stance_word="pro"), b.id: ScriptedProvider(stance_word="con")}
    )
    consensus = ConsensusEngine(factory, ScriptedProvider(moderator_reply="S."), MOD_OPTS)
    mutes = LateMutes({b.id: True})
    engine = DebateEngine(factory, repo, consensus, mutes=mutes)

    budget = DebateBudget(max_rounds=1, max_total_tokens=100_000, min_rounds=1)
    result = await engine.run(chamber, budget)

    assert result.status is ChamberStatus.CONCLUDED
    assert result.participant_by_id(b.id).muted is True  # type: ignore[union-attr]
    # B still argued: the mute arrived too late to skip its turn, which is the
    # documented boundary behaviour. What must not happen is losing the request.
    assert any(turn.participant_id == b.id for turn in result.turns)
    # And it is persisted, not just set on the returned object.
    stored = repo.get(chamber.id)
    assert stored is not None
    assert stored.participant_by_id(b.id).muted is True  # type: ignore[union-attr]


async def test_stable_stances_do_not_end_a_debate_while_arguments_are_new() -> None:
    """The F7 regression: an unchanged poll is not convergence.

    A real debate ended at round 3 of 8 on three identical polls, in a round where one
    debater announced a reversal ("I've reconsidered") and another moved from opposition
    to explicit support. The one-word poll is an unreliable instrument and saw none of
    it, so stance stability alone must not be enough to stop.
    """
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    repo = InMemoryChamberRepository()
    # Fixed stance words => every poll identical. ScriptedProvider still makes a
    # genuinely new point each turn, so nobody has stopped arguing.
    factory = StubFactory(
        {a.id: ScriptedProvider(stance_word="pro"), b.id: ScriptedProvider(stance_word="con")}
    )
    engine = _engine(factory, repo)

    result = await engine.run(chamber, DebateBudget(max_rounds=6, max_total_tokens=100_000))

    assert result.config["stop_reason"] != "stances_stable"
    assert result.config["rounds_completed"] == 6


async def test_stable_stances_end_the_debate_once_nobody_says_anything_new() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    # Repetition-stopping off, so the deterministic signal still corroborates stance
    # stability without ending the debate on its own — this is the path the setting
    # must not disable, and the reason the measurement is computed unconditionally.
    chamber.settings.stop_on_repetition = False
    repo = InMemoryChamberRepository()
    factory = StubFactory(
        {
            a.id: RepeatingProvider(stance_word="pro", fresh_turns=1),
            b.id: RepeatingProvider(stance_word="con", fresh_turns=1),
        }
    )
    engine = _engine(factory, repo)

    result = await engine.run(chamber, DebateBudget(max_rounds=9, max_total_tokens=100_000))

    assert result.config["stop_reason"] == "stances_stable"
    assert result.config["rounds_completed"] < 9


async def test_stop_on_repetition_off_does_not_stop_on_repetition_alone() -> None:
    """The setting still does its own job: only the REPETITION stop is gated by it."""
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    chamber.settings.stop_on_repetition = False
    chamber.settings.convergence_rounds = 0
    repo = InMemoryChamberRepository()
    factory = StubFactory(
        {
            a.id: RepeatingProvider(stance_word="pro", fresh_turns=1),
            b.id: RepeatingProvider(stance_word="con", fresh_turns=1),
        }
    )
    engine = _engine(factory, repo)

    result = await engine.run(chamber, DebateBudget(max_rounds=4, max_total_tokens=100_000))

    assert result.config["stop_reason"] != "repetition"


async def test_repetition_still_stops_a_debate_whose_stances_keep_changing() -> None:
    """The REPETITION path is unchanged: it does not depend on the poll settling."""

    class _RepeatingCycler(Provider):
        """Cycles its reported stance, but repeats the same argument every turn."""

        provider_type = ProviderType.MOCK

        def __init__(self, sequence: list[str]) -> None:
            self._seq = sequence
            self._i = 0

        async def generate(
            self, messages: list[Message], options: GenerateOptions
        ) -> GenerateResult:
            if "reply with exactly one word" in messages[-1].content.lower():
                word = self._seq[self._i % len(self._seq)]
                self._i += 1
                return GenerateResult(content=word, prompt_tokens=1, completion_tokens=1)
            return GenerateResult(content="The same point again.", prompt_tokens=5,
                                  completion_tokens=5)

        async def list_models(self) -> list[str]:
            return ["repeating-cycler"]

    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    repo = InMemoryChamberRepository()
    # Out of phase, so they never agree and the CONSENSUS stop cannot pre-empt this.
    factory = StubFactory({
        a.id: _RepeatingCycler(["pro", "con"]),
        b.id: _RepeatingCycler(["con", "pro"]),
    })
    engine = _engine(factory, repo)

    result = await engine.run(chamber, DebateBudget(max_rounds=9, max_total_tokens=100_000))

    assert result.config["stop_reason"] == "repetition"
    assert result.config["rounds_completed"] < 9


class _CountingProvider(MockProvider):
    """Records the last message of every call it receives, then delegates.

    Used to assert the judge was (or was not) actually *called* — as opposed
    to asserting on ``ARGUED_KEY``'s absence, which a judge that ran and had
    its verdict discarded would also satisfy.
    """

    def __init__(self, calls: list[str], scripted: list[str]) -> None:
        super().__init__(scripted=scripted)
        self._calls = calls

    async def generate(
        self, messages: list[Message], options: GenerateOptions
    ) -> GenerateResult:
        self._calls.append(messages[-1].content)
        return await super().generate(messages, options)


def _judged_chamber() -> tuple[Chamber, StubFactory, InMemoryChamberRepository]:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    factory = StubFactory(
        {a.id: ScriptedProvider(stance_word="pro"), b.id: ScriptedProvider(stance_word="pro")}
    )
    return chamber, factory, InMemoryChamberRepository()


async def test_turns_record_the_side_they_were_judged_to_argue() -> None:
    chamber, factory, repo = _judged_chamber()
    judge = ComplianceJudge(MockProvider(scripted=["con"] * 50), "mock-small")
    result = await _engine(factory, repo, judge).run(
        chamber, DebateBudget(max_rounds=1, max_total_tokens=100_000)
    )
    debate_turns = [t for t in result.turns if t.participant_id is not None]
    assert debate_turns
    assert all(t.metadata[ARGUED_KEY] == "con" for t in debate_turns)


async def test_no_judgement_is_recorded_when_the_setting_is_off() -> None:
    """The setting must suppress the judge *call*, not just its recorded verdict —
    a judge that ran and had its result discarded would also leave the key
    absent, so the call itself is what's asserted here."""
    chamber, factory, repo = _judged_chamber()
    chamber.settings.measure_compliance = False
    calls: list[str] = []
    judge = ComplianceJudge(_CountingProvider(calls, scripted=["con"] * 50), "mock-small")
    result = await _engine(factory, repo, judge).run(
        chamber, DebateBudget(max_rounds=1, max_total_tokens=100_000)
    )
    assert all(ARGUED_KEY not in t.metadata for t in result.turns)
    assert calls == []


async def test_a_failing_judge_leaves_the_key_absent_and_the_debate_running() -> None:
    """NFR-R-1: one failing judge call must not crash a debate, and must not be
    recorded as agreement."""
    chamber, factory, repo = _judged_chamber()
    judge = ComplianceJudge(MockProvider(fail_after=1), "mock-small")
    result = await _engine(factory, repo, judge).run(
        chamber, DebateBudget(max_rounds=1, max_total_tokens=100_000)
    )
    assert result.status is ChamberStatus.CONCLUDED
    debate_turns = [t for t in result.turns if t.participant_id is not None]
    assert debate_turns
    assert all(ARGUED_KEY not in t.metadata for t in debate_turns)


async def test_an_errored_turn_is_not_sent_to_the_judge() -> None:
    """An empty turn has no prose to read, so judging it would spend a call to
    learn nothing."""
    judged: list[str] = []

    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    # A debater whose own provider fails produces an empty turn.
    factory = StubFactory(
        {a.id: MockProvider(fail_after=1), b.id: MockProvider(fail_after=1)}
    )
    judge = ComplianceJudge(_CountingProvider(judged, scripted=["pro"] * 50), "mock-small")
    result = await _engine(factory, InMemoryChamberRepository(), judge).run(
        chamber, DebateBudget(max_rounds=1, max_total_tokens=100_000)
    )
    debate_turns = [t for t in result.turns if t.participant_id is not None]
    assert debate_turns
    assert all(t.content == "" for t in debate_turns)
    assert judged == []


# --- Per-debater reasoning control (issue #28) --------------------------------


class RecordingProvider(ScriptedProvider):
    """A ScriptedProvider that keeps the options it was called with."""

    def __init__(self) -> None:
        super().__init__(moderator_reply="STATEMENT.")
        self.options: list[GenerateOptions] = []

    async def generate(
        self, messages: list[Message], options: GenerateOptions
    ) -> GenerateResult:
        self.options.append(options)
        return await super().generate(messages, options)

    def turn_options(self, participant: Participant) -> list[GenerateOptions]:
        """Just the turn calls.

        The same provider also answers the stance poll, which sets
        ``allow_reasoning=False`` for its own reasons (a thinking model asked for
        one word once burned the whole budget and returned empty content). Only
        the turn budget comes from the participant's tuning, so that is what
        separates them.
        """
        return [o for o in self.options if o.max_tokens == participant.tuning.max_tokens]


async def test_a_turn_carries_the_participants_reasoning_setting() -> None:
    """A thinking model spends `max_tokens` on reasoning before it writes a word.

    Ollama counts reasoning against ``num_predict`` but returns it in a separate
    ``thinking`` field, so on a reasoning model ``tuning.max_tokens`` is a budget
    shared between invisible narration and the speech — in that order. Measured
    on muse-glimmer:30b-mlx at num_predict=1400 with a transcript in context:
    three samples produced 3733-6319 characters of thinking and 0-2635 of
    content, every one cut mid-sentence; with reasoning off, three samples
    finished cleanly on ~60% of the same budget.

    The flag already existed on GenerateOptions and the stance poll already set
    it. Turns could not, so there was no way to make the budget mean the turn.
    """
    quiet = make_participant("Quiet", Stance.PRO)
    quiet.tuning.allow_reasoning = False
    loud = make_participant("Loud", Stance.CON)
    quiet_provider, loud_provider = RecordingProvider(), RecordingProvider()
    repo = InMemoryChamberRepository()
    factory = StubFactory({quiet.id: quiet_provider, loud.id: loud_provider})

    budget = DebateBudget(max_rounds=1, max_total_tokens=100_000)
    await _engine(factory, repo).run(make_chamber(quiet, loud), budget)

    quiet_turns = quiet_provider.turn_options(quiet)
    loud_turns = loud_provider.turn_options(loud)
    assert quiet_turns, "the quiet debater never spoke"
    assert loud_turns, "the loud debater never spoke"
    # Per debater, not a global switch: one asked for silence, the other did not.
    assert all(o.allow_reasoning is False for o in quiet_turns)
    assert all(o.allow_reasoning is True for o in loud_turns)


async def test_a_turn_allows_reasoning_by_default() -> None:
    """Default True. Reasoning is useful in a debate — the defect is that it was
    unavoidable and unbudgeted, not that it exists — so existing chambers must
    keep behaving exactly as they did."""
    ada, bob = make_participant("Ada", Stance.PRO), make_participant("Bob", Stance.CON)
    ada_provider, bob_provider = RecordingProvider(), RecordingProvider()
    repo = InMemoryChamberRepository()
    factory = StubFactory({ada.id: ada_provider, bob.id: bob_provider})

    budget = DebateBudget(max_rounds=1, max_total_tokens=100_000)
    await _engine(factory, repo).run(make_chamber(ada, bob), budget)

    seen = ada_provider.turn_options(ada) + bob_provider.turn_options(bob)
    assert seen
    assert all(o.allow_reasoning is True for o in seen)


# --- Reasoning narration in turn content (issue #30) --------------------------

NARRATION = (
    "Okay, let me unpack this. The user wants me to continue as Castellan in a "
    "structured debate. *checks rules again* Must avoid bullet points."
)


def _debater_turns(chamber: Chamber) -> list[Turn]:
    return [t for t in chamber.turns if t.participant_id is not None]


async def test_a_turn_records_the_speech_not_the_models_narration() -> None:
    """Ollama's `think: false` does not silence every reasoning model — qwen3:30b
    answers by writing the narration into `content` instead of the separate
    `thinking` field, closed by a bare `</think>` that was never opened.

    Measured in a real run: 6 of 6 turns from that model, 16,471 characters of
    task deliberation stored as argument. `parse_stance` was taught to strip
    exactly this; the turn path never was, and it feeds the compliance judge,
    repetition detection, the moderator's transcript, exports and the UI.
    """
    ada, bob = make_participant("Ada", Stance.PRO), make_participant("Bob", Stance.CON)
    chamber = make_chamber(ada, bob)
    provider = ScriptedProvider(
        argument=f"{NARRATION}</think>\nFriends, we must act.",
        moderator_reply="STATEMENT.",
    )
    repo = InMemoryChamberRepository()
    factory = StubFactory({ada.id: provider, bob.id: provider})

    await _engine(factory, repo).run(
        chamber, DebateBudget(max_rounds=1, max_total_tokens=100_000)
    )

    turns = _debater_turns(chamber)
    assert turns
    for turn in turns:
        assert turn.content.startswith("Friends, we must act.")
        assert "Okay, let me unpack this" not in turn.content
        assert "</think>" not in turn.content


async def test_the_stripped_narration_is_kept_on_the_turn() -> None:
    """Stripped from the argument, not destroyed. `turns` is append-only and the
    narration is evidence about how the turn was produced — a silent strip is its
    own kind of unreadable record."""
    ada, bob = make_participant("Ada", Stance.PRO), make_participant("Bob", Stance.CON)
    chamber = make_chamber(ada, bob)
    provider = ScriptedProvider(
        argument=f"{NARRATION}</think>\nFriends, we must act.",
        moderator_reply="STATEMENT.",
    )
    repo = InMemoryChamberRepository()

    await _engine(StubFactory({ada.id: provider, bob.id: provider}), repo).run(
        chamber, DebateBudget(max_rounds=1, max_total_tokens=100_000)
    )

    turn = _debater_turns(chamber)[0]
    kept = turn.metadata.get(REASONING_KEY)
    assert isinstance(kept, str)
    assert "Okay, let me unpack this" in kept
    # The literal, not just the constant. `metadata` round-trips through JSON and
    # is read outside this process — exports, and anything analysing a run — so
    # the key is a wire format, and renaming the constant alone must not silently
    # change it. Same reason the suite spells "argued" out.
    assert "reasoning" in turn.metadata


async def test_a_turn_without_narration_is_untouched() -> None:
    """No key, not an empty one: a reader must be able to tell "nothing was
    stripped" from "the narration was empty"."""
    ada, bob = make_participant("Ada", Stance.PRO), make_participant("Bob", Stance.CON)
    chamber = make_chamber(ada, bob)
    provider = ScriptedProvider(argument="Friends, we must act.", moderator_reply="S.")
    repo = InMemoryChamberRepository()

    await _engine(StubFactory({ada.id: provider, bob.id: provider}), repo).run(
        chamber, DebateBudget(max_rounds=1, max_total_tokens=100_000)
    )

    turn = _debater_turns(chamber)[0]
    assert turn.content.startswith("Friends, we must act.")
    assert REASONING_KEY not in turn.metadata


async def test_a_turn_that_is_all_narration_has_no_content() -> None:
    """It said nothing. Recording the narration as the argument would put the
    model's deliberation in front of the judge, the moderator and the reader as
    though it were a speech; keeping it as content is the defect, not the fix.

    Empty content is already the engine's "this debater did not speak" state — the
    provider-failure path sets exactly that — so the existing machinery skips
    judging it and no new failure mode is introduced.
    """


    class AllNarration(ScriptedProvider):
        """Returns narration and nothing else for a turn.

        ScriptedProvider appends a distinct point to every turn so fixtures do
        not trip the repetition stop, which would leave speech after the tag —
        the one thing this test needs absent.
        """

        async def generate(
            self, messages: list[Message], options: GenerateOptions
        ) -> GenerateResult:
            before = self.turn_calls
            result = await super().generate(messages, options)
            if self.turn_calls == before:
                return result  # a stance poll or the moderator
            return GenerateResult(
                content=f"{NARRATION}</think>", prompt_tokens=5, completion_tokens=5
            )

    ada, bob = make_participant("Ada", Stance.PRO), make_participant("Bob", Stance.CON)
    chamber = make_chamber(ada, bob)
    provider = AllNarration(moderator_reply="S.")
    repo = InMemoryChamberRepository()

    await _engine(StubFactory({ada.id: provider, bob.id: provider}), repo).run(
        chamber, DebateBudget(max_rounds=1, max_total_tokens=100_000)
    )

    turn = _debater_turns(chamber)[0]
    assert turn.content == ""
    assert "Okay, let me unpack this" in str(turn.metadata.get(REASONING_KEY))


async def test_the_compliance_judge_reads_the_speech_not_the_narration() -> None:
    """The judge is asked which side a turn argues. Narration discusses both sides
    and the instructions, so judging it is judging the wrong text — and `argued`
    is recorded per turn and drives the F9 caveat."""
    seen: list[str] = []

    class Watching(ComplianceJudge):
        async def judge(self, topic: str, content: str) -> Stance | None:
            seen.append(content)
            return await super().judge(topic, content)

    ada, bob = make_participant("Ada", Stance.PRO), make_participant("Bob", Stance.CON)
    chamber = make_chamber(ada, bob)
    provider = ScriptedProvider(
        argument=f"{NARRATION}</think>\nFriends, we must act.",
        moderator_reply="STATEMENT.",
    )
    repo = InMemoryChamberRepository()
    factory = StubFactory({ada.id: provider, bob.id: provider})
    judge = Watching(MockProvider(scripted=["pro", "pro"]), "mock-small")

    await _engine(factory, repo, judge=judge).run(
        chamber, DebateBudget(max_rounds=1, max_total_tokens=100_000)
    )

    assert seen
    assert all("Okay, let me unpack this" not in text for text in seen)
