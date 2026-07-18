"""Hybrid consensus: deterministic stance signal + moderator synthesis (§6, FR-22/23/24).

- A cheap, deterministic **stance parse** turns each participant's self-report
  into a :class:`Stance`.
- ``is_consensus`` / ``majority_stance`` are pure rules over those stances.
- The chamber's **decision rule** (FR-22, D-settings) picks how a non-unanimous
  debate resolves: disagreement, majority, or a moderator-as-judge verdict, so a
  debate can always end with one position winning.
- The **moderator** (an injected provider) drafts the final Consensus Statement,
  Resolution, Verdict, or Summary of Disagreement.

The pure parts (``parse_stance``, ``is_consensus``, ``majority_stance``,
``parse_verdict``, ``decide_outcome``) are in the mutation-testing gate; the
provider-driven parts are covered by tests with mock providers.
"""

from __future__ import annotations

from collections import Counter

from cicero.core import prompts
from cicero.core.prompt_builder import (
    build_moderator_messages,
    build_stance_poll_messages,
)
from cicero.core.prompts import EMPTY_MODERATOR_STATEMENT, VERDICT_WINNER_PREFIX
from cicero.domain.enums import ConsensusOutcome, DecisionRule, Stance
from cicero.domain.models import Chamber, ConsensusResult
from cicero.providers.base import GenerateOptions, Provider, ProviderError
from cicero.providers.factory import ProviderFactory

# Longest-first so "neutral" is matched before a substring could shadow it.
_STANCE_KEYWORDS: tuple[tuple[str, Stance], ...] = (
    ("neutral", Stance.NEUTRAL),
    ("con", Stance.CON),
    ("pro", Stance.PRO),
)


def parse_stance(text: str) -> Stance | None:
    """Extract a stance from a free-text self-report, or ``None`` if unclear."""
    lowered = text.lower()
    found: tuple[int, Stance] | None = None
    for keyword, stance in _STANCE_KEYWORDS:
        index = lowered.find(keyword)
        if index != -1 and (found is None or index < found[0]):
            found = (index, stance)
    return found[1] if found is not None else None


def is_consensus(stances: dict[str, Stance]) -> bool:
    """True when every participant reports the same stance (and there is at least one)."""
    if not stances:
        return False
    return len(set(stances.values())) == 1


def majority_stance(stances: dict[str, Stance]) -> Stance | None:
    """The strict plurality winner among final stances, or ``None`` on a tie/empty."""
    if not stances:
        return None
    counts = Counter(stances.values())
    ranked = counts.most_common(2)
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None
    return ranked[0][0]


def parse_verdict(text: str) -> tuple[Stance | None, str]:
    """Split a judge reply into (winning stance, statement body).

    The judge is instructed to open with ``WINNER: pro|con|neutral``. If that
    line is missing or unparsable the whole text is returned with no winner.
    """
    stripped = text.strip()
    first, _, rest = stripped.partition("\n")
    if first.strip().upper().startswith(VERDICT_WINNER_PREFIX):
        word = first.strip()[len(VERDICT_WINNER_PREFIX) :].strip()
        winner = parse_stance(word)
        if winner is not None:
            body = rest.strip() or stripped
            return winner, body
    return None, stripped


def decide_outcome(
    stances: dict[str, Stance], rule: DecisionRule
) -> tuple[ConsensusOutcome, Stance | None]:
    """Apply the chamber's decision rule to the final stances (pure).

    Returns the outcome and the winning stance. A ``JUDGE`` rule with a tie
    returns ``(VERDICT, None)`` — the caller must ask the moderator-judge to
    name the winner.
    """
    if is_consensus(stances):
        return ConsensusOutcome.CONSENSUS, next(iter(stances.values()))
    if rule is DecisionRule.UNANIMOUS:
        return ConsensusOutcome.DISAGREEMENT, None
    winner = majority_stance(stances)
    if winner is not None:
        return ConsensusOutcome.MAJORITY, winner
    if rule is DecisionRule.JUDGE:
        return ConsensusOutcome.VERDICT, None
    return ConsensusOutcome.DISAGREEMENT, None


def _moderator_task(outcome: ConsensusOutcome, winner: Stance | None) -> str:
    """The moderator task text for a decided outcome."""
    if outcome is ConsensusOutcome.CONSENSUS:
        return prompts.MODERATOR_CONSENSUS_TASK
    if outcome is ConsensusOutcome.MAJORITY and winner is not None:
        return prompts.MODERATOR_MAJORITY_TASK.format(winner=winner.value)
    if outcome is ConsensusOutcome.VERDICT:
        return prompts.MODERATOR_JUDGE_TASK
    return prompts.MODERATOR_DISAGREEMENT_TASK


class ConsensusEngine:
    """Polls stances and produces the terminal consensus artifact."""

    def __init__(
        self,
        provider_factory: ProviderFactory,
        moderator: Provider,
        moderator_options: GenerateOptions,
    ) -> None:
        self._factory = provider_factory
        self._moderator = moderator
        self._moderator_options = moderator_options

    async def poll_stances(self, chamber: Chamber) -> dict[str, Stance]:
        """Ask each participant for its current stance; fall back to the last known."""
        stances: dict[str, Stance] = {}
        for participant in chamber.participants:
            provider = self._factory.get(participant)
            messages = build_stance_poll_messages(chamber, participant)
            options = GenerateOptions(model=participant.model, max_tokens=8, temperature=0.0)
            try:
                result = await provider.generate(messages, options)
                parsed = parse_stance(result.content)
            except ProviderError:
                parsed = None
            stances[str(participant.id)] = parsed if parsed is not None else participant.stance
        return stances

    async def finalize(
        self, chamber: Chamber, stances: dict[str, Stance]
    ) -> ConsensusResult:
        """Apply the chamber's decision rule and draft the final artifact."""
        outcome, winner = decide_outcome(stances, chamber.settings.decision_rule)
        task = _moderator_task(outcome, winner)
        messages = build_moderator_messages(chamber, stances, task)
        result = await self._moderator.generate(messages, self._moderator_options)
        statement = result.content.strip() or EMPTY_MODERATOR_STATEMENT
        if outcome is ConsensusOutcome.VERDICT:
            winner, body = parse_verdict(statement)
            statement = body or EMPTY_MODERATOR_STATEMENT
        return ConsensusResult(
            outcome=outcome,
            statement=statement,
            winning_stance=winner,
            final_stances=dict(stances),
        )
