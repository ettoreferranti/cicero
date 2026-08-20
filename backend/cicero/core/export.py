"""Export a chamber to Markdown or a JSON-ready dict (FR-31).

Pure and deterministic — part of the mutation-testing gate.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from cicero.core.compliance import ARGUED_KEY
from cicero.core.metrics import compute_participant_metrics
from cicero.core.outcome import summarize_outcome
from cicero.core.prompt_builder import REASONING_KEY, system_speaker_label
from cicero.core.repetition import REPEATED
from cicero.domain.models import Chamber, Turn


def to_export_dict(chamber: Chamber) -> dict[str, Any]:
    """A JSON-serialisable snapshot of the whole chamber, plus its outcome summary.

    The summary is a **sibling** of the chamber rather than a key inside it, so
    the chamber remains an exact dump of the model. Nothing consumes this export
    as input, so the added key breaks no round-trip.
    """
    summary = summarize_outcome(chamber)
    return {
        "chamber": chamber.model_dump(mode="json"),
        "outcome_summary": asdict(summary)
        | {
            "movements": list(summary.movements),
            "noncompliance": list(summary.noncompliance),
        }
        if summary is not None
        else None,
    }


#: Says what the reasoning blocks are, and — the load-bearing half — what they are
#: not. The engine strips narration before a turn is stored, so no other debater,
#: the moderator, or the compliance judge ever read it (#30). Rendering it beside
#: the speech without saying so would undo exactly that distinction.
REASONING_NOTE = (
    "Reasoning blocks record what a model generated before writing its turn. "
    "They were stripped from the turn itself, so no other debater, the moderator "
    "and the compliance judge never saw them; they did not shape the debate."
)


def _quoted_block(text: str) -> str:
    """Text the app did not write, set apart without a blockquote.

    A blockquote would be the obvious markup, and it is what this used to emit.
    But a Markdown-to-PDF converter renders blockquotes in italic, so emphasis
    *inside* one becomes italic applied twice — reported from a real pipeline as
    ``could not locate "helveticaii" among embedded core font definition files``,
    helvetica + I + I. The text here is written by models and by users, and
    models reach for ``*asides*`` constantly, so the combination is not avoidable
    by asking them nicely.

    Escaping their emphasis would alter what they wrote, and this is a record, so
    the blockquote goes instead: the reasoning already sits between horizontal
    rules under its own subheading, which separates it more clearly in a PDF than
    a blockquote ever did.
    """
    return text.strip()


def _run_configuration(chamber: Chamber) -> list[str]:
    """The settings that produced this run, and how it ended.

    Two runs that differ only in `max_rounds` or in the moderator used to export
    identically, which makes an exported debate impossible to reproduce or to
    compare against another.
    """
    settings, config = chamber.settings, chamber.config
    rows: list[tuple[str, str]] = [
        ("rounds", f"min {settings.min_rounds}, max {settings.max_rounds}"),
        ("convergence rounds", str(settings.convergence_rounds)),
        ("decision rule", settings.decision_rule.value),
        ("token budget", f"{settings.max_total_tokens:,}"),
        ("web evidence", str(settings.web_evidence)),
        ("stop on repetition", str(settings.stop_on_repetition)),
    ]
    if settings.max_duration_seconds is not None:
        rows.append(("wall-clock limit", f"{settings.max_duration_seconds:g}s"))
    if chamber.moderator is not None:
        rows.append(
            (
                "moderator",
                f"{chamber.moderator.provider.value}/{chamber.moderator.model} "
                f"(max_tokens {chamber.moderator.max_tokens}, "
                f"temperature {chamber.moderator.temperature})",
            )
        )
    for label, key in (
        ("stopped because", "stop_reason"),
        ("rounds completed", "rounds_completed"),
    ):
        if config.get(key) is not None:
            rows.append((label, str(config[key])))
    tokens_used = config.get("tokens_used")
    # `config` is dict[str, object] and round-trips through JSON, so anything can
    # be under the key. A malformed record must not break an export.
    if isinstance(tokens_used, int):
        rows.append(("tokens used", f"{tokens_used:,}"))
    return [
        "## Run configuration",
        "",
        "| setting | value |",
        "|---|---|",
        *[f"| {label} | {value} |" for label, value in rows],
        "",
    ]


def _tuning_rows(chamber: Chamber) -> list[str]:
    """Per-debater generation settings, which decide how a model behaves."""
    lines = [
        "| debater | provider / model | assigned | temperature | max tokens | reasoning |",
        "|---|---|---|---|---|---|",
    ]
    for participant in chamber.participants:
        tuning = participant.tuning
        lines.append(
            f"| {participant.display_name} "
            f"| {participant.provider.value}/{participant.model} "
            f"| {participant.stance.value} | {tuning.temperature} | {tuning.max_tokens} "
            f"| {'on' if tuning.allow_reasoning else 'off'} |"
        )
    return lines


def _prompts(chamber: Chamber) -> list[str]:
    """The personas and instructions the debaters were actually given.

    Printed once when they are identical, and said to be identical: in a
    comparison where the model is the variable, that sameness is the control, and
    a reader cannot verify it from a per-debater list that happens to repeat.
    """
    lines: list[str] = []
    instructions = {p.tuning.instructions.strip() for p in chamber.participants}
    if instructions == {""}:
        pass
    elif len(instructions) == 1:
        lines += [
            "",
            "**Instructions** (identical for every debater):",
            "",
            _quoted_block(instructions.pop()),
        ]
    else:
        lines += ["", "**Instructions** (these differ by debater):", ""]
        lines += [
            f"- **{p.display_name}:** {p.tuning.instructions.strip() or '_(none)_'}"
            for p in chamber.participants
        ]
    personas = [p for p in chamber.participants if p.tuning.persona.strip()]
    if personas:
        lines += ["", "**Personas:**", ""]
        lines += [f"- **{p.display_name}:** {p.tuning.persona.strip()}" for p in personas]
    return lines


def _turn_provenance(chamber: Chamber, turn: Turn) -> str:
    """One line saying who produced a turn and what was made of it."""
    speaker = (
        chamber.participant_by_id(turn.participant_id)
        if turn.participant_id is not None
        else None
    )
    if speaker is None:
        return ""
    facts = [
        f"**{speaker.provider.value}/{speaker.model}**",
        f"assigned **{speaker.stance.value}**",
    ]
    argued = turn.metadata.get(ARGUED_KEY)
    if isinstance(argued, str):
        facts.append(f"judged as **{argued}**")
    prompt_tokens = turn.metadata.get("prompt_tokens")
    completion_tokens = turn.metadata.get("completion_tokens")
    if prompt_tokens is not None or completion_tokens is not None:
        facts.append(f"{prompt_tokens or 0}+{completion_tokens or 0} tokens")
    if turn.metadata.get(REPEATED):
        facts.append("**flagged as a repeat**")
    if turn.metadata.get("error"):
        facts.append(f"**provider error:** {turn.metadata['error']}")
    return ", ".join(facts)


def _token_use(chamber: Chamber) -> list[str]:
    metrics = compute_participant_metrics(chamber)
    if not metrics:
        return []
    lines = [
        "## Token use",
        "",
        "| debater | turns | prompt tokens | completion tokens | errors |",
        "|---|---|---|---|---|",
    ]
    for entry in metrics:
        lines.append(
            f"| {entry.display_name} | {entry.turns} | {entry.prompt_tokens:,} "
            f"| {entry.completion_tokens:,} | {entry.errors} |"
        )
    return [*lines, ""]


def _stance_history_header(chamber: Chamber) -> str:
    """The stance-history table header: one column per debater (FR-25)."""
    names = " | ".join(participant.display_name for participant in chamber.participants)
    return f"| Round | {names} |"


def to_markdown(chamber: Chamber) -> str:
    """Render the debate as a complete Markdown record.

    Everything this function *writes* is ASCII. The turns themselves are not and
    cannot be — models emit curly quotes, en dashes and accented words, and that
    is the record — but the scaffolding around them should not add characters a
    downstream converter may choke on. Measured on one real export: 219 of 287
    non-ASCII characters were inside the debaters' own prose.
    """
    lines: list[str] = [f"# Debate: {chamber.topic}", ""]
    if chamber.category.strip():
        lines.append(f"**Category:** {chamber.category}")
    if chamber.description.strip():
        lines.extend([chamber.description, ""])

    lines.extend(_run_configuration(chamber))

    lines.append("## Participants")
    lines.extend(_tuning_rows(chamber))
    lines.extend(_prompts(chamber))
    lines.append("")
    if chamber.moderator is not None:
        # Who judged is part of the record: the moderator writes the headline and,
        # on a tie, names the winning position. Its own settings are in the run
        # configuration above, next to everything else that shaped the debate.
        lines.append(
            f"**Moderator:** {chamber.moderator.provider.value}/{chamber.moderator.model}"
        )
        lines.append("")

    lines.append("## Transcript")
    if any(str(turn.metadata.get(REASONING_KEY) or "").strip() for turn in chamber.turns):
        lines.extend(["", f"*{REASONING_NOTE}*", ""])
    for turn in chamber.turns:
        if not turn.content.strip():
            continue
        if turn.participant_id is None:
            name = system_speaker_label(turn)
        else:
            speaker = chamber.participant_by_id(turn.participant_id)
            name = speaker.display_name if speaker is not None else "unknown"
        lines.append(f"### Round {turn.round_index + 1} - {name}")
        provenance = _turn_provenance(chamber, turn)
        if provenance:
            lines.extend(["", provenance, ""])
        lines.append(turn.content)
        reasoning = turn.metadata.get(REASONING_KEY)
        if isinstance(reasoning, str) and reasoning.strip():
            # A horizontal rule and a real subheading, not just bold text. Bold
            # and blockquotes survive a Markdown-to-PDF conversion too faintly to
            # separate a model's narration from the speech it wrote, and a reader
            # who cannot tell them apart is reading deliberation as argument. The
            # rule closes the block as well, so the boundary is explicit at both
            # ends rather than relying on the next turn's heading.
            lines.extend(
                [
                    "",
                    "---",
                    "",
                    "#### Model reasoning (not part of the debate)",
                    "",
                    _quoted_block(reasoning),
                    "",
                    "---",
                ]
            )
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
                "`(?)`: the debater's reply could not be read; the previous "
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
        if summary.noncompliance:
            lines.append(
                f"- **Argued against their assigned side:** "
                f"{', '.join(summary.noncompliance)}"
            )
        if summary.compliance_caveat:
            lines.append("")
            lines.append(f"*{summary.compliance_caveat}*")
        if summary.caveat:
            lines.append("")
            lines.append(f"*{summary.caveat}*")
        lines.append("")
        lines.append(chamber.consensus.statement)
        lines.append("")

    lines.extend(_token_use(chamber))
    return "\n".join(lines)
