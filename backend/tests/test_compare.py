"""Tests for run comparison (H4)."""

from __future__ import annotations

from cicero.core.compare import compare_chambers, summarize_run
from cicero.domain.enums import ConsensusOutcome, Stance
from cicero.domain.models import ConsensusResult
from tests.conftest import make_chamber, make_participant


def _concluded_chamber(topic: str = "Should we colonise Mars?"):  # type: ignore[no-untyped-def]
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b, topic=topic)
    chamber.consensus = ConsensusResult(
        outcome=ConsensusOutcome.MAJORITY,
        statement="Pro won.",
        winning_stance=Stance.PRO,
        final_stances={str(a.id): Stance.PRO, str(b.id): Stance.PRO},
    )
    chamber.config = {"stop_reason": "max_rounds", "rounds_completed": 3, "tokens_used": 42}
    return chamber


def test_summarize_run_snapshot() -> None:
    chamber = _concluded_chamber()
    summary = summarize_run(chamber)
    assert summary["outcome"] == "majority"
    assert summary["winning_stance"] == "pro"
    assert summary["statement"] == "Pro won."
    assert summary["rounds_completed"] == 3
    assert summary["tokens_used"] == 42
    assert summary["participants"][0] == {
        "display_name": "A",
        "provider": "mock",
        "model": "scripted",
        "initial_stance": "pro",
        "final_stance": "pro",
    }
    # B started con but finished pro — the summary shows the movement.
    assert summary["participants"][1]["initial_stance"] == "con"
    assert summary["participants"][1]["final_stance"] == "pro"


def test_summarize_run_without_consensus() -> None:
    chamber = make_chamber(make_participant("A", Stance.PRO))
    summary = summarize_run(chamber)
    assert summary["outcome"] is None
    assert summary["winning_stance"] is None
    assert summary["participants"][0]["final_stance"] is None


def test_compare_chambers_flags_topic_match() -> None:
    a = _concluded_chamber()
    b = _concluded_chamber()
    result = compare_chambers(a, b)
    assert result["same_topic"] is True
    assert result["a"]["chamber_id"] == str(a.id)
    assert result["b"]["chamber_id"] == str(b.id)
    c = _concluded_chamber(topic="Should we ban cars?")
    assert compare_chambers(a, c)["same_topic"] is False
    # Case and surrounding whitespace do not count as a different topic.
    d = _concluded_chamber(topic="  SHOULD WE COLONISE MARS?  ")
    assert compare_chambers(a, d)["same_topic"] is True
