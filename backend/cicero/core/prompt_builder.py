"""Deterministic prompt **assembly** for debate turns and consensus (FR-15).

This module contains the *logic* that composes prompts: which fragments are
included, in what order, how the transcript is filtered and windowed, and — most
importantly for security (NFR-SEC-5) — how untrusted transcript content is wrapped
in delimiters so it is treated as **data, not instructions**. The prompt *text*
lives in ``prompts.py``. This logic module is part of the mutation-testing gate.
"""

from __future__ import annotations

import re

from cicero.core import prompts
from cicero.core.prompts import TRANSCRIPT_CLOSE, TRANSCRIPT_OPEN
from cicero.domain.enums import Stance
from cicero.domain.models import Chamber, Participant, Turn
from cicero.providers.base import Message, Role

__all__ = [
    "TRANSCRIPT_CLOSE",
    "TRANSCRIPT_OPEN",
    "build_compliance_messages",
    "build_moderator_messages",
    "build_stance_poll_messages",
    "build_turn_messages",
    "render_transcript",
    "system_speaker_label",
]

#: ``metadata["kind"]`` values for system-authored turns.
KIND_MODERATOR_NOTE = "moderator_note"
KIND_EVIDENCE = "evidence"

_SYSTEM_SPEAKERS = {
    KIND_MODERATOR_NOTE: prompts.MODERATOR_NOTE_SPEAKER,
    KIND_EVIDENCE: prompts.EVIDENCE_SPEAKER,
}


def system_speaker_label(turn: Turn) -> str:
    """The speaker label for a turn without a participant (note/evidence)."""
    kind = turn.metadata.get("kind")
    return _SYSTEM_SPEAKERS.get(str(kind), prompts.SYSTEM_SPEAKER)


#: A reasoning model's narration. Two shapes: a well-formed pair, and — the one
#: Ollama produces under ``think: false`` — a closing tag that was never opened,
#: so a matched-pair strip never fires. Measured on qwen3:30b, 6 of 6 turns in a
#: real debate arrived as ~2,800 characters of task deliberation, a bare
#: ``</think>``, then the speech.
_THINK_PAIR = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_UNOPENED = re.compile(r".*</think>", re.DOTALL | re.IGNORECASE)


def strip_reasoning(text: str) -> tuple[str, str]:
    """Split a reply into what the model said and what it was thinking.

    Returns ``(answer, narration)``. ``narration`` is ``""`` when there was none,
    which is how a caller tells "nothing was stripped" from "the narration was
    empty". An ``answer`` of ``""`` means the reply was *all* narration — the
    model thought and never spoke.

    Matched blocks go first, then any unopened remainder, so an answer written
    *before* a reasoning block survives; the greedy strip alone would swallow it.
    """
    answer = _THINK_UNOPENED.sub("", _THINK_PAIR.sub(" ", text)).strip()
    if answer == text.strip():
        return answer, ""
    return answer, text.strip()[: len(text.strip()) - len(answer)].strip()


#: A speaker label the model copied out of the transcript and into its own turn
#: — observed as a reply literally beginning "[You (pro)]: I appreciate...".
#: Anchored on the "(stance)]:" shape so ordinary bracketed prose is untouched.
_ECHOED_LABEL = re.compile(
    r"^\s*\[[^\]\n]{1,80}\((?:pro|con|neutral)\)\]\s*:\s*", re.IGNORECASE
)


def strip_echoed_speaker_label(content: str) -> str:
    """Drop a transcript speaker label the model prefixed to its own turn.

    The prompt asks models not to do this, and most do not; the ones that do
    would otherwise have it persisted, exported and shown as if they had said
    it. Repeated because a model that imitates the format once often does it
    twice.
    """
    previous = None
    while previous != content:
        previous = content
        content = _ECHOED_LABEL.sub("", content, count=1)
    return content


def _visible_turns(chamber: Chamber, viewer: Participant | None = None) -> list[tuple[str, str]]:
    """Return (speaker_label, content) for turns that carry real content.

    When rendered for a ``viewer``, that participant's own turns are labelled
    "You" — otherwise models treat their earlier statements as another
    debater's and start quoting themselves in the third person.
    """
    rows: list[tuple[str, str]] = []
    for turn in chamber.turns:
        if not turn.content.strip():
            continue  # skip empty/error turns
        if turn.participant_id is None:
            rows.append((system_speaker_label(turn), turn.content))
            continue
        speaker = chamber.participant_by_id(turn.participant_id)
        if speaker is None:
            continue
        if viewer is not None and speaker.id == viewer.id:
            rows.append((f"You ({speaker.stance.value})", turn.content))
        else:
            rows.append((f"{speaker.display_name} ({speaker.stance.value})", turn.content))
    return rows


def render_transcript(
    chamber: Chamber, max_turns: int | None = None, viewer: Participant | None = None
) -> str:
    """Render the delimited transcript block from a chamber's turns."""
    rows = _visible_turns(chamber, viewer)
    if max_turns is not None:
        rows = rows[-max_turns:]
    if not rows:
        body = prompts.EMPTY_TRANSCRIPT
    else:
        body = "\n\n".join(f"[{label}]: {content}" for label, content in rows)
    return f"{TRANSCRIPT_OPEN}\n{body}\n{TRANSCRIPT_CLOSE}"


def build_turn_messages(
    chamber: Chamber,
    participant: Participant,
    max_turns: int | None = None,
    converge: bool = False,
    research: bool = False,
) -> list[Message]:
    """Build the message list for ``participant``'s next turn.

    ``converge`` switches the prompt into the final, common-ground-seeking phase
    (FR-22): participants are told to concede, pick the strongest position, and
    propose compromises instead of opening new lines of attack. ``research``
    additionally invites the model to request a sandboxed web search via the
    ``SEARCH:`` protocol (FR-26/27).
    """
    system_parts = [
        prompts.TURN_INTRO.format(name=participant.display_name),
        prompts.MOTION_LABEL.format(topic=chamber.topic),
    ]
    if chamber.description.strip():
        system_parts.append(prompts.CONTEXT_LABEL.format(description=chamber.description))
    system_parts.append(prompts.STANCE_INSTRUCTION[participant.stance])
    if participant.tuning.persona.strip():
        system_parts.append(prompts.PERSONA_LABEL.format(persona=participant.tuning.persona))
    # Before the guidance and safety rules on purpose — they must have the last
    # word. Only reaches turn prompts: the stance poll wants one bare word, and
    # "speak in rhyme" would make it unparseable.
    if participant.tuning.instructions.strip():
        system_parts.append(
            prompts.INSTRUCTIONS_LABEL.format(instructions=participant.tuning.instructions)
        )
    system_parts.append(prompts.TURN_GUIDANCE)
    if converge:
        system_parts.append(prompts.CONVERGE_GUIDANCE)
    if research:
        system_parts.append(prompts.RESEARCH_INSTRUCTION)
    system_parts.append(prompts.FIRST_PERSON_RULE)
    system_parts.append(prompts.SAFETY_RULE)

    user_content = (
        f"{render_transcript(chamber, max_turns, viewer=participant)}"
        f"\n\n{prompts.TURN_USER_INSTRUCTION}"
    )
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
    chamber: Chamber, stances: dict[str, Stance], task: str
) -> list[Message]:
    """Build the moderator prompt that drafts the final artifact (FR-23/24).

    ``task`` is one of the ``MODERATOR_*_TASK`` texts (consensus, majority,
    judge, or disagreement), already formatted by the consensus engine.
    """
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
    user = (
        f"{render_transcript(chamber, viewer=participant)}\n\n{prompts.POLL_USER_INSTRUCTION}"
    )
    return [
        Message(role=Role.SYSTEM, content=system),
        Message(role=Role.USER, content=user),
    ]


def build_compliance_messages(topic: str, content: str) -> list[Message]:
    """Ask a judge which side one turn argues (FR-34).

    Takes the motion text and the turn text — deliberately not a ``Chamber`` and
    not a ``Participant``. The judge must not learn who wrote the turn or what
    stance they were assigned, and a signature that cannot receive those is a
    stronger guarantee than remembering not to pass them.

    ``content`` is untrusted: it is another model's output, so it goes inside the
    transcript markers that ``SAFETY_RULE`` (in ``COMPLIANCE_SYSTEM``) tells the
    judge to treat as data.
    """
    user = (
        f"{prompts.COMPLIANCE_MOTION_LABEL.format(topic=topic)}\n\n"
        f"{prompts.TRANSCRIPT_OPEN}\n{content}\n{prompts.TRANSCRIPT_CLOSE}\n\n"
        f"{prompts.COMPLIANCE_USER_INSTRUCTION}"
    )
    return [
        Message(role=Role.SYSTEM, content=prompts.COMPLIANCE_SYSTEM),
        Message(role=Role.USER, content=user),
    ]
