"""Replay the hand-read F10 benchmark past a compliance judge and score it.

``check_compliance_judge.py`` judges turns pulled from a *live* API, which is how
the F10 benchmark was built but not something anyone can re-run: it needs the
original chamber still sitting in a database. This reads the committed fixture
instead, so the 32 hand-read turns of the Switzerland/Italy chamber are a
benchmark any judge or prompt can be measured against, offline, forever.

    python scripts/score_compliance_judge.py --model qwen3:30b --out /tmp/arm.json

What it prints is a scoreboard against ``f10_hand_labels.json``: agreement,
unmeasured, and the pro/con inversions that are the defect this benchmark exists
to track. ``--compare-to`` a previous run's JSON adds the split that aggregate
agreement hides — which turns the change *fixed* and which it *broke*.

Every judge reply is kept verbatim in the ``--out`` JSON. The F10 quote-first run
recorded only parsed verdicts, so when it failed on 32 of 32 turns its own
decision record could not say whether the cause was a provider error, an
unreadable reply or an abstention. See
``docs/superpowers/decisions/2026-08-14-quote-first-judging-not-shipped.md``.

Nothing here writes to the database or mutates a chamber.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from cicero.core.compliance import ComplianceJudge
from cicero.core.consensus import parse_stance
from cicero.domain.enums import ProviderType
from cicero.providers.factory import ProviderFactory, SettingsProviderFactory

BACKEND_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = BACKEND_ROOT / "tests" / "fixtures"
DEFAULT_TURNS = FIXTURES / "f10_invasion_turns.md"
DEFAULT_LABELS = FIXTURES / "f10_hand_labels.json"

#: Section header written by ``check_compliance_judge.py``'s ``_write_turns_md``.
_TURN_HEADER = re.compile(r"^## Turn (?P<turn_id>\S+)\s*$", re.MULTILINE)
_META = re.compile(r"^- (?P<key>[a-z ]+): (?P<value>.*)$")


@dataclass(frozen=True)
class BenchmarkTurn:
    """One hand-read turn: the argument, and the context needed to judge it."""

    turn_id: str
    topic: str
    speaker: str
    round_index: int
    #: The stance its author was *assigned*. Recorded for reporting only — it is
    #: never shown to the judge, which is the whole design of FR-34.
    assigned: str
    content: str


def parse_turns_markdown(text: str) -> list[BenchmarkTurn]:
    """Read the turns fixture back into structured turns.

    The fixture is the exact file ``check_compliance_judge.py`` writes: a
    ``## Turn <id>`` header, a block of ``- key: value`` metadata, a blank line,
    then the argument, which runs to several paragraphs and must be kept whole.
    """
    turns: list[BenchmarkTurn] = []
    matches = list(_TURN_HEADER.finditer(text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end() : end]
        meta: dict[str, str] = {}
        lines = body.splitlines()
        cursor = 0
        for cursor, line in enumerate(lines):  # noqa: B007
            if found := _META.match(line):
                meta[found["key"]] = found["value"].strip()
            elif meta and not line.strip():
                cursor += 1
                break
        turns.append(
            BenchmarkTurn(
                turn_id=match["turn_id"],
                topic=meta.get("motion", ""),
                speaker=meta.get("speaker", ""),
                round_index=int(meta.get("round", 0)),
                assigned=meta.get("assigned stance", ""),
                content="\n".join(lines[cursor:]).strip(),
            )
        )
    return turns


@dataclass(frozen=True)
class Scoreboard:
    """How one arm's verdicts compare with the hand labels."""

    #: Hand-read turns, i.e. the denominator. Always the label count, never the
    #: verdict count — a run that died halfway must not score out of what it
    #: managed to finish.
    total: int
    agreed: int
    #: Turns the arm produced no readable verdict for, including turns it never
    #: reached. Distinct from ``wrong``: the F10 gate thresholds them separately.
    unmeasured: int
    #: ``(turn_id, hand_label, verdict)`` for each turn judged and judged wrongly.
    wrong: list[tuple[str, str, str]] = field(default_factory=list)
    confusion: Counter[tuple[str, str]] = field(default_factory=Counter)

    @property
    def inversions(self) -> int:
        """Wrong verdicts that are a straight pro/con flip.

        The failure F10 set out to fix and did not: a turn spending four
        paragraphs dismantling the pro case reads as pro. Milder misses — a con
        read as neutral — are wrong but are not this.
        """
        return sum(
            1
            for _, expected, got in self.wrong
            if {expected, got} == {"pro", "con"}
        )


def score(verdicts: Mapping[str, str | None], labels: Mapping[str, str]) -> Scoreboard:
    """Score ``verdicts`` against the hand labels.

    Verdicts for turns that were never hand-read are ignored rather than counted:
    there is no ground truth to score them against.
    """
    agreed = unmeasured = 0
    wrong: list[tuple[str, str, str]] = []
    confusion: Counter[tuple[str, str]] = Counter()
    for turn_id, expected in labels.items():
        got = verdicts.get(turn_id)
        if got is None:
            unmeasured += 1
            continue
        confusion[(expected, got)] += 1
        if got == expected:
            agreed += 1
        else:
            wrong.append((turn_id, expected, got))
    return Scoreboard(
        total=len(labels),
        agreed=agreed,
        unmeasured=unmeasured,
        wrong=wrong,
        confusion=confusion,
    )


@dataclass(frozen=True)
class Delta:
    """Which turns a change fixed, and which it broke."""

    fixed: list[str]
    broken: list[str]


def compare(
    baseline: Mapping[str, str | None],
    treatment: Mapping[str, str | None],
    labels: Mapping[str, str],
) -> Delta:
    """Split two arms turn by turn.

    Aggregate agreement can hold steady while an arm fixes three turns and breaks
    three others; those are not the same result, and only this tells them apart.

    A turn *absent* from either arm is skipped — one arm never covered it, so it
    is evidence about neither. A turn *present* but null was judged and came back
    unreadable, which is a real regression when the other arm read it.
    """
    fixed: list[str] = []
    broken: list[str] = []
    for turn_id, expected in labels.items():
        if turn_id not in baseline or turn_id not in treatment:
            continue
        was = baseline[turn_id] == expected
        now = treatment[turn_id] == expected
        if now and not was:
            fixed.append(turn_id)
        elif was and not now:
            broken.append(turn_id)
    return Delta(fixed=fixed, broken=broken)


async def _run(
    turns: Sequence[BenchmarkTurn], judge: ComplianceJudge
) -> dict[str, dict[str, str | None]]:
    """Judge every turn, keeping the verdict *and* the reply behind it."""
    readings: dict[str, dict[str, str | None]] = {}
    for index, turn in enumerate(turns, start=1):
        reading = await judge.read(turn.topic, turn.content)
        readings[turn.turn_id] = {
            "verdict": reading.stance.value if reading.stance else None,
            "reply": reading.reply,
            "error": reading.error,
        }
        print(f"  [{index}/{len(turns)}] {turn.turn_id[:8]} -> {reading.stance or '??'}")
    return readings


def _report(board: Scoreboard, readings: Mapping[str, Mapping[str, str | None]]) -> None:
    percent = 100.0 * board.agreed / board.total if board.total else 0.0
    print(f"\nagreement  {board.agreed}/{board.total} ({percent:.0f}%)")
    print(f"unmeasured {board.unmeasured}/{board.total}")
    print(f"wrong      {len(board.wrong)} (of which {board.inversions} pro/con inversions)")
    if board.confusion:
        print("\nhand label -> verdict")
        for (expected, got), count in sorted(board.confusion.items()):
            mark = "  " if expected == got else "<-"
            print(f"  {expected:>7} -> {got:<7} {count:>3} {mark}")
    if board.wrong:
        print("\nmisjudged turns")
        for turn_id, expected, got in board.wrong:
            print(f"  {turn_id}  hand={expected:<7} judge={got}")
    # Unmeasured turns are the ones whose cause was invisible in the F10 run.
    for turn_id, reading in readings.items():
        if reading.get("verdict") is None:
            cause = reading.get("error") or f"unreadable reply: {reading.get('reply')!r}"
            print(f"  {turn_id} NOT MEASURED — {cause}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3:30b", help="the judge model")
    parser.add_argument(
        "--provider", default="ollama", choices=[p.value for p in ProviderType]
    )
    parser.add_argument("--turns", type=Path, default=DEFAULT_TURNS)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument(
        "--out", type=Path, help="JSON output: per turn, the verdict, the raw reply, any error"
    )
    parser.add_argument(
        "--compare-to",
        type=Path,
        help="a previous run's JSON (or a flat {turn_id: verdict} file), "
        "to split fixed from broken",
    )
    parser.add_argument(
        "--score-only",
        type=Path,
        help="score an existing run's JSON instead of calling a judge",
    )
    parser.add_argument(
        "--reparse",
        type=Path,
        help="re-read a previous run's stored replies with the current parser, "
        "offline — no model call. Scores a parser change in milliseconds.",
    )
    args = parser.parse_args()

    labels: dict[str, str] = json.loads(args.labels.read_text(encoding="utf-8"))

    if args.reparse:
        stored = _verdict_records(json.loads(args.reparse.read_text(encoding="utf-8")))
        fresh = reparse(stored)
        readings = {
            turn_id: {**record, "verdict": fresh[turn_id]}
            for turn_id, record in stored.items()
        }
        print(f"re-parsed {len(readings)} stored replies from {args.reparse.name}")
    elif args.score_only:
        readings = _verdict_records(json.loads(args.score_only.read_text(encoding="utf-8")))
    else:
        turns = parse_turns_markdown(args.turns.read_text(encoding="utf-8"))
        print(f"judging {len(turns)} turns with {args.model} via {args.provider}")
        factory: ProviderFactory = SettingsProviderFactory()
        judge = ComplianceJudge(
            factory.get_for_type(ProviderType(args.provider)), args.model
        )
        readings = asyncio.run(_run(turns, judge))

    verdicts = {turn_id: r.get("verdict") for turn_id, r in readings.items()}
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(readings, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"\nwrote {args.out}")

    _report(score(verdicts, labels), readings)

    if args.compare_to:
        other = _verdict_records(json.loads(args.compare_to.read_text(encoding="utf-8")))
        baseline = {turn_id: r.get("verdict") for turn_id, r in other.items()}
        delta = compare(baseline, verdicts, labels)
        base_board = score(baseline, labels)
        print(f"\nvs {args.compare_to.name}: {base_board.agreed}/{base_board.total} -> ", end="")
        print(f"{score(verdicts, labels).agreed}/{base_board.total}")
        print(f"  fixed  {len(delta.fixed)}: {', '.join(t[:8] for t in delta.fixed) or '—'}")
        print(f"  broken {len(delta.broken)}: {', '.join(t[:8] for t in delta.broken) or '—'}")


def reparse(records: Mapping[str, Mapping[str, str | None]]) -> dict[str, str | None]:
    """Re-read stored judge replies with the *current* ``parse_stance``.

    The point of keeping the raw replies. A change to the parser can be scored
    against the hand labels offline, in milliseconds, with no model involved —
    which is how the ``</think>`` defect should have been caught, and how the
    next one will be.
    """
    verdicts: dict[str, str | None] = {}
    for turn_id, record in records.items():
        reply = record.get("reply")
        stance = parse_stance(reply) if reply else None
        verdicts[turn_id] = stance.value if stance else None
    return verdicts


def _verdict_records(raw: dict[str, object]) -> dict[str, dict[str, str | None]]:
    """Accept either this script's rich records or a flat ``{turn_id: verdict}``.

    ``f10_baseline_verdicts.json`` is the flat shape, written by the older
    harness. It is the arm every future change is measured against, so reading it
    is not optional.
    """
    records: dict[str, dict[str, str | None]] = {}
    for turn_id, value in raw.items():
        if isinstance(value, dict):
            records[turn_id] = {
                "verdict": value.get("verdict"),
                "reply": value.get("reply"),
                "error": value.get("error"),
            }
        else:
            records[turn_id] = {"verdict": value, "reply": None, "error": None}
    return records


if __name__ == "__main__":
    main()
