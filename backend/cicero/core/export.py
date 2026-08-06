"""Export a chamber to Markdown or a JSON-ready dict (FR-31).

Pure and deterministic — part of the mutation-testing gate.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from cicero.core.outcome import summarize_outcome
from cicero.core.prompt_builder import system_speaker_label
from cicero.domain.models import Chamber


def to_export_dict(chamber: Chamber) -> dict[str, Any]:
    """A JSON-serialisable snapshot of the whole chamber, plus its outcome summary.

    The summary is a **sibling** of the chamber rather than a key inside it, so
    the chamber remains an exact dump of the model. Nothing consumes this export
    as input, so the added key breaks no round-trip.
    """
    summary = summarize_outcome(chamber)
    return {
        "chamber": chamber.model_dump(mode="json"),
        "outcome_summary": asdict(summary) | {"movements": list(summary.movements)}
        if summary is not None
        else None,
    }


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
    if chamber.moderator is not None:
        # Who judged is part of the record: the moderator writes the headline and,
        # on a tie, names the winning position.
        lines.append(
            f"**Moderator:** {chamber.moderator.provider.value}/{chamber.moderator.model}"
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
            cells = []
            for participant in chamber.participants:
                key = str(participant.id)
                value = poll.stances.get(key, participant.stance).value
                # Mark what was carried over rather than measured, so a reader
                # does not mistake a parse failure for a settled position.
                cells.append(f"{value} (?)" if key in poll.unparsed else value)
            lines.append(f"| {poll.round_index + 1} | " + " | ".join(cells) + " |")
        if any(poll.unparsed for poll in chamber.stance_history):
            lines.append("")
            lines.append(
                "`(?)` — the debater's reply could not be read; the previous "
                "value was carried forward and is not evidence of their position."
            )
        lines.append("")

    summary = summarize_outcome(chamber)
    if chamber.consensus is not None and summary is not None:
        if chamber.consensus.headline:
            lines.append("## Outcome")
            lines.append("")
            lines.append(f"**The chamber concluded:** {chamber.consensus.headline}")
        else:
            # No usable headline: fall back to the pre-F5 shape rather than
            # showing a heading with nothing under it.
            lines.append(f"## Outcome: {chamber.consensus.outcome.value}")
            if chamber.consensus.winning_stance is not None:
                lines.append(
                    f"**Winning position:** {chamber.consensus.winning_stance.value}"
                )
        lines.append("")
        lines.append(f"- **Support:** {summary.support}")
        lines.append(f"- **How decided:** {summary.decided_by}")
        if summary.movements:
            # "Recorded stance changes", not "Positions moved": this is what the
            # one-word poll captured, which is not the same claim as a description
            # of where the debater actually ended up.
            lines.append(f"- **Recorded stance changes:** {', '.join(summary.movements)}")
        if summary.caveat:
            lines.append("")
            lines.append(f"*{summary.caveat}*")
        lines.append("")
        lines.append(chamber.consensus.statement)
        lines.append("")

    return "\n".join(lines)
