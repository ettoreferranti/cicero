"""The debate engine: the turn-based group-chat loop (FR-14/15/16/17/19).

Round-robin turns; each participant sees the shared transcript and argues per its
stance. The loop persists every turn, enforces budgets (rounds, tokens, wall
clock), and checks for convergence, then produces the terminal consensus
artifact.

The debate runs in two phases: an adversarial phase, then a **convergence
phase** for the last ``settings.convergence_rounds`` rounds, where prompts steer
participants toward concessions and a common position (FR-22). If stances
stabilise early in the adversarial phase, the engine skips straight to the
convergence phase instead of burning rounds on a stalemate.

All collaborators (providers, repository, consensus, listener, evidence
gatherer, note source) are injected, so the engine runs deterministically
against mock providers and is in the mutation gate.
"""

from __future__ import annotations

import asyncio
from typing import Protocol
from uuid import UUID

from cicero.core import prompts
from cicero.core.budget import BudgetTracker, DebateBudget, StopReason
from cicero.core.consensus import ConsensusEngine, is_consensus
from cicero.core.prompt_builder import (
    KIND_EVIDENCE,
    KIND_MODERATOR_NOTE,
    build_turn_messages,
)
from cicero.core.research import (
    MAX_SEARCHES_PER_TURN,
    EvidenceGatherer,
    ResearchSession,
    parse_search_request,
)
from cicero.core.state_machine import transition
from cicero.domain.enums import ChamberStatus, Stance
from cicero.domain.models import Chamber, Citation, Turn
from cicero.persistence.repository import ChamberRepository
from cicero.providers.base import (
    GenerateOptions,
    GenerateResult,
    Message,
    Provider,
    ProviderError,
    Role,
)
from cicero.providers.factory import ProviderFactory

MIN_PARTICIPANTS = 2

EVIDENCE_HEADER = "Background evidence gathered from the web (with sources):"


class TurnListener(Protocol):
    """Receives each turn as it is produced (used for live streaming)."""

    async def on_turn(self, chamber: Chamber, turn: Turn) -> None: ...


class NoteSource(Protocol):
    """Yields moderator notes queued for injection between turns (FR-21)."""

    def drain(self) -> list[str]: ...


def tokens_spent(chamber: Chamber) -> int:
    """Tokens recorded on persisted turns — seeds the budget on resume (NFR-R-3)."""
    total = 0
    for turn in chamber.turns:
        for key in ("prompt_tokens", "completion_tokens"):
            value = turn.metadata.get(key, 0)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                total += value
    return total


def participants_spoken(chamber: Chamber, round_index: int) -> set[UUID]:
    """Ids of participants that already have a turn in ``round_index``."""
    return {
        turn.participant_id
        for turn in chamber.turns
        if turn.participant_id is not None and turn.round_index == round_index
    }


def round_complete(chamber: Chamber, round_index: int) -> bool:
    """Whether every participant has spoken in ``round_index``."""
    spoken = participants_spoken(chamber, round_index)
    return all(participant.id in spoken for participant in chamber.participants)


def resume_round(chamber: Chamber) -> int:
    """The round a (resumed or stepped) debate should continue from.

    The last round with participant turns, if someone still hasn't spoken in
    it; otherwise the next round. A chamber with no participant turns starts
    at round 0, so fresh debates take the same path.
    """
    rounds = [
        turn.round_index for turn in chamber.turns if turn.participant_id is not None
    ]
    if not rounds:
        return 0
    last = max(rounds)
    return last + 1 if round_complete(chamber, last) else last


class TurnLimit:
    """Caps how many participant turns one run produces — the step control (FR-19).

    A run given a limit stops once its allowance is spent and parks the chamber
    as ``paused`` instead of concluding it. Stepping (or resuming) again picks up
    exactly where it left off, because the engine already finishes a partially
    completed round on resume.
    """

    def __init__(self, turns: int) -> None:
        if turns < 1:
            raise ValueError("a step must run at least one turn")
        self.remaining = turns

    @property
    def reached(self) -> bool:
        """Whether the allowance is spent."""
        return self.remaining <= 0

    def consume(self) -> None:
        self.remaining -= 1


def format_evidence_content(citations: list[Citation]) -> str:
    """Render gathered evidence as a readable, source-attributed block."""
    blocks = []
    for citation in citations:
        label = citation.title.strip() or citation.url
        blocks.append(f"[{label}]\n{citation.excerpt}\n(Source: {citation.url})")
    return EVIDENCE_HEADER + "\n\n" + "\n\n".join(blocks)


class DebateEngine:
    """Runs a debate to conclusion."""

    def __init__(
        self,
        provider_factory: ProviderFactory,
        repository: ChamberRepository,
        consensus: ConsensusEngine,
        max_transcript_turns: int | None = None,
        listener: TurnListener | None = None,
        evidence: EvidenceGatherer | None = None,
        notes: NoteSource | None = None,
    ) -> None:
        self._factory = provider_factory
        self._repo = repository
        self._consensus = consensus
        self._max_transcript_turns = max_transcript_turns
        self._listener = listener
        self._evidence = evidence
        self._notes = notes

    async def run(
        self,
        chamber: Chamber,
        budget: DebateBudget,
        turn_limit: TurnLimit | None = None,
    ) -> Chamber:
        """Run a debate to conclusion — from `draft`, or resumed from `paused`
        (NFR-R-3): the loop continues at the first round any participant has
        not yet spoken in, and prior token/round spend still counts against
        the budget.

        With a ``turn_limit`` the run instead stops after that many participant
        turns and parks the chamber as `paused` — the step control (FR-19). A
        stepped debate still concludes normally once its rounds or budget run
        out, or when a round boundary lands on consensus.
        """
        if len(chamber.participants) < MIN_PARTICIPANTS:
            raise ValueError("a debate needs at least two participants")

        self._persist(chamber)
        transition(chamber, ChamberStatus.RUNNING)
        self._persist(chamber)
        try:
            return await self._run_to_conclusion(chamber, budget, turn_limit)
        except asyncio.CancelledError:
            # A stopped/interrupted debate parks as `paused`, so it can be
            # resumed instead of wedging in `running` (E5/J3).
            if chamber.status is ChamberStatus.RUNNING:
                transition(chamber, ChamberStatus.PAUSED)
                self._persist(chamber)
            raise

    async def _run_to_conclusion(
        self, chamber: Chamber, budget: DebateBudget, limit: TurnLimit | None = None
    ) -> Chamber:
        await self._inject_evidence(chamber)

        settings = chamber.settings
        start_round = min(resume_round(chamber), budget.max_rounds)
        tracker = BudgetTracker(
            budget,
            initial_tokens=tokens_spent(chamber),
            initial_rounds=start_round,
        )
        # First round of the convergence phase; == max_rounds means "never".
        converge_start = budget.max_rounds - min(settings.convergence_rounds, budget.max_rounds)
        if settings.convergence_rounds == 0:
            converge_start = budget.max_rounds

        previous_poll: dict[str, Stance] | None = None
        final_stances: dict[str, Stance] | None = None
        stop_reason = StopReason.MAX_ROUNDS
        budget_hit = tracker.hard_budget_hit()  # a resumed debate may be spent
        if budget_hit is not None:
            stop_reason = budget_hit

        for round_index in range(start_round, budget.max_rounds):
            if budget_hit is not None:
                break
            converge = round_index >= converge_start
            budget_hit = await self._run_round(chamber, round_index, tracker, converge, limit)
            # A spent step allowance parks the debate — unless a budget was hit
            # too, in which case the debate is over and should conclude instead.
            stepped_out = limit is not None and limit.reached and budget_hit is None
            if stepped_out and not round_complete(chamber, round_index):
                return self._park(chamber)  # mid-round: no round to count yet
            tracker.complete_round()
            if budget_hit is not None:
                stop_reason = budget_hit
                break

            if tracker.may_stop_early():
                poll = await self._consensus.poll_stances(chamber)
                if is_consensus(poll):
                    stop_reason, final_stances = StopReason.CONSENSUS, poll
                    break
                if previous_poll is not None and poll == previous_poll:
                    if converge or settings.convergence_rounds == 0:
                        stop_reason, final_stances = StopReason.STANCES_STABLE, poll
                        break
                    # Stalemate in the adversarial phase: move the convergence
                    # phase forward instead of burning more rounds on it.
                    converge_start = round_index + 1
                previous_poll = poll

            if tracker.rounds_exhausted():
                stop_reason = StopReason.MAX_ROUNDS
                break
            if stepped_out:
                return self._park(chamber)  # step landed on a round boundary

        if final_stances is None:
            final_stances = await self._consensus.poll_stances(chamber)

        result = await self._consensus.finalize(chamber, final_stances)
        chamber.consensus = result
        chamber.config = {
            **chamber.config,
            "stop_reason": stop_reason.value,
            "rounds_completed": tracker.rounds_completed,
            "tokens_used": tracker.tokens_used,
        }
        transition(chamber, ChamberStatus.CONCLUDED)
        self._persist(chamber)
        return chamber

    async def _inject_evidence(self, chamber: Chamber) -> None:
        """Gather web evidence once, up front, and add it as a cited system turn.

        Opt-in per chamber (FR-26); the evidence text is untrusted and enters
        prompts only inside the delimited transcript block (NFR-SEC-5). A
        gathering failure never stops the debate.
        """
        if not chamber.settings.web_evidence or self._evidence is None:
            return
        if any(turn.metadata.get("kind") == KIND_EVIDENCE for turn in chamber.turns):
            return  # resumed debate: the brief was already gathered
        try:
            citations = await self._evidence.gather(chamber.topic)
        except Exception:  # noqa: BLE001 (evidence is best-effort)
            return
        if not citations:
            return
        turn = Turn(
            participant_id=None,
            round_index=0,
            content=format_evidence_content(citations),
            citations=citations,
            metadata={"kind": KIND_EVIDENCE},
        )
        await self._append_turn(chamber, turn)

    async def _drain_notes(self, chamber: Chamber, round_index: int) -> None:
        """Inject any queued moderator notes as system turns (FR-21)."""
        if self._notes is None:
            return
        for note in self._notes.drain():
            turn = Turn(
                participant_id=None,
                round_index=round_index,
                content=note,
                metadata={"kind": KIND_MODERATOR_NOTE},
            )
            await self._append_turn(chamber, turn)

    async def _run_round(
        self,
        chamber: Chamber,
        round_index: int,
        tracker: BudgetTracker,
        converge: bool,
        limit: TurnLimit | None = None,
    ) -> StopReason | None:
        """Run one round of turns. Returns the hard budget hit, if any.

        A stepped run (``limit``) stops as soon as its turn allowance is spent,
        leaving the round partly done for the next step to finish.
        """
        gatherer = self._evidence if chamber.settings.web_evidence else None
        # On resume, a partially completed round is finished, not repeated.
        spoken = participants_spoken(chamber, round_index)
        for participant in chamber.participants:
            if participant.id in spoken:
                continue
            if limit is not None and limit.reached:
                return None
            await self._drain_notes(chamber, round_index)
            session = ResearchSession(gatherer) if gatherer is not None else None
            messages = build_turn_messages(
                chamber,
                participant,
                self._max_transcript_turns,
                converge=converge,
                research=session is not None,
            )
            provider = self._factory.get(participant)
            options = GenerateOptions(
                model=participant.model,
                temperature=participant.tuning.temperature,
                max_tokens=participant.tuning.max_tokens,
            )
            citations: list[Citation] = []
            try:
                result = await self._generate_turn(provider, messages, options, session)
                content = result.content
                metadata: dict[str, object] = {
                    "provider": participant.provider.value,
                    "prompt_tokens": result.prompt_tokens,
                    "completion_tokens": result.completion_tokens,
                    "phase": "converge" if converge else "open",
                }
                if session is not None and session.queries:
                    metadata["searches"] = list(session.queries)
                    citations = list(session.citations)
                tracker.add_tokens(result.prompt_tokens, result.completion_tokens)
            except ProviderError as exc:
                # One failing participant must not crash the debate (NFR-R-1).
                content = ""
                metadata = {"provider": participant.provider.value, "error": str(exc)}

            turn = Turn(
                participant_id=participant.id,
                round_index=round_index,
                content=content,
                citations=citations,
                metadata=metadata,
            )
            await self._append_turn(chamber, turn)
            if limit is not None:
                limit.consume()

            budget_hit = tracker.hard_budget_hit()
            if budget_hit is not None:
                return budget_hit
        return None

    async def _generate_turn(
        self,
        provider: Provider,
        messages: list[Message],
        options: GenerateOptions,
        session: ResearchSession | None,
    ) -> GenerateResult:
        """Generate one turn, letting the model research first when enabled.

        Providers with native tool use run their own search loop; everyone else
        gets the universal ``SEARCH:`` text protocol. Both call back into the
        session, so every query goes through the sandboxed gatherer and is
        recorded for per-turn attribution (FR-28).
        """
        if session is None:
            return await provider.generate(messages, options)
        if provider.supports_native_search:
            return await provider.generate_with_search(
                messages, options, session.run, MAX_SEARCHES_PER_TURN
            )
        return await self._text_protocol_turn(provider, messages, options, session)

    async def _text_protocol_turn(
        self,
        provider: Provider,
        messages: list[Message],
        options: GenerateOptions,
        session: ResearchSession,
    ) -> GenerateResult:
        """The ``SEARCH:`` fallback for providers without native tool use."""
        prompt_tokens = completion_tokens = 0

        async def generate(current: list[Message]) -> GenerateResult:
            nonlocal prompt_tokens, completion_tokens
            result = await provider.generate(current, options)
            prompt_tokens += result.prompt_tokens
            completion_tokens += result.completion_tokens
            return result

        result = await generate(messages)
        for _ in range(MAX_SEARCHES_PER_TURN):
            query = parse_search_request(result.content)
            if query is None:
                break
            results_block = await session.run(query)
            messages = [
                *messages,
                Message(role=Role.ASSISTANT, content=result.content),
                Message(role=Role.USER, content=f"{results_block}\n\n{prompts.SEARCH_FOLLOWUP}"),
            ]
            result = await generate(messages)
        if parse_search_request(result.content) is not None:
            # Search budget spent but the model asked again: demand the argument.
            messages = [
                *messages,
                Message(role=Role.ASSISTANT, content=result.content),
                Message(role=Role.USER, content=prompts.NO_MORE_SEARCHES),
            ]
            result = await generate(messages)
        return GenerateResult(
            content=result.content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            metadata=result.metadata,
        )

    def _park(self, chamber: Chamber) -> Chamber:
        """Pause a stepped debate so the next step (or resume) continues it."""
        transition(chamber, ChamberStatus.PAUSED)
        self._persist(chamber)
        return chamber

    async def _append_turn(self, chamber: Chamber, turn: Turn) -> None:
        chamber.turns.append(turn)
        self._persist(chamber)
        if self._listener is not None:
            await self._listener.on_turn(chamber, turn)

    def _persist(self, chamber: Chamber) -> None:
        if self._repo.get(chamber.id) is None:
            self._repo.add(chamber)
        else:
            self._repo.update(chamber)
