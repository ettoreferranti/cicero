"""Tests for run comparison (H4)."""

from __future__ import annotations

from cicero.core.compare import compare_chambers, summarize_run
from cicero.domain.enums import ConsensusOutcome, ProviderType, Stance
from cicero.domain.models import ConsensusResult, Moderator
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


def test_run_summary_carries_the_headline() -> None:
    ada = make_participant("Ada", Stance.PRO)
    chamber = make_chamber(ada)
    chamber.consensus = ConsensusResult(
        outcome=ConsensusOutcome.CONSENSUS,
        statement="A long statement nobody wants to read twice.",
        headline="Mars should wait.",
        winning_stance=Stance.PRO,
        final_stances={str(ada.id): Stance.PRO},
        unparsed=[],
    )
    assert summarize_run(chamber)["headline"] == "Mars should wait."


def test_run_summary_headline_is_none_before_an_outcome() -> None:
    chamber = make_chamber(make_participant("Ada", Stance.PRO))
    assert summarize_run(chamber)["headline"] is None


def test_run_summary_empty_headline_converts_to_none() -> None:
    ada = make_participant("Ada", Stance.PRO)
    chamber = make_chamber(ada)
    chamber.consensus = ConsensusResult(
        outcome=ConsensusOutcome.CONSENSUS,
        statement="Some statement.",
        headline="",
        winning_stance=Stance.PRO,
        final_stances={str(ada.id): Stance.PRO},
        unparsed=[],
    )
    assert summarize_run(chamber)["headline"] is None


def test_run_summary_carries_the_moderator() -> None:
    chamber = make_chamber(make_participant("Ada", Stance.PRO))
    chamber.moderator = Moderator(provider=ProviderType.MOCK, model="judge-model")
    # Which model judged is exactly the variable a two-run comparison isolates.
    assert summarize_run(chamber)["moderator"] == "mock/judge-model"


def test_run_summary_moderator_is_none_when_unset() -> None:
    chamber = make_chamber(make_participant("Ada", Stance.PRO))
    assert summarize_run(chamber)["moderator"] is None
