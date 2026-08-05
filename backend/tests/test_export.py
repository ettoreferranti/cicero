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
    assert exported["chamber"]["stance_history"][0]["round_index"] == 0
    assert exported["chamber"]["stance_history"][0]["stances"][str(ada.id)] == "con"


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
    assert data["chamber"]["topic"] == "Should we colonise Mars?"
    assert data["chamber"]["consensus"]["outcome"] == "disagreement"
    assert len(data["chamber"]["participants"]) == 2


def _concluded_with_headline():  # type: ignore[no-untyped-def]
    # A genuine majority: two of three debaters (Ada, Zeno) settle on neutral,
    # the third (Kant) dissents on con. A 2-of-2 "majority" is unreachable —
    # decide_outcome reports that as CONSENSUS — so a real MAJORITY fixture
    # needs the dissenter.
    chamber = _chamber_with_debate()
    ada, zeno = chamber.participants
    kant = make_participant("Kant", Stance.CON)
    chamber.participants.append(kant)
    chamber.consensus = ConsensusResult(
        outcome=ConsensusOutcome.MAJORITY,
        statement="The majority prevailed.",
        headline="Mars should wait for cheaper launch costs.",
        winning_stance=Stance.NEUTRAL,
        final_stances={
            str(ada.id): Stance.NEUTRAL,
            str(zeno.id): Stance.NEUTRAL,
            str(kant.id): Stance.CON,
        },
        unparsed=[],
    )
    return chamber


def test_markdown_leads_with_the_headline() -> None:
    md = to_markdown(_concluded_with_headline())
    assert "## Outcome" in md
    assert "**The chamber concluded:** Mars should wait for cheaper launch costs." in md
    # The stance word is demoted to evidence, not promoted to the headline.
    assert "**Winning position:**" not in md


def test_markdown_shows_the_derived_outcome_facts() -> None:
    md = to_markdown(_concluded_with_headline())
    assert "- **Support:** contested — 2 of 3 debaters settled on neutral, 1 dissent" in md
    assert "- **How decided:** majority of final positions" in md


def test_markdown_lists_who_moved() -> None:
    chamber = _concluded_with_headline()
    ada, zeno, kant = chamber.participants
    chamber.stance_history = [
        StancePoll(round_index=0, stances={str(ada.id): Stance.PRO,
                                           str(zeno.id): Stance.CON,
                                           str(kant.id): Stance.CON}),
        StancePoll(round_index=1, stances={str(ada.id): Stance.NEUTRAL,
                                           str(zeno.id): Stance.NEUTRAL,
                                           str(kant.id): Stance.CON}),
    ]
    md = to_markdown(chamber)
    assert "- **Positions moved:** Ada (pro→neutral), Zeno (con→neutral)" in md


def test_markdown_omits_the_movement_line_when_nobody_moved() -> None:
    assert "**Positions moved:**" not in to_markdown(_concluded_with_headline())


def test_markdown_falls_back_to_the_old_shape_without_a_headline() -> None:
    chamber = _concluded_with_headline()
    chamber.consensus.headline = ""
    md = to_markdown(chamber)
    # Strictly better than before, never broken: the old heading returns, and the
    # derived facts are shown alongside it.
    assert "## Outcome: majority" in md
    assert "**Winning position:** neutral" in md
    assert "- **Support:** contested — 2 of 3 debaters settled on neutral, 1 dissent" in md


def test_json_export_carries_the_derived_summary_beside_the_chamber() -> None:
    data = to_export_dict(_concluded_with_headline())
    # A sibling key, so the chamber snapshot stays an exact dump of the model.
    assert data["chamber"]["consensus"]["headline"] == (
        "Mars should wait for cheaper launch costs."
    )
    assert data["outcome_summary"] == {
        "headline": "Mars should wait for cheaper launch costs.",
        "support": "contested — 2 of 3 debaters settled on neutral, 1 dissent",
        "decided_by": "majority of final positions",
        "movements": [],
    }


def test_json_export_has_no_summary_before_a_debate_concludes() -> None:
    chamber = make_chamber(make_participant("Ada", Stance.PRO))
    assert to_export_dict(chamber)["outcome_summary"] is None


# --- Exact-layout tests -----------------------------------------------------
#
# Everything above checks *presence* of a fragment (`"x" in md`), which cannot
# tell a literal from one padded with extra characters — a Markdown heading
# and a line of body text read identically to `in`. These pin full documents
# with `==` so that a stray character anywhere in the render is a failure,
# not just a missing one.


def test_markdown_matches_expected_layout_for_a_simple_debate() -> None:
    md = to_markdown(_chamber_with_debate())
    assert md == "\n".join(
        [
            "# Debate: Should we colonise Mars?",
            "",
            "**Category:** scientific",
            "## Participants",
            "- **Ada** (mock/scripted) — stance: pro",
            "- **Zeno** (mock/scripted) — stance: con",
            "",
            "## Transcript",
            "### Round 1 — Ada",
            "Mars matters.",
            "",
            "### Round 1 — Zeno",
            "Too costly.",
            "",
            "## Outcome: disagreement",
            "",
            "- **Support:** unmeasured — no debater's final position could be read",
            "- **How decided:** unresolved under the judge rule",
            "",
            "No agreement reached.",
            "",
        ]
    )


def test_markdown_matches_expected_layout_for_citations() -> None:
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
    assert md == "\n".join(
        [
            "# Debate: Should we colonise Mars?",
            "",
            "## Participants",
            "- **Ada** (mock/scripted) — stance: pro",
            "",
            "## Transcript",
            "### Round 1 — Ada",
            "Evidence supports this.",
            "",
            "Sources:",
            "- [A study](https://example.org/a)",
            "- [https://example.org/b](https://example.org/b)",
            "",
        ]
    )


def test_markdown_matches_expected_layout_for_a_description() -> None:
    a = make_participant("Ada", Stance.PRO)
    chamber = make_chamber(a)
    chamber.description = "Framing: assume a 20-year horizon."
    md = to_markdown(chamber)
    assert md == "\n".join(
        [
            "# Debate: Should we colonise Mars?",
            "",
            "Framing: assume a 20-year horizon.",
            "",
            "## Participants",
            "- **Ada** (mock/scripted) — stance: pro",
            "",
            "## Transcript",
        ]
    )


def test_markdown_matches_expected_layout_when_a_turn_is_skipped() -> None:
    # An empty turn between two real ones: `continue` must skip only that
    # turn, not stop the scan — the third round's heading has to survive.
    a = make_participant("Ada", Stance.PRO)
    chamber = make_chamber(a)
    chamber.turns.append(Turn(participant_id=a.id, round_index=0, content="First."))
    chamber.turns.append(Turn(participant_id=a.id, round_index=1, content="   "))
    chamber.turns.append(Turn(participant_id=a.id, round_index=2, content="Third."))
    md = to_markdown(chamber)
    assert md == "\n".join(
        [
            "# Debate: Should we colonise Mars?",
            "",
            "## Participants",
            "- **Ada** (mock/scripted) — stance: pro",
            "",
            "## Transcript",
            "### Round 1 — Ada",
            "First.",
            "",
            "### Round 3 — Ada",
            "Third.",
            "",
        ]
    )


def test_markdown_matches_expected_layout_for_system_and_unknown_speakers() -> None:
    a = make_participant("Ada", Stance.PRO)
    chamber = make_chamber(a)
    chamber.turns.append(
        Turn(round_index=0, content="Stay concrete.", metadata={"kind": KIND_MODERATOR_NOTE})
    )
    chamber.turns.append(
        Turn(round_index=0, content="Background reading.", metadata={"kind": KIND_EVIDENCE})
    )
    chamber.turns.append(Turn(participant_id=uuid4(), round_index=0, content="Who said this?"))
    md = to_markdown(chamber)
    assert md == "\n".join(
        [
            "# Debate: Should we colonise Mars?",
            "",
            "## Participants",
            "- **Ada** (mock/scripted) — stance: pro",
            "",
            "## Transcript",
            "### Round 1 — Moderator note",
            "Stay concrete.",
            "",
            "### Round 1 — Research (web evidence)",
            "Background reading.",
            "",
            "### Round 1 — unknown",
            "Who said this?",
            "",
        ]
    )


def test_markdown_matches_expected_layout_for_stance_history_with_unparsed() -> None:
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
    assert md == "\n".join(
        [
            "# Debate: Should we colonise Mars?",
            "",
            "**Category:** scientific",
            "## Participants",
            "- **Ada** (mock/scripted) — stance: pro",
            "- **Zeno** (mock/scripted) — stance: con",
            "",
            "## Transcript",
            "### Round 1 — Ada",
            "Mars matters.",
            "",
            "### Round 1 — Zeno",
            "Too costly.",
            "",
            "## Stance history",
            "| Round | Ada | Zeno |",
            "|---|---|---|",
            "| 1 | pro | con (?) |",
            "",
            "`(?)` — the debater's reply could not be read; the previous "
            "value was carried forward and is not evidence of their position.",
            "",
            "## Outcome: disagreement",
            "",
            "- **Support:** unmeasured — no debater's final position could be read",
            "- **How decided:** unresolved under the judge rule",
            "",
            "No agreement reached.",
            "",
        ]
    )


def test_markdown_matches_expected_layout_for_the_headline_with_movements() -> None:
    chamber = _concluded_with_headline()
    ada, zeno, kant = chamber.participants
    chamber.stance_history = [
        StancePoll(round_index=0, stances={str(ada.id): Stance.PRO,
                                           str(zeno.id): Stance.CON,
                                           str(kant.id): Stance.CON}),
        StancePoll(round_index=1, stances={str(ada.id): Stance.NEUTRAL,
                                           str(zeno.id): Stance.NEUTRAL,
                                           str(kant.id): Stance.CON}),
    ]
    md = to_markdown(chamber)
    assert md == "\n".join(
        [
            "# Debate: Should we colonise Mars?",
            "",
            "**Category:** scientific",
            "## Participants",
            "- **Ada** (mock/scripted) — stance: pro",
            "- **Zeno** (mock/scripted) — stance: con",
            "- **Kant** (mock/scripted) — stance: con",
            "",
            "## Transcript",
            "### Round 1 — Ada",
            "Mars matters.",
            "",
            "### Round 1 — Zeno",
            "Too costly.",
            "",
            "## Stance history",
            "| Round | Ada | Zeno | Kant |",
            "|---|---|---|---|",
            "| 1 | pro | con | con |",
            "| 2 | neutral | neutral | con |",
            "",
            "## Outcome",
            "",
            "**The chamber concluded:** Mars should wait for cheaper launch costs.",
            "",
            "- **Support:** contested — 2 of 3 debaters settled on neutral, 1 dissent",
            "- **How decided:** majority of final positions",
            "- **Positions moved:** Ada (pro→neutral), Zeno (con→neutral)",
            "",
            "The majority prevailed.",
            "",
        ]
    )


def test_markdown_matches_expected_layout_for_the_fallback_without_a_headline() -> None:
    chamber = _concluded_with_headline()
    chamber.consensus.headline = ""
    md = to_markdown(chamber)
    assert md == "\n".join(
        [
            "# Debate: Should we colonise Mars?",
            "",
            "**Category:** scientific",
            "## Participants",
            "- **Ada** (mock/scripted) — stance: pro",
            "- **Zeno** (mock/scripted) — stance: con",
            "- **Kant** (mock/scripted) — stance: con",
            "",
            "## Transcript",
            "### Round 1 — Ada",
            "Mars matters.",
            "",
            "### Round 1 — Zeno",
            "Too costly.",
            "",
            "## Outcome: majority",
            "**Winning position:** neutral",
            "",
            "- **Support:** contested — 2 of 3 debaters settled on neutral, 1 dissent",
            "- **How decided:** majority of final positions",
            "",
            "The majority prevailed.",
            "",
        ]
    )


def test_markdown_matches_expected_layout_for_a_verdict_with_a_winner() -> None:
    chamber = _chamber_with_debate()
    chamber.consensus = ConsensusResult(
        outcome=ConsensusOutcome.VERDICT,
        statement="VERDICT: the pro case prevails.",
        winning_stance=Stance.PRO,
    )
    md = to_markdown(chamber)
    assert md == "\n".join(
        [
            "# Debate: Should we colonise Mars?",
            "",
            "**Category:** scientific",
            "## Participants",
            "- **Ada** (mock/scripted) — stance: pro",
            "- **Zeno** (mock/scripted) — stance: con",
            "",
            "## Transcript",
            "### Round 1 — Ada",
            "Mars matters.",
            "",
            "### Round 1 — Zeno",
            "Too costly.",
            "",
            "## Outcome: verdict",
            "**Winning position:** pro",
            "",
            "- **Support:** unmeasured — no debater's final position could be read",
            "- **How decided:** moderator's verdict on argument strength",
            "",
            "VERDICT: the pro case prevails.",
            "",
        ]
    )
