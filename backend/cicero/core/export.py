"""Export a chamber to Markdown or a JSON-ready dict (FR-31).

Pure and deterministic — part of the mutation-testing gate.
"""

from __future__ import annotations

from typing import Any

from cicero.domain.models import Chamber


def to_export_dict(chamber: Chamber) -> dict[str, Any]:
    """A JSON-serialisable snapshot of the whole chamber."""
    return chamber.model_dump(mode="json")


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
        speaker = chamber.participant_by_id(turn.participant_id)
        name = speaker.display_name if speaker is not None else "unknown"
        lines.append(f"### Round {turn.round_index + 1} — {name}")
        lines.append(turn.content)
        lines.append("")

    if chamber.consensus is not None:
        lines.append(f"## Outcome: {chamber.consensus.outcome.value}")
        lines.append(chamber.consensus.statement)
        lines.append("")

    return "\n".join(lines)
