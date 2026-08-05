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
``parse_directives``, ``parse_moderator_reply``, ``decide_outcome``) are in the
mutation-testing gate; the provider-driven parts are covered by tests with mock
providers.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from cicero.core import prompts
from cicero.core.prompt_builder import (
    build_moderator_messages,
    build_stance_poll_messages,
)
from cicero.core.prompts import (
    DIRECTIVE_HEADLINE,
    DIRECTIVE_WINNER,
    EMPTY_MODERATOR_STATEMENT,
)
from cicero.core.roster import deciding_stances
from cicero.domain.enums import ConsensusOutcome, DecisionRule, Stance
from cicero.domain.models import MAX_HEADLINE_LENGTH, Chamber, ConsensusResult
from cicero.providers.base import GenerateOptions, Provider, ProviderError
from cicero.providers.factory import ProviderFactory

# Reasoning models (qwen3 and friends) narrate before answering, and that
# narration argues *both* sides — scanning it would score whichever side the
# model happened to muse about first.
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_WORDS = re.compile(r"[a-z]+")

#: Accepted when the reply *is* a single word, which is what the poll asks for.
#: Safe here precisely because there is no surrounding prose to misread.
_EXACT_STANCE_WORDS: dict[str, Stance] = {
    "pro": Stance.PRO,
    "for": Stance.PRO,
    "yes": Stance.PRO,
    "support": Stance.PRO,
    "agree": Stance.PRO,
    "affirmative": Stance.PRO,
    "favour": Stance.PRO,
    "favor": Stance.PRO,
    "con": Stance.CON,
    "against": Stance.CON,
    "no": Stance.CON,
    "oppose": Stance.CON,
    "opposed": Stance.CON,
    "disagree": Stance.CON,
    "negative": Stance.CON,
    "neutral": Stance.NEUTRAL,
    "undecided": Stance.NEUTRAL,
    "unsure": Stance.NEUTRAL,
    "abstain": Stance.NEUTRAL,
}

#: Scanned inside a longer reply. Deliberately narrower: "agree" and "support"
#: usually take a *person* as their object ("I agree with SanePerson that we
#: should ban it"), and "for"/"yes"/"no" open sentences that go on to say the
#: opposite. Reading those in prose is guessing, and a wrong stance is worse
#: than an unparsed one.
_SCANNED_STANCE_WORDS: dict[str, Stance] = {
    "pro": Stance.PRO,
    "con": Stance.CON,
    "against": Stance.CON,
    "oppose": Stance.CON,
    "opposed": Stance.CON,
    "neutral": Stance.NEUTRAL,
    "undecided": Stance.NEUTRAL,
    "abstain": Stance.NEUTRAL,
}


#: A one-word answer needs very few tokens, but reasoning models spend some on
#: a <think> block first; too tight a cap truncates the answer itself.
_POLL_MAX_TOKENS = 512


@dataclass(frozen=True)
class StanceReport:
    """The result of one stance poll: the stances, and whose reply was unreadable."""

    stances: dict[str, Stance]
    #: Participant ids (as strings) whose reply could not be parsed. Their entry
    #: in ``stances`` is carried over, not measured.
    unparsed: tuple[str, ...] = ()


def parse_stance(text: str) -> Stance | None:
    """Extract a stance from a free-text self-report, or ``None`` if unclear.

    Matching is **whole-word**. Substring matching is how "prohibit" gets read
    as *pro* and "context" as *con* — silently inverting a debater who has just
    conceded. Returning ``None`` when the reply cannot be read is the honest
    answer; the caller decides what to do about it.
    """
    words = _WORDS.findall(_THINK_BLOCK.sub(" ", text).lower())
    if not words:
        return None
    if len(words) == 1:
        return _EXACT_STANCE_WORDS.get(words[0])
    for word in words:
        stance = _SCANNED_STANCE_WORDS.get(word)
        if stance is not None:
            return stance
    return None


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


#: Every directive key the moderator may open a reply with.
MODERATOR_DIRECTIVES = frozenset({DIRECTIVE_HEADLINE, DIRECTIVE_WINNER})


@dataclass(frozen=True)
class ModeratorReply:
    """A moderator reply split into its directives and its prose body."""

    #: One declarative sentence, or ``""`` when none was given or it was unusable.
    headline: str
    #: The judge's winner, or ``None`` outside a verdict (or when unreadable).
    winner: Stance | None
    #: The statement itself, with the directive lines removed.
    body: str


def parse_directives(text: str, keys: frozenset[str]) -> tuple[dict[str, str], str]:
    """Peel leading ``KEY: value`` lines off a reply, in any order.

    The moderator prompts are written independently, so the order the directives
    arrive in must not be load-bearing. Peeling stops at the first line that is
    not blank and not a recognised directive, which is what keeps ordinary prose
    — including a sentence that happens to contain a colon — out of the result.

    A blank line between two directive lines is tolerated and does *not* stop
    the peel: real Ollama models reliably put an empty line between ``HEADLINE:``
    and ``WINNER:``, and treating that blank as "the peel is over" silently
    drops ``WINNER:`` into the body, leaking it into the published statement and
    leaving the verdict without a winner. A blank line is only ever skipped
    *while more directives may still follow* (i.e. after at least one has
    already been found); once real content is seen the peel stops exactly as
    before, so a blank line does not let unrelated prose be mistaken for a
    directive. A blank line that opens the body itself *is* consumed the same
    way — it just does not matter, because ``body`` is stripped afterward.

    Returns the directives found (keys upper-cased) and the remaining body. When
    nothing remains, the original text is returned as the body: a reply that was
    *only* a directive still has to yield a statement rather than an empty one.
    """
    stripped = text.strip()
    remaining = stripped.split("\n")
    found: dict[str, str] = {}
    while remaining:
        line = remaining[0].strip()
        if not line and found:
            remaining = remaining[1:]
            continue
        key, separator, value = line.partition(":")
        candidate = key.strip().upper()
        if not separator or candidate not in keys or candidate in found:
            break
        found[candidate] = value.strip()
        remaining = remaining[1:]
    body = "\n".join(remaining).strip()
    return found, body or stripped


def parse_moderator_reply(
    text: str, keys: frozenset[str] = MODERATOR_DIRECTIVES
) -> ModeratorReply:
    """Split a moderator reply into its headline, winner and statement body.

    A recognised leading directive line is always consumed; whether its *value*
    is usable only decides whether a value is extracted. An unusable directive is
    a failed instruction, not content, and rendering ``WINNER: maybe`` at the top
    of a statement would be worse than dropping it.

    ``keys`` narrows which directives this reply is allowed to open with. Only a
    verdict actually asked for ``WINNER:``; peeling it from any other outcome
    risks eating a body sentence that happens to start the same way (e.g. "Winner:
    the pro side, because..."), silently deleting it from the published
    statement. Defaults to the full set so existing callers are unaffected.
    """
    directives, body = parse_directives(text, keys)
    headline = directives.get(DIRECTIVE_HEADLINE, "")
    if len(headline) > MAX_HEADLINE_LENGTH:
        headline = ""
    winner_word = directives.get(DIRECTIVE_WINNER)
    winner = parse_stance(winner_word) if winner_word else None
    return ModeratorReply(headline=headline, winner=winner, body=body)


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

    async def poll_stances(self, chamber: Chamber) -> StanceReport:
        """Ask each participant for its current stance.

        An unreadable reply is *reported*, not hidden. The previous poll is
        carried forward so the tally still has a value for everyone, but the id
        is listed in ``unparsed`` — otherwise a run where every poll failed
        looks exactly like a debate where nobody changed their mind, which is
        how an inverted stance can reach the final tally unnoticed.
        """
        previous = chamber.stance_history[-1].stances if chamber.stance_history else {}
        stances: dict[str, Stance] = {}
        unparsed: list[str] = []
        for participant in chamber.participants:
            key = str(participant.id)
            provider = self._factory.get(participant)
            messages = build_stance_poll_messages(chamber, participant)
            options = GenerateOptions(
                model=participant.model,
                max_tokens=_POLL_MAX_TOKENS,
                temperature=0.0,
                allow_reasoning=False,
            )
            try:
                result = await provider.generate(messages, options)
                parsed = parse_stance(result.content)
            except ProviderError:
                parsed = None
            if parsed is None:
                unparsed.append(key)
                stances[key] = previous.get(key, participant.stance)
            else:
                stances[key] = parsed
        return StanceReport(stances=stances, unparsed=tuple(unparsed))

    async def finalize(
        self,
        chamber: Chamber,
        stances: dict[str, Stance],
        unparsed: Iterable[str] = (),
    ) -> ConsensusResult:
        """Apply the chamber's decision rule and draft the final artifact.

        The rule sees only the stances that carry a vote: muted debaters are
        excluded by design (FR-13), and so are debaters whose position was never
        actually read — their value is a carried-forward assumption, and letting
        it vote would count the setup as a result. Every stance is still
        recorded on the result.
        """
        outcome, winner = decide_outcome(
            deciding_stances(chamber, stances, unparsed), chamber.settings.decision_rule
        )
        task = _moderator_task(outcome, winner)
        messages = build_moderator_messages(chamber, stances, task)
        result = await self._moderator.generate(messages, self._moderator_options)
        # Only a VERDICT reply was actually asked for a WINNER: line (see
        # _moderator_task); narrowing the key set for the other three outcomes
        # keeps a body sentence that happens to start "Winner: ..." from being
        # peeled off and silently dropped from the published statement.
        keys = (
            MODERATOR_DIRECTIVES
            if outcome is ConsensusOutcome.VERDICT
            else frozenset({DIRECTIVE_HEADLINE})
        )
        reply = parse_moderator_reply(result.content.strip(), keys=keys)
        statement = reply.body.strip() or EMPTY_MODERATOR_STATEMENT
        if outcome is ConsensusOutcome.VERDICT:
            winner = reply.winner
        return ConsensusResult(
            outcome=outcome,
            statement=statement,
            headline=reply.headline,
            winning_stance=winner,
            final_stances=dict(stances),
            unparsed=list(unparsed),
        )
