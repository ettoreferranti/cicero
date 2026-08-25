"""Replay a consensus stop gate over concluded chambers, offline.

Every concluded chamber already carries both signals a stop could be built from:
the **stance poll** each debater answered about itself (`chamber.stance_history`),
and the **compliance judge's** independent read of what each turn argued
(`Turn.metadata["argued"]`, F9/FR-34). This replays a candidate gate over that
record and prints what it would have done — no model calls, no network, no
mutation. The whole corpus scores in under a second.

    python scripts/replay_consensus_gate.py --database-url sqlite:///./cicero.db

Three gates are scored side by side, because the interesting comparison is not
"is the judge a better reader" (F9 measured that: 20/21 against hand-reading) but
"is it a better *stop signal*", which is a different question and has a different
answer:

- ``poll``  — what ships today: every measured, unmuted debater's self-report agrees.
- ``judge`` — the same rule over the judged side of each debater's turn that round.
- ``both``  — the poll fires *and* the judge's read of the same round agrees.

Judged sides are filtered exactly as ``deciding_stances`` filters polled ones:
muted debaters and unmeasured turns do not vote, and an empty tally falls back to
the wider set rather than reading as disagreement. A round nobody was judged in
scores as no-consensus for the judge, never as agreement by default.

Written for the measurement behind
``docs/superpowers/specs/2026-08-25-consensus-needs-two-signals-design.md``. Point
it at whichever database holds the runs you want to score — the Docker stack keeps
its chambers in the ``cicero-data`` volume, not in the repo's ``cicero.db``:

    docker cp cicero-backend-1:/data/cicero.db /tmp/cicero.db
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from cicero.core.consensus import is_consensus
from cicero.core.roster import active_participants, deciding_stances
from cicero.domain.enums import ChamberStatus, Stance
from cicero.domain.models import Chamber
from cicero.persistence.sqlalchemy_repo import SqlAlchemyChamberRepository

#: The judged-side values that count as a vote. Anything else — a missing key, a
#: failed judge call, an empty turn — is unmeasured, and unmeasured never votes.
_JUDGED = {stance.value for stance in Stance}


@dataclass(frozen=True)
class RoundVerdict:
    """What each gate said about one polled round."""

    round_index: int
    poll: bool
    judge: bool

    @property
    def both(self) -> bool:
        return self.poll and self.judge


def judged_stances(chamber: Chamber, round_index: int) -> tuple[dict[str, Stance], tuple[str, ...]]:
    """Each active debater's judged side for one round, and whose turn was unmeasured.

    The mirror of ``deciding_stances`` for the judge's signal: only real evidence
    votes. A debater with no judged turn in this round is reported, not assumed —
    counting an assumption is how a split chamber gets recorded as unanimous.
    """
    active = {participant.id for participant in active_participants(chamber)}
    voting: dict[str, Stance] = {}
    unmeasured: list[str] = []
    for turn in chamber.turns:
        if turn.participant_id is None or turn.round_index != round_index:
            continue
        if turn.participant_id not in active:
            continue
        argued = turn.metadata.get("argued")
        if argued in _JUDGED:
            voting[str(turn.participant_id)] = Stance(str(argued))
        else:
            unmeasured.append(str(turn.participant_id))
    return voting, tuple(unmeasured)


def replay(chamber: Chamber) -> list[RoundVerdict]:
    """Score every gate against every poll the chamber actually recorded."""
    verdicts = []
    history = list(chamber.stance_history)
    for index, poll in enumerate(history):
        # The engine only ever saw the polls up to this one, and
        # ``deciding_stances`` reads that history to tell a debater who was
        # never measured from one whose reading is merely stale. Replaying with
        # the full history would let a later poll excuse an earlier phantom.
        as_seen = chamber.model_copy(deep=True)
        as_seen.stance_history = history[: index + 1]
        polled = is_consensus(deciding_stances(as_seen, poll.stances, poll.unparsed))
        judged, _ = judged_stances(chamber, poll.round_index)
        verdicts.append(
            RoundVerdict(
                round_index=poll.round_index,
                poll=polled,
                judge=is_consensus(judged) if judged else False,
            )
        )
    return verdicts


def _first(verdicts: Iterable[RoundVerdict], gate: str) -> int | None:
    """The 1-based round a gate would first have stopped the debate, or ``None``."""
    for verdict in verdicts:
        if getattr(verdict, gate):
            return verdict.round_index + 1
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--database-url",
        default="sqlite:///./cicero.db",
        help="SQLAlchemy URL of the database holding the concluded chambers.",
    )
    args = parser.parse_args()

    repository = SqlAlchemyChamberRepository(args.database_url)
    scored = []
    for chamber in repository.list():
        if chamber.status is not ChamberStatus.CONCLUDED:
            continue  # a paused or running debate has not finished being evidence
        if not any(turn.metadata.get("argued") in _JUDGED for turn in chamber.turns):
            continue  # ran before F9, or with measure_compliance off: no judge signal
        if not chamber.stance_history:
            continue
        scored.append((chamber, replay(chamber)))

    if not scored:
        print("No chamber carries both signals. Is this the right --database-url?")
        return

    header = f"{'topic':46s} {'ran':>4} {'stopped':>14} {'poll':>6} {'judge':>6} {'both':>6}"
    print(header)
    print("-" * len(header))
    counts: Counter[str] = Counter()
    for chamber, verdicts in scored:
        ran = int(str(chamber.config.get("rounds_completed") or 0))
        gates = {gate: _first(verdicts, gate) for gate in ("poll", "judge", "both")}
        for gate, at in gates.items():
            if at is not None:
                counts[gate] += 1
                if at < ran:
                    counts[f"{gate}_earlier"] += 1
        cells = "".join(f"{'-' if at is None else f'r{at}':>7}" for at in gates.values())
        stop = str(chamber.config.get("stop_reason") or "?")
        print(f"{chamber.topic[:46]:46s} {ran:>4} {stop:>14} {cells}")

    print(f"\n{len(scored)} chambers carry both signals.")
    for gate in ("poll", "judge", "both"):
        print(
            f"  {gate:6s} fires in {counts[gate]:>2} of them; "
            f"{counts[f'{gate}_earlier']:>2} would end a debate that actually ran longer"
        )


if __name__ == "__main__":
    main()
