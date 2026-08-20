"""Tests for chamber export (Markdown / JSON)."""

from __future__ import annotations

from uuid import uuid4

from cicero.core.compliance import UNOPPOSED_CAVEAT
from cicero.core.export import to_export_dict, to_markdown
from cicero.core.outcome import STANCE_CAVEAT
from cicero.core.prompt_builder import KIND_EVIDENCE, KIND_MODERATOR_NOTE, REASONING_KEY
from cicero.domain.enums import ConsensusOutcome, ProviderType, Stance
from cicero.domain.models import Citation, ConsensusResult, Moderator, StancePoll, Turn
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
    # The roster is a table now, so the names are cells rather than bold runs.
    roster = md.split("## Participants")[1].split("\n## ")[0]
    assert "Ada" in roster and "Zeno" in roster
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
        StancePoll(round_index=0, stances={str(ada.id): Stance.PRO, str(zeno.id): Stance.CON}),
        StancePoll(round_index=1, stances={str(ada.id): Stance.PRO, str(zeno.id): Stance.NEUTRAL}),
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


def test_markdown_keeps_rendering_turns_after_an_empty_one() -> None:
    # An empty turn must be *skipped*, not treated as the end of the transcript:
    # a `break` in place of the `continue` would silently drop every later turn,
    # and a test with only one empty turn cannot tell the two apart.
    a = make_participant("Ada", Stance.PRO)
    chamber = make_chamber(a)
    chamber.turns.append(Turn(participant_id=a.id, round_index=0, content="   "))
    chamber.turns.append(Turn(participant_id=a.id, round_index=1, content="A real point."))
    md = to_markdown(chamber)
    assert "A real point." in md


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
    # Adjacency, not just presence: the description is followed by a blank line
    # that separates it from the next heading, and a bare substring check cannot
    # see whether that separator survived.
    # The description still opens the document; the run configuration now sits
    # between it and the roster.
    assert "Framing: assume a 20-year horizon.\n\n## Run configuration" in md


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
    # System turns are attributed to their role, never to a debater. Assert the
    # rendered heading, not just the content: dropping the speaker label entirely
    # would leave the content present and only the attribution wrong.
    assert "### Round 1 - Moderator note" in md
    assert "### Round 1 - Research (web evidence)" in md
    assert "Stay concrete." in md
    assert "Background reading." in md
    # Scoped to the transcript section: the token-use table further down names
    # every debater, legitimately. The claim here is that a system-authored turn
    # is not attributed to one.
    transcript = md.split("## Transcript")[1].split("\n## ")[0]
    assert "Ada" not in transcript


def test_markdown_labels_a_turn_from_an_unknown_participant() -> None:
    chamber = make_chamber(make_participant("Ada", Stance.PRO))
    chamber.turns.append(Turn(participant_id=uuid4(), round_index=0, content="Who said this?"))
    assert "- unknown" in to_markdown(chamber)


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
    # Whole lines, not substrings: a substring check still matches when the line
    # has been padded or reworded around it, so it cannot see the separator or
    # the list marker actually surviving.
    rendered = md.split("\n")
    assert "Sources:" in rendered
    assert "- [A study](https://example.org/a)" in rendered
    # A blank title falls back to the URL as the link label.
    assert "- [https://example.org/b](https://example.org/b)" in rendered
    # The blank line that separates the sources block from the turn above it.
    assert "Evidence supports this.\n\nSources:\n" in md


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
    # the third (Kant) holds con. A 2-of-2 "majority" is unreachable —
    # decide_outcome reports that as CONSENSUS — so a real MAJORITY fixture
    # needs the non-winner.
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
    assert "- **Support:** contested: 2 of 3 debaters settled on neutral (1 con)" in md
    assert "- **How decided:** majority of final positions" in md


def test_markdown_lists_who_moved() -> None:
    chamber = _concluded_with_headline()
    ada, zeno, kant = chamber.participants
    chamber.stance_history = [
        StancePoll(
            round_index=0,
            stances={str(ada.id): Stance.PRO, str(zeno.id): Stance.CON, str(kant.id): Stance.CON},
        ),
        StancePoll(
            round_index=1,
            stances={
                str(ada.id): Stance.NEUTRAL,
                str(zeno.id): Stance.NEUTRAL,
                str(kant.id): Stance.CON,
            },
        ),
    ]
    md = to_markdown(chamber)
    assert "- **Recorded stance changes:** Ada (pro to neutral), Zeno (con to neutral)" in md


def test_markdown_omits_the_movement_line_when_nobody_moved() -> None:
    assert "**Recorded stance changes:**" not in to_markdown(_concluded_with_headline())


def test_markdown_falls_back_to_the_old_shape_without_a_headline() -> None:
    chamber = _concluded_with_headline()
    chamber.consensus.headline = ""
    md = to_markdown(chamber)
    # Strictly better than before, never broken: the old heading returns, and the
    # derived facts are shown alongside it.
    assert "## Outcome: majority" in md
    assert "**Winning position:** neutral" in md
    assert "- **Support:** contested: 2 of 3 debaters settled on neutral (1 con)" in md


def test_json_export_carries_the_derived_summary_beside_the_chamber() -> None:
    data = to_export_dict(_concluded_with_headline())
    # A sibling key, so the chamber snapshot stays an exact dump of the model.
    assert data["chamber"]["consensus"]["headline"] == (
        "Mars should wait for cheaper launch costs."
    )
    assert data["outcome_summary"] == {
        "headline": "Mars should wait for cheaper launch costs.",
        "support": "contested: 2 of 3 debaters settled on neutral (1 con)",
        "decided_by": "majority of final positions",
        "movements": [],
        # Two debaters settled on neutral, so the labels above need qualifying.
        "caveat": STANCE_CAVEAT,
        # Winner is neutral, which has no polar opposite to leave uncontested,
        # and no turn in this fixture was judged either way.
        "compliance_caveat": "",
        "noncompliance": [],
    }


def test_json_export_has_no_summary_before_a_debate_concludes() -> None:
    chamber = make_chamber(make_participant("Ada", Stance.PRO))
    assert to_export_dict(chamber)["outcome_summary"] is None


def test_markdown_renders_the_compliance_caveat_and_lines() -> None:
    ada = make_participant("Ada", Stance.PRO)
    bob = make_participant("Bob", Stance.CON)
    chamber = make_chamber(ada, bob)
    chamber.consensus = ConsensusResult(
        outcome=ConsensusOutcome.MAJORITY,
        statement="Statement.",
        headline="Pro prevails.",
        winning_stance=Stance.PRO,
        final_stances={str(ada.id): Stance.PRO, str(bob.id): Stance.PRO},
        unparsed=[],
    )
    chamber.turns = [
        Turn(participant_id=bob.id, round_index=0, content="y", metadata={"argued": "pro"}),
    ]
    markdown = to_markdown(chamber)
    assert "Bob (assigned con) argued pro in 1 of 1 judged turns" in markdown
    assert UNOPPOSED_CAVEAT in markdown


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
            "## Run configuration",
            "",
            "| setting | value |",
            "|---|---|",
            "| rounds | min 1, max 8 |",
            "| convergence rounds | 2 |",
            "| decision rule | judge |",
            "| token budget | 200,000 |",
            "| web evidence | False |",
            "| stop on repetition | True |",
            "",
            "## Participants",
            "| debater | provider / model | assigned | temperature | max tokens | reasoning |",
            "|---|---|---|---|---|---|",
            "| Ada | mock/scripted | pro | 0.7 | 2048 | on |",
            "| Zeno | mock/scripted | con | 0.7 | 2048 | on |",
            "",
            "## Transcript",
            "### Round 1 - Ada",
            "",
            "**mock/scripted**, assigned **pro**",
            "",
            "Mars matters.",
            "",
            "### Round 1 - Zeno",
            "",
            "**mock/scripted**, assigned **con**",
            "",
            "Too costly.",
            "",
            "## Outcome: disagreement",
            "",
            "- **Support:** unmeasured: no debater's final position could be read",
            "- **How decided:** unresolved under the judge rule",
            "",
            "No agreement reached.",
            "",
            "## Token use",
            "",
            "| debater | turns | prompt tokens | completion tokens | errors |",
            "|---|---|---|---|---|",
            "| Ada | 1 | 0 | 0 | 0 |",
            "| Zeno | 1 | 0 | 0 | 0 |",
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
            "## Run configuration",
            "",
            "| setting | value |",
            "|---|---|",
            "| rounds | min 1, max 8 |",
            "| convergence rounds | 2 |",
            "| decision rule | judge |",
            "| token budget | 200,000 |",
            "| web evidence | False |",
            "| stop on repetition | True |",
            "",
            "## Participants",
            "| debater | provider / model | assigned | temperature | max tokens | reasoning |",
            "|---|---|---|---|---|---|",
            "| Ada | mock/scripted | pro | 0.7 | 2048 | on |",
            "| Zeno | mock/scripted | con | 0.7 | 2048 | on |",
            "",
            "## Transcript",
            "### Round 1 - Ada",
            "",
            "**mock/scripted**, assigned **pro**",
            "",
            "Mars matters.",
            "",
            "### Round 1 - Zeno",
            "",
            "**mock/scripted**, assigned **con**",
            "",
            "Too costly.",
            "",
            "## Stance history",
            "| Round | Ada | Zeno |",
            "|---|---|---|",
            "| 1 | pro | con (?) |",
            "",
            "`(?)`: the debater's reply could not be read; the previous value "
            "was carried forward and is not evidence of their position.",
            "",
            "## Outcome: disagreement",
            "",
            "- **Support:** unmeasured: no debater's final position could be read",
            "- **How decided:** unresolved under the judge rule",
            "",
            "No agreement reached.",
            "",
            "## Token use",
            "",
            "| debater | turns | prompt tokens | completion tokens | errors |",
            "|---|---|---|---|---|",
            "| Ada | 1 | 0 | 0 | 0 |",
            "| Zeno | 1 | 0 | 0 | 0 |",
            "",
        ]
    )


def test_markdown_matches_expected_layout_for_the_headline_with_movements() -> None:
    chamber = _concluded_with_headline()
    ada, zeno, kant = chamber.participants
    chamber.stance_history = [
        StancePoll(
            round_index=0,
            stances={str(ada.id): Stance.PRO, str(zeno.id): Stance.CON, str(kant.id): Stance.CON},
        ),
        StancePoll(
            round_index=1,
            stances={
                str(ada.id): Stance.NEUTRAL,
                str(zeno.id): Stance.NEUTRAL,
                str(kant.id): Stance.CON,
            },
        ),
    ]
    md = to_markdown(chamber)
    assert md == "\n".join(
        [
            "# Debate: Should we colonise Mars?",
            "",
            "**Category:** scientific",
            "## Run configuration",
            "",
            "| setting | value |",
            "|---|---|",
            "| rounds | min 1, max 8 |",
            "| convergence rounds | 2 |",
            "| decision rule | judge |",
            "| token budget | 200,000 |",
            "| web evidence | False |",
            "| stop on repetition | True |",
            "",
            "## Participants",
            "| debater | provider / model | assigned | temperature | max tokens | reasoning |",
            "|---|---|---|---|---|---|",
            "| Ada | mock/scripted | pro | 0.7 | 2048 | on |",
            "| Zeno | mock/scripted | con | 0.7 | 2048 | on |",
            "| Kant | mock/scripted | con | 0.7 | 2048 | on |",
            "",
            "## Transcript",
            "### Round 1 - Ada",
            "",
            "**mock/scripted**, assigned **pro**",
            "",
            "Mars matters.",
            "",
            "### Round 1 - Zeno",
            "",
            "**mock/scripted**, assigned **con**",
            "",
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
            "- **Support:** contested: 2 of 3 debaters settled on neutral (1 con)",
            "- **How decided:** majority of final positions",
            "- **Recorded stance changes:** Ada (pro to neutral), Zeno (con to neutral)",
            "",
            "*Stance labels record how each debater answered a three-word poll, "
            "not what they argued. A neutral answer covers both holding no "
            "position and holding a compromise the poll has no word for; read "
            "the transcript for what a debater actually held.*",
            "",
            "The majority prevailed.",
            "",
            "## Token use",
            "",
            "| debater | turns | prompt tokens | completion tokens | errors |",
            "|---|---|---|---|---|",
            "| Ada | 1 | 0 | 0 | 0 |",
            "| Zeno | 1 | 0 | 0 | 0 |",
            "| Kant | 0 | 0 | 0 | 0 |",
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
            "## Run configuration",
            "",
            "| setting | value |",
            "|---|---|",
            "| rounds | min 1, max 8 |",
            "| convergence rounds | 2 |",
            "| decision rule | judge |",
            "| token budget | 200,000 |",
            "| web evidence | False |",
            "| stop on repetition | True |",
            "",
            "## Participants",
            "| debater | provider / model | assigned | temperature | max tokens | reasoning |",
            "|---|---|---|---|---|---|",
            "| Ada | mock/scripted | pro | 0.7 | 2048 | on |",
            "| Zeno | mock/scripted | con | 0.7 | 2048 | on |",
            "| Kant | mock/scripted | con | 0.7 | 2048 | on |",
            "",
            "## Transcript",
            "### Round 1 - Ada",
            "",
            "**mock/scripted**, assigned **pro**",
            "",
            "Mars matters.",
            "",
            "### Round 1 - Zeno",
            "",
            "**mock/scripted**, assigned **con**",
            "",
            "Too costly.",
            "",
            "## Outcome: majority",
            "**Winning position:** neutral",
            "",
            "- **Support:** contested: 2 of 3 debaters settled on neutral (1 con)",
            "- **How decided:** majority of final positions",
            "",
            "*Stance labels record how each debater answered a three-word poll, "
            "not what they argued. A neutral answer covers both holding no "
            "position and holding a compromise the poll has no word for; read "
            "the transcript for what a debater actually held.*",
            "",
            "The majority prevailed.",
            "",
            "## Token use",
            "",
            "| debater | turns | prompt tokens | completion tokens | errors |",
            "|---|---|---|---|---|",
            "| Ada | 1 | 0 | 0 | 0 |",
            "| Zeno | 1 | 0 | 0 | 0 |",
            "| Kant | 0 | 0 | 0 | 0 |",
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
            "## Run configuration",
            "",
            "| setting | value |",
            "|---|---|",
            "| rounds | min 1, max 8 |",
            "| convergence rounds | 2 |",
            "| decision rule | judge |",
            "| token budget | 200,000 |",
            "| web evidence | False |",
            "| stop on repetition | True |",
            "",
            "## Participants",
            "| debater | provider / model | assigned | temperature | max tokens | reasoning |",
            "|---|---|---|---|---|---|",
            "| Ada | mock/scripted | pro | 0.7 | 2048 | on |",
            "| Zeno | mock/scripted | con | 0.7 | 2048 | on |",
            "",
            "## Transcript",
            "### Round 1 - Ada",
            "",
            "**mock/scripted**, assigned **pro**",
            "",
            "Mars matters.",
            "",
            "### Round 1 - Zeno",
            "",
            "**mock/scripted**, assigned **con**",
            "",
            "Too costly.",
            "",
            "## Outcome: verdict",
            "**Winning position:** pro",
            "",
            "- **Support:** unmeasured: no debater's final position could be read",
            "- **How decided:** moderator's verdict on argument strength",
            "",
            "VERDICT: the pro case prevails.",
            "",
            "## Token use",
            "",
            "| debater | turns | prompt tokens | completion tokens | errors |",
            "|---|---|---|---|---|",
            "| Ada | 1 | 0 | 0 | 0 |",
            "| Zeno | 1 | 0 | 0 | 0 |",
            "",
        ]
    )


def test_markdown_labels_movement_as_the_poll_record() -> None:
    chamber = _concluded_with_headline()
    ada = chamber.participants[0]
    chamber.stance_history = [
        StancePoll(round_index=0, stances={str(ada.id): Stance.PRO}),
        StancePoll(round_index=1, stances={str(ada.id): Stance.CON}),
    ]
    md = to_markdown(chamber)
    # "Positions moved" reads as a claim about the debater; this is a poll record.
    assert "- **Recorded stance changes:**" in md
    assert "**Positions moved:**" not in md


def test_markdown_carries_the_stance_caveat_when_neutral_is_involved() -> None:
    assert STANCE_CAVEAT in to_markdown(_concluded_with_headline())


def test_markdown_omits_the_caveat_when_no_neutral_is_involved() -> None:
    chamber = _concluded_with_headline()
    ada, zeno, kant = chamber.participants
    chamber.consensus.final_stances = {
        str(ada.id): Stance.PRO,
        str(zeno.id): Stance.PRO,
        str(kant.id): Stance.CON,
    }
    chamber.consensus.winning_stance = Stance.PRO
    assert STANCE_CAVEAT not in to_markdown(chamber)


def test_markdown_names_the_moderator() -> None:
    chamber = _chamber_with_debate()
    chamber.moderator = Moderator(provider=ProviderType.MOCK, model="judge-model")
    # A transcript that does not say who judged is missing something material —
    # the moderator writes the headline and, on a tie, names the winner.
    md = to_markdown(chamber)
    # Whole line, not substring: a substring check still matches when the line has
    # been padded around it, so it cannot see the rendered line actually surviving.
    assert "**Moderator:** mock/judge-model" in md.split("\n")
    # And the blank line that separates it from the next heading.
    assert "**Moderator:** mock/judge-model\n\n## Transcript" in md


def test_markdown_omits_the_moderator_line_when_unset() -> None:
    assert "**Moderator:**" not in to_markdown(_chamber_with_debate())


# --- A complete record, not just a transcript ---------------------------------
# An export that omits the settings that produced the run, the instructions the
# debaters were given, and which model wrote each turn is not a record anyone can
# assess. Everything below is data the app already had and dropped on the way out.


def test_markdown_records_the_settings_that_produced_the_run() -> None:
    chamber = _chamber_with_debate()
    chamber.settings.min_rounds = 3
    chamber.settings.max_rounds = 8
    chamber.settings.convergence_rounds = 0
    chamber.config = {"stop_reason": "max_rounds", "rounds_completed": 8, "tokens_used": 1234}

    md = to_markdown(chamber)

    assert "## Run configuration" in md
    assert "min 3, max 8" in md
    assert "max_rounds" in md  # the stop reason
    assert "1,234" in md


def test_markdown_records_each_debaters_tuning() -> None:
    """The roster named the model but not how it was configured, so two runs that
    differed only in temperature exported identically."""
    chamber = _chamber_with_debate()
    ada = chamber.participants[0]
    ada.tuning.temperature = 0.2
    ada.tuning.max_tokens = 1500
    ada.tuning.allow_reasoning = False

    md = to_markdown(chamber)

    assert "0.2" in md and "1500" in md
    assert "off" in md  # reasoning


def test_markdown_records_the_instructions_the_debaters_were_given() -> None:
    chamber = _chamber_with_debate()
    for participant in chamber.participants:
        participant.tuning.instructions = "Speak as a politician at a rally."

    md = to_markdown(chamber)

    assert "Speak as a politician at a rally." in md


def test_markdown_notes_when_every_debater_shares_the_same_instructions() -> None:
    """Worth saying explicitly: identical instructions are what make a
    model-vs-model comparison a comparison of models."""
    chamber = _chamber_with_debate()
    for participant in chamber.participants:
        participant.tuning.instructions = "Speak as a politician at a rally."

    md = to_markdown(chamber)

    assert md.count("Speak as a politician at a rally.") == 1
    assert "identical" in md.lower()


def test_markdown_lists_differing_instructions_per_debater() -> None:
    chamber = _chamber_with_debate()
    chamber.participants[0].tuning.instructions = "Be terse."
    chamber.participants[1].tuning.instructions = "Speak in rhyme."

    md = to_markdown(chamber)

    assert "Be terse." in md and "Speak in rhyme." in md


def test_markdown_attributes_each_turn_to_its_model_and_verdict() -> None:
    """Which model produced a turn, and what the judge made of it, is the whole
    point of a run where the model is the variable under test."""
    chamber = _chamber_with_debate()
    chamber.turns[0].metadata.update(
        {"argued": "con", "prompt_tokens": 10, "completion_tokens": 20}
    )

    md = to_markdown(chamber)

    assert "mock/scripted" in md
    assert "con" in md


def test_markdown_marks_model_reasoning_as_outside_the_debate() -> None:
    """Narration is a record of what the model produced, not of what the debate
    saw — the engine strips it before the turn is stored, so no debater, the
    moderator or the judge ever read it (#30). Rendering it unlabelled beside the
    speech would undo that distinction for a reader."""
    chamber = _chamber_with_debate()
    chamber.turns[0].metadata[REASONING_KEY] = "Okay, let me unpack this."

    md = to_markdown(chamber)

    assert "Okay, let me unpack this." in md
    assert "not part of the debate" in md.lower()


def test_markdown_has_no_reasoning_section_when_there_was_none() -> None:
    md = to_markdown(_chamber_with_debate())
    assert "not part of the debate" not in md.lower()


def test_markdown_reports_token_use_per_debater() -> None:
    chamber = _chamber_with_debate()
    chamber.turns[0].metadata.update({"prompt_tokens": 10, "completion_tokens": 20})

    md = to_markdown(chamber)

    assert "## Token use" in md
    assert "Ada" in md


# --- The record's details, pinned ---------------------------------------------
# Each of these covers a branch the layout tests never reach, because the chamber
# they build does not set the field. A row nobody asserts is a row that can be
# renamed or dropped without a test noticing.


def test_markdown_names_the_run_configuration_rows_exactly() -> None:
    chamber = _chamber_with_debate()
    chamber.settings.max_duration_seconds = 90
    chamber.config = {"stop_reason": "consensus", "rounds_completed": 4, "tokens_used": 5678}

    md = to_markdown(chamber)

    for row in (
        "| rounds | min 1, max 8 |",
        "| convergence rounds | 2 |",
        "| decision rule | judge |",
        "| token budget | 200,000 |",
        "| web evidence | False |",
        "| stop on repetition | True |",
        "| wall-clock limit | 90s |",
        "| stopped because | consensus |",
        "| rounds completed | 4 |",
        "| tokens used | 5,678 |",
    ):
        assert row in md, row


def test_markdown_omits_run_rows_that_have_no_value() -> None:
    """A debate with no wall-clock cap and no recorded ending should not show
    empty rows for them — an export that says "stopped because: None" reads as a
    failure rather than as a run still in progress."""
    md = to_markdown(_chamber_with_debate())
    assert "wall-clock limit" not in md
    assert "stopped because" not in md
    assert "tokens used" not in md


def test_markdown_shows_the_moderator_and_its_settings() -> None:
    chamber = _chamber_with_debate()
    chamber.moderator = Moderator(
        provider=ProviderType.MOCK, model="arbiter", max_tokens=4096, temperature=0.0
    )

    md = to_markdown(chamber)

    assert "| moderator | mock/arbiter (max_tokens 4096, temperature 0.0) |" in md


def test_markdown_spells_out_whether_reasoning_was_allowed() -> None:
    chamber = _chamber_with_debate()
    chamber.participants[0].tuning.allow_reasoning = True
    chamber.participants[1].tuning.allow_reasoning = False

    md = to_markdown(chamber)

    assert "| Ada | mock/scripted | pro | 0.7 | 2048 | on |" in md
    assert "| Zeno | mock/scripted | con | 0.7 | 2048 | off |" in md


def test_markdown_lists_personas_when_any_debater_has_one() -> None:
    chamber = _chamber_with_debate()
    chamber.participants[0].tuning.persona = "a cautious economist"

    md = to_markdown(chamber)

    assert "**Personas:**" in md
    assert "- **Ada:** a cautious economist" in md
    assert "Zeno" not in md.split("**Personas:**")[1].split("\n## ")[0]


def test_markdown_has_no_persona_section_when_nobody_has_one() -> None:
    assert "**Personas:**" not in to_markdown(_chamber_with_debate())


def test_markdown_marks_differing_instructions_as_differing() -> None:
    chamber = _chamber_with_debate()
    chamber.participants[0].tuning.instructions = "Be terse."

    md = to_markdown(chamber)

    assert "**Instructions** (these differ by debater):" in md
    assert "- **Ada:** Be terse." in md
    assert "- **Zeno:** _(none)_" in md


def test_markdown_says_when_instructions_are_shared() -> None:
    chamber = _chamber_with_debate()
    for participant in chamber.participants:
        participant.tuning.instructions = "Speak plainly."

    md = to_markdown(chamber)

    assert "**Instructions** (identical for every debater):" in md
    assert "> Speak plainly." in md


def test_markdown_turn_provenance_carries_every_fact_it_has() -> None:
    chamber = _chamber_with_debate()
    chamber.turns[0].metadata.update(
        {"argued": "con", "prompt_tokens": 11, "completion_tokens": 22, "repeated": True}
    )

    md = to_markdown(chamber)

    assert (
        "**mock/scripted**, assigned **pro**, judged as **con**, "
        "11+22 tokens, **flagged as a repeat**" in md
    )


def test_markdown_turn_provenance_reports_one_sided_token_counts() -> None:
    """A turn that recorded only one half still gets a token line; requiring both
    would silently drop the count for a turn that failed partway."""
    chamber = _chamber_with_debate()
    chamber.turns[0].metadata["prompt_tokens"] = 11

    assert "11+0 tokens" in to_markdown(chamber)


def test_markdown_turn_provenance_shows_a_provider_error() -> None:
    chamber = _chamber_with_debate()
    chamber.turns[0].metadata["error"] = "provider is down"

    assert "**provider error:** provider is down" in to_markdown(chamber)


def test_markdown_gives_system_turns_no_provenance_line() -> None:
    """A moderator note has no model, no assigned stance and no verdict; inventing
    a line for it would attribute a debater's properties to the system."""
    chamber = _chamber_with_debate()
    chamber.turns.append(
        Turn(round_index=0, content="Stay concrete.", metadata={"kind": KIND_MODERATOR_NOTE})
    )

    md = to_markdown(chamber)
    section = md.split("### Round 1 - Moderator note")[1]
    assert "assigned" not in section.split("\n## ")[0]


def test_markdown_reasoning_note_states_who_did_not_see_it() -> None:
    """The load-bearing half of the note. Rendering narration beside the speech
    without saying it was stripped would let a reader think the debate saw it."""
    chamber = _chamber_with_debate()
    chamber.turns[0].metadata[REASONING_KEY] = "Okay, let me unpack this."

    md = to_markdown(chamber)

    assert "#### Model reasoning (not part of the debate)" in md
    # Fenced by rules at both ends, so the boundary survives a converter that
    # renders bold and blockquotes faintly.
    assert "\n---\n\n#### Model reasoning" in md
    assert "> Okay, let me unpack this." in md
    assert "no other debater, the moderator" in md
    assert "did not shape the debate" in md


def test_markdown_token_use_rows_are_exact() -> None:
    chamber = _chamber_with_debate()
    chamber.turns[0].metadata.update({"prompt_tokens": 100, "completion_tokens": 250})

    md = to_markdown(chamber)

    assert "| Ada | 1 | 100 | 250 | 0 |" in md
    assert "| Zeno | 1 | 0 | 0 | 0 |" in md


def test_markdown_blockquotes_every_line_of_multi_line_reasoning() -> None:
    """Narration runs to paragraphs. A blockquote that only marked the first line
    would leave the rest reading as the debate's own prose."""
    chamber = _chamber_with_debate()
    chamber.turns[0].metadata[REASONING_KEY] = "First thought.\n\nSecond thought."

    md = to_markdown(chamber)

    assert "> First thought.\n>\n> Second thought." in md


def test_markdown_reports_a_genuine_zero_token_count() -> None:
    """`or 0` must be a fallback for a *missing* count, not a rewrite of a real
    one: a turn that truly cost 0 prompt tokens has to report 0."""
    chamber = _chamber_with_debate()
    chamber.turns[0].metadata.update({"prompt_tokens": 0, "completion_tokens": 7})

    assert "0+7 tokens" in to_markdown(chamber)


def test_markdown_puts_nothing_between_a_system_turn_and_its_text() -> None:
    """`_turn_provenance` returns empty for a system turn, and empty must mean
    *nothing rendered* — not a blank marker line between the heading and the
    note."""
    chamber = _chamber_with_debate()
    chamber.turns.append(
        Turn(round_index=0, content="Stay concrete.", metadata={"kind": KIND_MODERATOR_NOTE})
    )

    md = to_markdown(chamber)

    assert "### Round 1 - Moderator note\nStay concrete." in md


def test_markdown_scaffolding_is_ascii() -> None:
    """The export must not *add* characters a downstream converter may choke on.

    It cannot promise an ASCII file: the turns are the record, and models emit
    curly quotes, en dashes and accented words. Measured on one real export, 219
    of 287 non-ASCII characters were inside the debaters' own prose. What the
    export controls is its own scaffolding — headings, labels, separators — and a
    chamber whose every input is ASCII should therefore render as ASCII.

    Reported from a real pipeline: an md-to-PDF converter mangled the em dashes,
    and an editor flagged the U+00B7 separator this file used between the facts
    on a turn's provenance line.
    """
    chamber = _chamber_with_debate()
    chamber.description = "Plain ASCII description."
    for participant in chamber.participants:
        participant.tuning.persona = "an economist"
        participant.tuning.instructions = "Be terse."
    chamber.moderator = Moderator(provider=ProviderType.MOCK, model="arbiter")
    chamber.settings.max_duration_seconds = 60
    chamber.config = {"stop_reason": "max_rounds", "rounds_completed": 1, "tokens_used": 10}
    chamber.turns[0].metadata.update(
        {"argued": "con", "prompt_tokens": 1, "completion_tokens": 2, "repeated": True}
    )
    chamber.turns[1].metadata[REASONING_KEY] = "Plain reasoning.\n\nSecond line."
    chamber.stance_history.append(
        StancePoll(
            round_index=0,
            stances={str(chamber.participants[0].id): Stance.PRO},
            unparsed=(str(chamber.participants[1].id),),
        )
    )

    md = to_markdown(chamber)

    offenders = sorted({character for character in md if ord(character) > 127})
    assert not offenders, f"non-ASCII in export scaffolding: {offenders}"
