"""Write the compliance judge's reading of real turns, for hand-checking.

Reads one or more existing chambers from the API and judges each debate turn
with the real ``ComplianceJudge`` (see ``cicero.core.compliance``). It writes
nothing to the database and mutates no chamber — it only produces two local
files, deliberately kept apart:

- ``--turns-out``: Markdown, one section per judged turn (id, round, speaker,
  assigned stance, full turn text). **Never contains the judge's verdict.**
- ``--verdicts-out``: JSON, a mapping of the same turn id to the judge's
  answer (``"pro"``, ``"con"``, ``"neutral"``, or ``null`` when unreadable).

The split exists so a turn can be hand-read without the judge's answer
anchoring that reading. A keyword classifier for this exact task was wrong by
a factor of seven against hand-reading (see the decision record at
``docs/superpowers/decisions/2026-08-06-debaters-do-not-hold-assigned-sides.md``),
matching words like "essential" inside passages *rebutting* the pro case — a
one-table printout that shows the verdict alongside the excerpt invites the
same mistake by letting the verdict bias the reading instead of checking it.
Judge the turns file on its own, record your own answers, and only then open
the verdicts file to compare.

    python scripts/check_compliance_judge.py <chamber-id> [<chamber-id> ...] \\
        --model llama3.1:latest \\
        --turns-out /tmp/turns.md --verdicts-out /tmp/verdicts.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from cicero.core.compliance import ComplianceJudge
from cicero.domain.enums import ProviderType
from cicero.domain.models import Chamber
from cicero.providers.factory import ProviderFactory, SettingsProviderFactory


def _write_turns_md(path: Path, entries: list[dict[str, Any]]) -> None:
    """Write the turns file: no verdict anywhere in it, by construction."""
    lines: list[str] = []
    for entry in entries:
        lines.append(f"## Turn {entry['turn_id']}")
        lines.append("")
        lines.append(f"- chamber: {entry['chamber_id']}")
        lines.append(f"- motion: {entry['topic']}")
        lines.append(f"- round: {entry['round_index']}")
        lines.append(f"- speaker: {entry['speaker']}")
        lines.append(f"- assigned stance: {entry['assigned_stance']}")
        lines.append("")
        lines.append(entry["content"])
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_verdicts_json(path: Path, verdicts: dict[str, str | None]) -> None:
    path.write_text(json.dumps(verdicts, indent=2, sort_keys=True) + "\n", encoding="utf-8")


async def _judge_chamber(
    client: httpx.Client, chamber_id: str, judge: ComplianceJudge
) -> tuple[list[dict[str, Any]], dict[str, str | None]]:
    response = client.get(f"/chambers/{chamber_id}")
    response.raise_for_status()
    chamber = Chamber.model_validate(response.json())

    entries: list[dict[str, Any]] = []
    verdicts: dict[str, str | None] = {}
    for turn in chamber.turns:
        if turn.participant_id is None or not turn.content:
            continue
        speaker = chamber.participant_by_id(turn.participant_id)
        if speaker is None:
            continue
        turn_id = str(turn.id)
        entries.append(
            {
                "turn_id": turn_id,
                "chamber_id": str(chamber.id),
                "topic": chamber.topic,
                "round_index": turn.round_index,
                "speaker": speaker.display_name,
                "assigned_stance": speaker.stance.value,
                "content": turn.content,
            }
        )
        judgement = await judge.judge(chamber.topic, turn.content)
        verdicts[turn_id] = judgement.stance.value if judgement else None
    return entries, verdicts


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("chamber_ids", nargs="+", help="one or more chamber ids to pool")
    parser.add_argument("--model", default="llama3.1:latest")
    parser.add_argument(
        "--provider", default="ollama", choices=[p.value for p in ProviderType]
    )
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument(
        "--turns-out",
        required=True,
        type=Path,
        help="Markdown output: turn id, round, speaker, assigned stance, full text. "
        "Never contains the judge's verdict.",
    )
    parser.add_argument(
        "--verdicts-out",
        required=True,
        type=Path,
        help="JSON output: turn id -> judge's verdict ('pro'/'con'/'neutral'/null).",
    )
    args = parser.parse_args()

    factory: ProviderFactory = SettingsProviderFactory()
    provider = factory.get_for_type(ProviderType(args.provider))
    judge = ComplianceJudge(provider, args.model)

    all_entries: list[dict[str, Any]] = []
    all_verdicts: dict[str, str | None] = {}
    with httpx.Client(base_url=args.api, timeout=30.0) as client:
        for chamber_id in args.chamber_ids:
            entries, verdicts = await _judge_chamber(client, chamber_id, judge)
            all_entries.extend(entries)
            all_verdicts.update(verdicts)

    args.turns_out.parent.mkdir(parents=True, exist_ok=True)
    args.verdicts_out.parent.mkdir(parents=True, exist_ok=True)
    _write_turns_md(args.turns_out, all_entries)
    _write_verdicts_json(args.verdicts_out, all_verdicts)

    judged = len(all_verdicts)
    unreadable = sum(1 for v in all_verdicts.values() if v is None)
    print(
        f"judged {judged} turns across {len(args.chamber_ids)} chamber(s); "
        f"{unreadable} could not be read"
    )


if __name__ == "__main__":
    asyncio.run(main())
