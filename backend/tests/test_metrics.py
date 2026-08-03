"""Tests for per-participant metrics computation."""

from __future__ import annotations

from cicero.core.metrics import compute_participant_metrics
from cicero.domain.enums import Stance
from cicero.domain.models import Turn
from tests.conftest import make_chamber, make_participant


def _turn(pid, rnd, pt=0, ct=0, error=False):  # type: ignore[no-untyped-def]
    meta: dict[str, object] = {"prompt_tokens": pt, "completion_tokens": ct}
    if error:
        meta["error"] = "boom"
    return Turn(participant_id=pid, round_index=rnd, content="x", metadata=meta)


def test_metrics_zero_when_no_turns() -> None:
    a = make_participant("A", Stance.PRO)
    metrics = compute_participant_metrics(make_chamber(a))
    assert len(metrics) == 1
    assert metrics[0].turns == 0
    assert metrics[0].total_tokens == 0


def test_metrics_aggregate_tokens_and_turns() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    chamber.turns.append(_turn(a.id, 0, pt=5, ct=3))
    chamber.turns.append(_turn(a.id, 1, pt=4, ct=2))
    chamber.turns.append(_turn(b.id, 0, pt=1, ct=1))
    metrics = {m.display_name: m for m in compute_participant_metrics(chamber)}
    assert metrics["A"].turns == 2
    assert metrics["A"].prompt_tokens == 9
    assert metrics["A"].completion_tokens == 5
    assert metrics["A"].total_tokens == 14
    assert metrics["B"].turns == 1
    assert metrics["B"].total_tokens == 2


def test_metrics_count_errors() -> None:
    a = make_participant("A", Stance.PRO)
    chamber = make_chamber(a)
    chamber.turns.append(_turn(a.id, 0, pt=1, ct=1))
    chamber.turns.append(_turn(a.id, 1, error=True))
    m = compute_participant_metrics(chamber)[0]
    assert m.turns == 2
    assert m.errors == 1


def test_metrics_preserve_participant_order() -> None:
    a = make_participant("Alpha", Stance.PRO)
    b = make_participant("Beta", Stance.CON)
    metrics = compute_participant_metrics(make_chamber(a, b))
    assert [m.display_name for m in metrics] == ["Alpha", "Beta"]


def test_metrics_ignore_unknown_participant_turns() -> None:
    from uuid import uuid4

    a = make_participant("A", Stance.PRO)
    chamber = make_chamber(a)
    chamber.turns.append(_turn(uuid4(), 0, pt=99, ct=99))  # ghost turn
    m = compute_participant_metrics(chamber)[0]
    assert m.turns == 0
    assert m.total_tokens == 0


def test_metrics_ignore_system_turns() -> None:
    """Moderator notes and evidence turns have no participant, so they count for none."""
    a = make_participant("A", Stance.PRO)
    chamber = make_chamber(a)
    chamber.turns.append(Turn(round_index=0, content="note", metadata={"prompt_tokens": 7}))
    m = compute_participant_metrics(chamber)[0]
    assert m.turns == 0
    assert m.total_tokens == 0


def test_metrics_treat_non_integer_token_counts_as_zero() -> None:
    """Turn metadata is free-form, so odd values must not corrupt the totals."""
    a = make_participant("A", Stance.PRO)
    chamber = make_chamber(a)
    chamber.turns.append(
        Turn(
            participant_id=a.id,
            round_index=0,
            content="x",
            # bool is an int subclass, and a provider may report a string.
            metadata={"prompt_tokens": True, "completion_tokens": "12"},
        )
    )
    m = compute_participant_metrics(chamber)[0]
    assert m.turns == 1
    assert m.prompt_tokens == 0
    assert m.completion_tokens == 0
