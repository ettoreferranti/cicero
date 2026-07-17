"""Tests for chamber export (Markdown / JSON)."""

from __future__ import annotations

from cicero.core.export import to_export_dict, to_markdown
from cicero.domain.enums import ConsensusOutcome, Stance
from cicero.domain.models import ConsensusResult, Turn
from tests.conftest import make_chamber, make_participant


def _chamber_with_debate():  # type: ignore[no-untyped-def]
    a = make_participant("Ada", Stance.PRO)
    b = make_participant("Zeno", Stance.CON)
    chamber = make_chamber(a, b, topic="Should we colonise Mars?")
    chamber.category = "scientific"
    chamber.turns.append(Turn(participant_id=a.id, round_index=0, content="Mars matters."))
    chamber.turns.append(Turn(participant_id=b.id, round_index=0, content="Too costly."))
    chamber.consensus = ConsensusResult(
        outcome=ConsensusOutcome.DISAGREEMENT, statement="No agreement reached."
    )
    return chamber


def test_markdown_includes_topic_participants_and_turns() -> None:
    md = to_markdown(_chamber_with_debate())
    assert "# Debate: Should we colonise Mars?" in md
    assert "**Category:** scientific" in md
    assert "**Ada**" in md and "**Zeno**" in md
    assert "Mars matters." in md
    assert "Too costly." in md


def test_markdown_includes_outcome_and_statement() -> None:
    md = to_markdown(_chamber_with_debate())
    assert "## Outcome: disagreement" in md
    assert "No agreement reached." in md


def test_markdown_skips_empty_turns() -> None:
    a = make_participant("Ada", Stance.PRO)
    chamber = make_chamber(a)
    chamber.turns.append(Turn(participant_id=a.id, round_index=0, content="   "))
    md = to_markdown(chamber)
    assert "Round 1" not in md  # the empty turn produced no heading


def test_markdown_without_consensus_has_no_outcome_section() -> None:
    a = make_participant("Ada", Stance.PRO)
    md = to_markdown(make_chamber(a))
    assert "## Outcome" not in md


def test_export_dict_roundtrips_key_fields() -> None:
    chamber = _chamber_with_debate()
    data = to_export_dict(chamber)
    assert data["topic"] == "Should we colonise Mars?"
    assert data["consensus"]["outcome"] == "disagreement"
    assert len(data["participants"]) == 2
