"""Export a chamber to Markdown or a JSON-ready dict (FR-31).

Pure and deterministic — part of the mutation-testing gate.
"""

from __future__ import annotations

from typing import Any

from cicero.core.prompt_builder import system_speaker_label
from cicero.domain.models import Chamber


def to_export_dict(chamber: Chamber) -> dict[str, Any]:
    """A JSON-serialisable snapshot of the whole chamber."""
    return chamber.model_dump(mode="json")


def _stance_history_header(chamber: Chamber) -> str:
    """The stance-history table header: one column per debater (FR-25)."""
    names = " | ".join(participant.display_name for participant in chamber.participants)
    return f"| Round | {names} |"


def to_markdown(chamber: Chamber) -> str:
    """Render a readable Markdown transcript of the debate."""
    lines: list[str] = [f"# Debate: {chamber.topic}", ""]
    if chamber.category.strip():
        lines.append(f"**Category:** {chamber.category}")
    if chamber.description.strip():
        lines.extend([chamber.description, ""])

    lines.append("## Participants")
    for participant in chamber.participants:
        lines.append(
            f"- **{participant.display_name}** "
            f"({participant.provider.value}/{participant.model}) — "
            f"stance: {participant.stance.value}"
        )
    lines.append("")

    lines.append("## Transcript")
    for turn in chamber.turns:
        if not turn.content.strip():
            continue
        if turn.participant_id is None:
            name = system_speaker_label(turn)
        else:
            speaker = chamber.participant_by_id(turn.participant_id)
            name = speaker.display_name if speaker is not None else "unknown"
        lines.append(f"### Round {turn.round_index + 1} — {name}")
        lines.append(turn.content)
        if turn.citations:
            lines.append("")
            lines.append("Sources:")
            for citation in turn.citations:
                label = citation.title.strip() or citation.url
                lines.append(f"- [{label}]({citation.url})")
        lines.append("")

    if chamber.stance_history:
        lines.append("## Stance history")
        lines.append(_stance_history_header(chamber))
        lines.append("|---" * (len(chamber.participants) + 1) + "|")
        for poll in chamber.stance_history:
            cells = [
                poll.stances.get(str(participant.id), participant.stance).value
                for participant in chamber.participants
            ]
            lines.append(f"| {poll.round_index + 1} | " + " | ".join(cells) + " |")
        lines.append("")

    if chamber.consensus is not None:
        lines.append(f"## Outcome: {chamber.consensus.outcome.value}")
        if chamber.consensus.winning_stance is not None:
            lines.append(f"**Winning position:** {chamber.consensus.winning_stance.value}")
            lines.append("")
        lines.append(chamber.consensus.statement)
        lines.append("")

    return "\n".join(lines)
