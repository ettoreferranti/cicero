"""Detecting a debater who has run out of things to say (FR-16).

Models converge, and then they restate. Observed with qwen3 on a settled
transcript: it re-emitted its previous turn nearly word for word, and on a long
enough debate byte for byte. Those rounds cost full price and add nothing.

A repeat is therefore worth two things: recording it on the turn, so a reader
can see the debate stopped progressing, and — when every active debater does it
in the same round — ending the debate, because nobody has anything left.

Matching is near-identity, not exact: a model that changes one clause is still
repeating itself. The threshold is per-chamber so an operator can tighten it to
1.0 (byte-identical only) or loosen it.

Pure and deterministic, so it sits in the mutation gate.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from uuid import UUID

from cicero.core.roster import active_participants
from cicero.domain.models import Chamber, Turn

#: Marks a turn that restates the speaker's previous one.
REPEATED = "repeated"


def normalise(text: str) -> str:
    """Casefold and collapse whitespace, so formatting noise is not a difference."""
    return " ".join(text.split()).casefold()


def similarity(first: str, second: str) -> float:
    """How alike two turns are, 0.0 to 1.0, ignoring case and whitespace."""
    left, right = normalise(first), normalise(second)
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def is_repeat(content: str, previous: str | None, threshold: float) -> bool:
    """Whether ``content`` restates ``previous`` closely enough to count.

    An empty turn is never a repeat: it is a provider failure, already recorded
    as an error, and treating it as "nothing new to say" would end debates on
    an outage.
    """
    if previous is None or not content.strip():
        return False
    return similarity(content, previous) >= threshold


def previous_turn_content(chamber: Chamber, participant_id: UUID) -> str | None:
    """The speaker's most recent non-empty turn, or ``None`` if they have none."""
    for turn in reversed(chamber.turns):
        if turn.participant_id == participant_id and turn.content.strip():
            return turn.content
    return None


def round_turns(chamber: Chamber, round_index: int) -> dict[UUID, Turn]:
    """Participant turns in ``round_index``, keyed by speaker."""
    return {
        turn.participant_id: turn
        for turn in chamber.turns
        if turn.participant_id is not None and turn.round_index == round_index
    }


def round_all_repeated(chamber: Chamber, round_index: int) -> bool:
    """Whether every active debater merely restated themselves this round.

    False for an empty roster, and false unless *every* active debater spoke and
    repeated — one participant still making progress is a live debate.
    """
    active = active_participants(chamber)
    if not active:
        return False
    turns = round_turns(chamber, round_index)
    return all(
        (turn := turns.get(participant.id)) is not None
        and bool(turn.metadata.get(REPEATED))
        for participant in active
    )
