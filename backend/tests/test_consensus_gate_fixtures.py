"""The F11 benchmark: what each candidate stop gate does to four real chambers.

The chambers in ``tests/fixtures/consensus_gate/`` are the evidence behind
``docs/superpowers/specs/2026-08-25-consensus-needs-two-signals-design.md``. Two of
them no longer exist anywhere else — they were deleted from the maintainer's
database the day after the measurement — which is the whole reason they are
committed. Their debater names were replaced with the neutral roster the rest of
the suite uses; nothing else about the records was altered.

These tests pin the *conclusions* the spec draws, not the file contents, so they
fail if a change to ``is_consensus``, ``deciding_stances`` or the judged-stance rule
would quietly invalidate the argument for building F11 — which is the failure mode a
frozen fixture cannot catch on its own (see ``test_judge_benchmark`` for the same
reasoning applied to F10).

No provider, no network, no database.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from cicero.domain.models import Chamber

BACKEND_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = BACKEND_ROOT / "tests" / "fixtures" / "consensus_gate"


def _load_script() -> Any:
    """Import ``scripts/replay_consensus_gate.py`` as a module.

    Measurement code lives outside the ``cicero`` package, but what it concludes is
    load-bearing for a design decision, so it is tested like anything else.
    """
    path = BACKEND_ROOT / "scripts" / "replay_consensus_gate.py"
    spec = importlib.util.spec_from_file_location("replay_consensus_gate", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


script = _load_script()


def _chamber(name: str) -> Chamber:
    return Chamber.model_validate_json((FIXTURES / f"{name}.json").read_text())


#: (fixture, round the poll would stop on, round poll+judge would stop on).
#: ``None`` means that gate never fires. 1-based rounds, as the spec reports them.
GATES = [
    # Both original Colombia chambers: the poll called unanimity on the opening
    # statements; the judged turns of that same round contradict it.
    ("gate_colombia_original", 1, None),
    ("gate_colombia_original_no_moderator", 1, None),
    # The decisive one. Rerun with min_rounds=3 and a readable poll, it still
    # stopped on a false unanimity — later, not never.
    ("gate_colombia_rerun_floor3", 4, None),
    # The control: a debate that genuinely converged. Both signals agree, so the
    # conjunction leaves it exactly where it is.
    ("gate_lawns_converged", 7, 7),
]


@pytest.mark.parametrize(("name", "poll_round", "both_round"), GATES)
def test_gate_verdicts_match_the_spec(
    name: str, poll_round: int | None, both_round: int | None
) -> None:
    verdicts = script.replay(_chamber(name))
    assert script._first(verdicts, "poll") == poll_round
    assert script._first(verdicts, "both") == both_round


def test_the_conjunction_blocks_three_stops_and_keeps_one() -> None:
    # The spec's claim in one line: on this corpus the second signal only ever
    # withholds a stop the poll proposed, and it withholds three of the four.
    blocked = [name for name, poll, both in GATES if poll is not None and both is None]
    kept = [name for name, poll, both in GATES if poll is not None and both == poll]
    assert len(blocked) == 3 and len(kept) == 1
    assert not [name for name, poll, both in GATES if both is not None and poll is None]


def test_the_rerun_shows_a_round_floor_does_not_prevent_a_false_unanimity() -> None:
    # The objection this fixture exists to answer: "min_rounds was the whole
    # problem." The rerun stopped on `consensus` at round 4 with BOTH pro-assigned
    # debaters judged `pro` in every round they spoke — still arguing the motion in
    # the round that ended it.
    chamber = _chamber("gate_colombia_rerun_floor3")
    assert chamber.settings.min_rounds == 3
    assert chamber.config["stop_reason"] == "consensus"

    pro_debaters = {p.id for p in chamber.participants if p.stance.value == "pro"}
    judged = [
        turn.metadata.get("argued")
        for turn in chamber.turns
        if turn.participant_id in pro_debaters
    ]
    assert judged and all(side == "pro" for side in judged)

    # ...while the poll that ended it recorded every debater as `con`.
    final = chamber.stance_history[-1]
    assert final.unparsed == []  # not a parse failure; these are the models' answers
    assert {stance.value for stance in final.stances.values()} == {"con"}


def test_fixture_rosters_carry_no_real_person_names() -> None:
    # These are public-repo fixtures of models instructed to invent facts and
    # attack each other by name. The roster is neutral, and must stay that way.
    allowed = {"Alice", "Bob", "Eve", "Charlie", "David"}
    for path in sorted(FIXTURES.glob("*.json")):
        chamber = Chamber.model_validate_json(path.read_text())
        names = {p.display_name for p in chamber.participants}
        assert names <= allowed, f"{path.name}: unexpected debater names {names - allowed}"
