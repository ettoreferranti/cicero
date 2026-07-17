"""Deterministic prompt **assembly** for debate turns and consensus (FR-15).

This module contains the *logic* that composes prompts: which fragments are
included, in what order, how the transcript is filtered and windowed, and — most
importantly for security (NFR-SEC-5) — how untrusted transcript content is wrapped
in delimiters so it is treated as **data, not instructions**. The prompt *text*
lives in ``prompts.py``. This logic module is part of the mutation-testing gate.
"""

from __future__ import annotations

from cicero.core import prompts
from cicero.core.prompts import TRANSCRIPT_CLOSE, TRANSCRIPT_OPEN
from cicero.domain.enums import Stance
from cicero.domain.models import Chamber, Participant
from cicero.providers.base import Message, Role

__all__ = [
    "TRANSCRIPT_CLOSE",
    "TRANSCRIPT_OPEN",
    "build_moderator_messages",
    "build_stance_poll_messages",
    "build_turn_messages",
    "render_transcript",
]


def _visible_turns(chamber: Chamber) -> list[tuple[str, str]]:
    """Return (speaker_label, content) for turns that carry real content."""
    rows: list[tuple[str, str]] = []
    for turn in chamber.turns:
        if not turn.content.strip():
            continue  # skip empty/error turns
        speaker = chamber.participant_by_id(turn.participant_id)
        if speaker is None:
            continue
        rows.append((f"{speaker.display_name} ({speaker.stance.value})", turn.content))
    return rows


def render_transcript(chamber: Chamber, max_turns: int | None = None) -> str:
    """Render the delimited transcript block from a chamber's turns."""
    rows = _visible_turns(chamber)
    if max_turns is not None:
        rows = rows[-max_turns:]
    if not rows:
        body = prompts.EMPTY_TRANSCRIPT
    else:
        body = "\n\n".join(f"[{label}]: {content}" for label, content in rows)
    return f"{TRANSCRIPT_OPEN}\n{body}\n{TRANSCRIPT_CLOSE}"


def build_turn_messages(
    chamber: Chamber, participant: Participant, max_turns: int | None = None
) -> list[Message]:
    """Build the message list for ``participant``'s next turn."""
    system_parts = [
        prompts.TURN_INTRO.format(name=participant.display_name),
        prompts.MOTION_LABEL.format(topic=chamber.topic),
    ]
    if chamber.description.strip():
        system_parts.append(prompts.CONTEXT_LABEL.format(description=chamber.description))
    system_parts.append(prompts.STANCE_INSTRUCTION[participant.stance])
    if participant.tuning.persona.strip():
        system_parts.append(prompts.PERSONA_LABEL.format(persona=participant.tuning.persona))
    system_parts.append(prompts.TURN_GUIDANCE)
    system_parts.append(prompts.SAFETY_RULE)

    user_content = f"{render_transcript(chamber, max_turns)}\n\n{prompts.TURN_USER_INSTRUCTION}"
    return [
        Message(role=Role.SYSTEM, content="\n\n".join(system_parts)),
        Message(role=Role.USER, content=user_content),
    ]


def _stance_tally(chamber: Chamber, stances: dict[str, Stance]) -> str:
    """A readable list of each participant's final stance."""
    lines = []
    for participant in chamber.participants:
        stance = stances.get(str(participant.id), participant.stance)
        lines.append(f"- {participant.display_name}: {stance.value}")
    return "\n".join(lines)


def build_moderator_messages(
    chamber: Chamber, stances: dict[str, Stance], is_consensus: bool
) -> list[Message]:
    """Build the moderator prompt that drafts the final artifact (FR-23/24)."""
    task = prompts.MODERATOR_CONSENSUS_TASK if is_consensus else prompts.MODERATOR_DISAGREEMENT_TASK
    user = (
        f"{prompts.MODERATOR_MOTION_LABEL.format(topic=chamber.topic)}\n\n"
        f"{render_transcript(chamber)}\n\n"
        f"{prompts.MODERATOR_POSITIONS_LABEL}\n{_stance_tally(chamber, stances)}\n\n"
        f"{task}"
    )
    return [
        Message(role=Role.SYSTEM, content=prompts.MODERATOR_SYSTEM),
        Message(role=Role.USER, content=user),
    ]


def build_stance_poll_messages(chamber: Chamber, participant: Participant) -> list[Message]:
    """Build a short poll asking a participant for its current stance (FR-25)."""
    system = prompts.POLL_SYSTEM.format(name=participant.display_name, topic=chamber.topic)
    user = f"{render_transcript(chamber)}\n\n{prompts.POLL_USER_INSTRUCTION}"
    return [
        Message(role=Role.SYSTEM, content=system),
        Message(role=Role.USER, content=user),
    ]
