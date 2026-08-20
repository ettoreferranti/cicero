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
from cicero.core.compliance import ARGUED_KEY, ComplianceJudge
from cicero.core.consensus import ConsensusEngine, StanceReport, is_consensus
from cicero.core.prompt_builder import (
    KIND_EVIDENCE,
    KIND_MODERATOR_NOTE,
    REASONING_KEY,
    build_turn_messages,
    strip_echoed_speaker_label,
    strip_reasoning,
)
from cicero.core.repetition import (
    REPEATED,
    is_repeat,
    previous_turn_content,
    round_all_repeated,
)
from cicero.core.research import (
    MAX_SEARCHES_PER_TURN,
    EvidenceGatherer,
    ResearchSession,
    parse_search_request,
)
from cicero.core.roster import active_participants, deciding_stances
from cicero.core.state_machine import transition
from cicero.domain.enums import ChamberStatus, Stance
from cicero.domain.models import Chamber, Citation, StancePoll, Turn
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


class MuteSource(Protocol):
    """Yields queued mute/unmute changes, applied between rounds (FR-13)."""

    def drain(self) -> dict[UUID, bool]: ...


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
    """Whether every *active* participant has spoken in ``round_index``."""
    spoken = participants_spoken(chamber, round_index)
    return all(participant.id in spoken for participant in active_participants(chamber))


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
        mutes: MuteSource | None = None,
        judge: ComplianceJudge | None = None,
    ) -> None:
        self._factory = provider_factory
        self._repo = repository
        self._consensus = consensus
        self._max_transcript_turns = max_transcript_turns
        self._listener = listener
        self._evidence = evidence
        self._notes = notes
        self._mutes = mutes
        self._judge = judge

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
        final_unparsed: tuple[str, ...] = ()
        stop_reason = StopReason.MAX_ROUNDS
        budget_hit = tracker.hard_budget_hit()  # a resumed debate may be spent
        if budget_hit is not None:
            stop_reason = budget_hit

        for round_index in range(start_round, budget.max_rounds):
            if budget_hit is not None:
                break
            # Mutes land at the round boundary, never mid-round: a debater's
            # muted state is then constant for a whole round, which is what
            # keeps round completion (and so resume and step) coherent.
            self._apply_mutes(chamber)
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

            # Measured unconditionally. ``stop_on_repetition`` governs whether
            # repetition may *end* a debate on its own, not whether the engine may
            # look at it — the stance-stability check below needs the same signal,
            # and gating the measurement would let that setting silently restore the
            # defect where a flat poll ended a debate mid-argument.
            repeated = round_all_repeated(chamber, round_index)

            if tracker.may_stop_early():
                report = await self._consensus.poll_stances(chamber)
                poll = report.stances
                self._record_poll(chamber, round_index, report)
                # Everyone is polled, but only measured, unmuted debaters decide
                # whether the debate has converged (FR-13).
                if is_consensus(deciding_stances(chamber, poll, report.unparsed)):
                    stop_reason, final_stances = StopReason.CONSENSUS, poll
                    final_unparsed = report.unparsed
                    break
                if previous_poll is not None and poll == previous_poll:
                    in_converge = converge or settings.convergence_rounds == 0
                    # An unchanged poll is not convergence. The one-word stance poll
                    # is an unreliable instrument — measured across three real debates
                    # it reports `neutral` for debaters who take a clear side, and no
                    # reword or resampling fixes it (see the F7 spec). A debate ended
                    # at round 3 of 8 on three identical polls while one debater was
                    # announcing a reversal. So it only counts as finished when nobody
                    # is saying anything new either, which `repeated` measures
                    # deterministically from the text with no model judgement in it.
                    if in_converge and repeated:
                        stop_reason, final_stances = StopReason.STANCES_STABLE, poll
                        final_unparsed = report.unparsed
                        break
                    if not in_converge:
                        # Stalemate in the adversarial phase: move the convergence
                        # phase forward instead of burning more rounds on it. The
                        # guard is for clarity, not behaviour — once converging,
                        # ``round_index >= converge_start`` already holds and pushing
                        # the start forward stays true on every later round. A
                        # mutation run will report dropping it as surviving; it is
                        # equivalent, not uncovered.
                        converge_start = round_index + 1
                previous_poll = poll

            # Checked after consensus/stability, which are more informative
            # reasons when both apply: this one fires as soon as a single round
            # produces nothing new, without waiting for two matching polls.
            if settings.stop_on_repetition and repeated:
                stop_reason = StopReason.REPETITION
                break

            if tracker.rounds_exhausted():
                stop_reason = StopReason.MAX_ROUNDS
                break
            if stepped_out:
                return self._park(chamber)  # step landed on a round boundary

        if final_stances is None:
            final_report = await self._consensus.poll_stances(chamber)
            final_stances = final_report.stances
            final_unparsed = final_report.unparsed
            self._record_poll(chamber, max(tracker.rounds_completed - 1, 0), final_report)

        result = await self._consensus.finalize(chamber, final_stances, final_unparsed)
        chamber.consensus = result
        chamber.config = {
            **chamber.config,
            "stop_reason": stop_reason.value,
            "rounds_completed": tracker.rounds_completed,
            "tokens_used": tracker.tokens_used,
        }
        # A mute requested during the last round has no "next round" to land on.
        # Dropping it silently makes the API's "queued" a promise it never kept:
        # the operator sees no muted debater and no way to undo one. Applied here
        # — after the outcome is computed — so the roster records what was asked
        # without a change that never survived a full round altering the tally.
        self._apply_mutes(chamber)
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
        judge = self._judge if chamber.settings.measure_compliance else None
        # On resume, a partially completed round is finished, not repeated.
        spoken = participants_spoken(chamber, round_index)
        for participant in active_participants(chamber):
            if participant.id in spoken:
                continue
            if limit is not None and limit.reached:
                return None
            await self._drain_notes(chamber, round_index)
            previous = previous_turn_content(chamber, participant.id)
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
                # Without this a thinking model spends the turn budget reasoning
                # and the speech gets the remainder — silently, since a truncated
                # turn is stored like any other (issue #28).
                allow_reasoning=participant.tuning.allow_reasoning,
            )
            citations: list[Citation] = []
            try:
                result = await self._generate_turn(provider, messages, options, session)
                # Reasoning first: Ollama's `think: false` does not silence
                # every model — qwen3:30b writes its narration into `content`
                # instead of the separate `thinking` field, closed by a bare
                # `</think>`. That narration is not argument, and it reaches the
                # compliance judge, repetition detection, the moderator's
                # transcript, exports and the reader (issue #30). Stripped before
                # the speaker label, since the narration can carry one of its own.
                content, narration = strip_reasoning(result.content)
                # Models sometimes imitate the transcript and prefix their reply
                # with a speaker label; that is formatting, not argument.
                content = strip_echoed_speaker_label(content)
                metadata: dict[str, object] = {
                    "provider": participant.provider.value,
                    "prompt_tokens": result.prompt_tokens,
                    "completion_tokens": result.completion_tokens,
                    "phase": "converge" if converge else "open",
                }
                # Kept, not destroyed. `turns` is append-only and the narration is
                # evidence about how the turn was produced; a strip nobody can
                # audit is its own kind of unreadable record. A turn that was
                # *all* narration keeps it here with empty content — the engine's
                # existing "this debater did not speak" state, which already skips
                # judging and cannot match a repeat.
                if narration:
                    metadata[REASONING_KEY] = narration
                if session is not None and session.queries:
                    metadata["searches"] = list(session.queries)
                    citations = list(session.citations)
                # Recorded before the turn is appended, while `previous` still
                # means the speaker's last turn rather than this one.
                if is_repeat(content, previous, chamber.settings.repetition_threshold):
                    metadata[REPEATED] = True
                tracker.add_tokens(result.prompt_tokens, result.completion_tokens)
            except ProviderError as exc:
                # One failing participant must not crash the debate (NFR-R-1).
                content = ""
                metadata = {"provider": participant.provider.value, "error": str(exc)}

            # Deliberately outside the try/except above: that handler is scoped to
            # the debater's own generation failing (NFR-R-1), not the judge's. If
            # judging raised inside the try, a successful debater turn would be
            # discarded, the failure mis-attributed to the debater's provider, and
            # its tokens dropped from the budget — even though ComplianceJudge.judge
            # only swallows ProviderError, returning None, and any other exception
            # it raises is not what NFR-R-1 protects.
            # Judged before the turn is appended, so the verdict is present the
            # first time the turn reaches the SSE stream — no update event, and no
            # re-persisting a turn a reader has already seen. An empty turn (the
            # error path above) has no prose to read.
            if judge is not None and content:
                argued = await judge.judge(chamber.topic, content)
                if argued is not None:
                    metadata[ARGUED_KEY] = argued.value

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

    def _apply_mutes(self, chamber: Chamber) -> None:
        """Apply any queued mute/unmute changes to the roster (FR-13)."""
        if self._mutes is None:
            return
        changes = self._mutes.drain()
        if not changes:
            return
        for participant in chamber.participants:
            if participant.id in changes:
                participant.muted = changes[participant.id]
        self._persist(chamber)

    def _record_poll(
        self, chamber: Chamber, round_index: int, report: StanceReport
    ) -> None:
        """Persist a stance snapshot so the debate's trajectory survives (FR-25).

        The engine polls stances anyway to decide convergence; recording each
        poll is what makes "who moved, and when" answerable afterwards. A round
        is recorded once — the final poll is skipped when the round it measures
        is already in the history (which is also what keeps stepping, where each
        step is its own run, from double-recording).
        """
        if chamber.stance_history and chamber.stance_history[-1].round_index >= round_index:
            return
        chamber.stance_history.append(
            StancePoll(
                round_index=round_index,
                stances=dict(report.stances),
                unparsed=list(report.unparsed),
            )
        )
        self._persist(chamber)

    def _park(self, chamber: Chamber) -> Chamber:
        """Pause a stepped debate so the next step (or resume) continues it."""
        # Same reason as at conclusion: a parked debate may never run another
        # round, so a queued mute would otherwise be lost with the task.
        self._apply_mutes(chamber)
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
