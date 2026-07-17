"""The debate engine: the turn-based group-chat loop (FR-14/15/16/17).

Round-robin turns; each participant sees the shared transcript and argues per its
stance. The loop persists every turn, enforces budgets, and checks for
convergence, then produces the terminal consensus artifact. All collaborators
(providers, repository, consensus, clock, listener) are injected, so the engine
runs deterministically against mock providers and is in the mutation gate.
"""

from __future__ import annotations

from typing import Protocol

from cicero.core.budget import BudgetTracker, DebateBudget, StopReason
from cicero.core.consensus import ConsensusEngine, is_consensus
from cicero.core.prompt_builder import build_turn_messages
from cicero.core.state_machine import transition
from cicero.domain.enums import ChamberStatus, Stance
from cicero.domain.models import Chamber, Turn
from cicero.persistence.repository import ChamberRepository
from cicero.providers.base import GenerateOptions, ProviderError
from cicero.providers.factory import ProviderFactory

MIN_PARTICIPANTS = 2


class TurnListener(Protocol):
    """Receives each turn as it is produced (used for live streaming)."""

    async def on_turn(self, chamber: Chamber, turn: Turn) -> None: ...


class DebateEngine:
    """Runs a debate to conclusion."""

    def __init__(
        self,
        provider_factory: ProviderFactory,
        repository: ChamberRepository,
        consensus: ConsensusEngine,
        max_transcript_turns: int | None = None,
        listener: TurnListener | None = None,
    ) -> None:
        self._factory = provider_factory
        self._repo = repository
        self._consensus = consensus
        self._max_transcript_turns = max_transcript_turns
        self._listener = listener

    async def run(self, chamber: Chamber, budget: DebateBudget) -> Chamber:
        if len(chamber.participants) < MIN_PARTICIPANTS:
            raise ValueError("a debate needs at least two participants")

        self._persist(chamber)
        transition(chamber, ChamberStatus.RUNNING)
        self._persist(chamber)

        tracker = BudgetTracker(budget)
        previous_poll: dict[str, Stance] | None = None
        final_stances: dict[str, Stance] | None = None
        stop_reason = StopReason.MAX_ROUNDS

        for round_index in range(budget.max_rounds):
            budget_hit = await self._run_round(chamber, round_index, tracker)
            tracker.complete_round()
            if budget_hit:
                stop_reason = StopReason.TOKEN_BUDGET
                break

            if tracker.may_stop_early():
                poll = await self._consensus.poll_stances(chamber)
                if is_consensus(poll):
                    stop_reason, final_stances = StopReason.CONSENSUS, poll
                    break
                if previous_poll is not None and poll == previous_poll:
                    stop_reason, final_stances = StopReason.STANCES_STABLE, poll
                    break
                previous_poll = poll

            if tracker.rounds_exhausted():
                stop_reason = StopReason.MAX_ROUNDS
                break

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

    async def _run_round(
        self, chamber: Chamber, round_index: int, tracker: BudgetTracker
    ) -> bool:
        """Run one round of turns. Returns True if the token budget was hit."""
        for participant in chamber.participants:
            messages = build_turn_messages(chamber, participant, self._max_transcript_turns)
            provider = self._factory.get(participant)
            options = GenerateOptions(
                model=participant.model,
                temperature=participant.tuning.temperature,
                max_tokens=participant.tuning.max_tokens,
            )
            try:
                result = await provider.generate(messages, options)
                content = result.content
                metadata: dict[str, object] = {
                    "provider": participant.provider.value,
                    "prompt_tokens": result.prompt_tokens,
                    "completion_tokens": result.completion_tokens,
                }
                tracker.add_tokens(result.prompt_tokens, result.completion_tokens)
            except ProviderError as exc:
                # One failing participant must not crash the debate (NFR-R-1).
                content = ""
                metadata = {"provider": participant.provider.value, "error": str(exc)}

            turn = Turn(
                participant_id=participant.id,
                round_index=round_index,
                content=content,
                metadata=metadata,
            )
            chamber.turns.append(turn)
            self._persist(chamber)
            if self._listener is not None:
                await self._listener.on_turn(chamber, turn)

            if tracker.token_budget_exhausted():
                return True
        return False

    def _persist(self, chamber: Chamber) -> None:
        if self._repo.get(chamber.id) is None:
            self._repo.add(chamber)
        else:
            self._repo.update(chamber)
