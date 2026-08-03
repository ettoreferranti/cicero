"""Tests for chamber export (Markdown / JSON)."""

from __future__ import annotations

from uuid import uuid4

from cicero.core.export import to_export_dict, to_markdown
from cicero.core.prompt_builder import KIND_EVIDENCE, KIND_MODERATOR_NOTE
from cicero.domain.enums import ConsensusOutcome, Stance
from cicero.domain.models import Citation, ConsensusResult, StancePoll, Turn
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


def test_markdown_renders_the_stance_history_as_a_table() -> None:
    chamber = _chamber_with_debate()
    ada, zeno = chamber.participants
    chamber.stance_history = [
        StancePoll(
            round_index=0, stances={str(ada.id): Stance.PRO, str(zeno.id): Stance.CON}
        ),
        StancePoll(
            round_index=1, stances={str(ada.id): Stance.PRO, str(zeno.id): Stance.NEUTRAL}
        ),
    ]
    md = to_markdown(chamber)

    assert "## Stance history" in md
    assert "| Round | Ada | Zeno |" in md
    assert "| 1 | pro | con |" in md
    assert "| 2 | pro | neutral |" in md  # Zeno moved


def test_markdown_marks_stances_that_could_not_be_read() -> None:
    chamber = _chamber_with_debate()
    ada, zeno = chamber.participants
    chamber.stance_history = [
        StancePoll(
            round_index=0,
            stances={str(ada.id): Stance.PRO, str(zeno.id): Stance.CON},
            unparsed=[str(zeno.id)],
        )
    ]
    md = to_markdown(chamber)
    assert "| 1 | pro | con (?) |" in md
    assert "could not be read" in md


def test_markdown_omits_the_stance_history_when_there_is_none() -> None:
    assert "## Stance history" not in to_markdown(_chamber_with_debate())


def test_stance_history_falls_back_to_the_declared_stance() -> None:
    # A participant added after a poll has no entry in it; render their
    # declared stance rather than a blank or a crash.
    chamber = _chamber_with_debate()
    chamber.stance_history = [StancePoll(round_index=0, stances={})]
    md = to_markdown(chamber)
    assert "| 1 | pro | con |" in md


def test_export_dict_carries_the_stance_history() -> None:
    chamber = _chamber_with_debate()
    ada = chamber.participants[0]
    chamber.stance_history = [StancePoll(round_index=0, stances={str(ada.id): Stance.CON})]
    exported = to_export_dict(chamber)
    assert exported["stance_history"][0]["round_index"] == 0
    assert exported["stance_history"][0]["stances"][str(ada.id)] == "con"


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


def test_markdown_omits_empty_category_and_includes_description() -> None:
    a = make_participant("Ada", Stance.PRO)
    chamber = make_chamber(a)
    assert "**Category:**" not in to_markdown(chamber)

    chamber.description = "Framing: assume a 20-year horizon."
    md = to_markdown(chamber)
    assert "Framing: assume a 20-year horizon." in md


def test_markdown_labels_system_authored_turns() -> None:
    a = make_participant("Ada", Stance.PRO)
    chamber = make_chamber(a)
    chamber.turns.append(
        Turn(round_index=0, content="Stay concrete.", metadata={"kind": KIND_MODERATOR_NOTE})
    )
    chamber.turns.append(
        Turn(round_index=0, content="Background reading.", metadata={"kind": KIND_EVIDENCE})
    )
    md = to_markdown(chamber)
    # System turns are attributed to their role, never to a debater.
    assert "Stay concrete." in md
    assert "Background reading." in md
    assert "Ada" not in md.split("## Transcript")[1]


def test_markdown_labels_a_turn_from_an_unknown_participant() -> None:
    chamber = make_chamber(make_participant("Ada", Stance.PRO))
    chamber.turns.append(Turn(participant_id=uuid4(), round_index=0, content="Who said this?"))
    assert "— unknown" in to_markdown(chamber)


def test_markdown_renders_citations_with_title_or_url_fallback() -> None:
    a = make_participant("Ada", Stance.PRO)
    chamber = make_chamber(a)
    chamber.turns.append(
        Turn(
            participant_id=a.id,
            round_index=0,
            content="Evidence supports this.",
            citations=[
                Citation(url="https://example.org/a", title="A study"),
                Citation(url="https://example.org/b", title="   "),
            ],
        )
    )
    md = to_markdown(chamber)
    assert "Sources:" in md
    assert "- [A study](https://example.org/a)" in md
    # A blank title falls back to the URL as the link label.
    assert "- [https://example.org/b](https://example.org/b)" in md


def test_markdown_reports_the_winning_stance() -> None:
    chamber = _chamber_with_debate()
    chamber.consensus = ConsensusResult(
        outcome=ConsensusOutcome.VERDICT,
        statement="VERDICT: the pro case prevails.",
        winning_stance=Stance.PRO,
    )
    md = to_markdown(chamber)
    assert "## Outcome: verdict" in md
    assert "**Winning position:** pro" in md


def test_markdown_without_a_winner_omits_the_winning_position_line() -> None:
    md = to_markdown(_chamber_with_debate())  # disagreement, no winner
    assert "**Winning position:**" not in md


def test_export_dict_roundtrips_key_fields() -> None:
    chamber = _chamber_with_debate()
    data = to_export_dict(chamber)
    assert data["topic"] == "Should we colonise Mars?"
    assert data["consensus"]["outcome"] == "disagreement"
    assert len(data["participants"]) == 2
