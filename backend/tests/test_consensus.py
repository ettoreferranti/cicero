"""Tests for the hybrid consensus engine."""

from __future__ import annotations

import pytest

from cicero.core.consensus import (
    ConsensusEngine,
    decide_outcome,
    is_consensus,
    majority_stance,
    parse_directives,
    parse_moderator_reply,
    parse_stance,
)
from cicero.core.prompts import DIRECTIVE_HEADLINE
from cicero.domain.enums import ConsensusOutcome, DecisionRule, Stance
from cicero.domain.models import MAX_HEADLINE_LENGTH, StancePoll
from cicero.providers.base import GenerateOptions
from tests.conftest import (
    ConstantFactory,
    ScriptedProvider,
    StubFactory,
    make_chamber,
    make_participant,
)

MOD_OPTS = GenerateOptions(model="mod")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("pro", Stance.PRO),
        ("CON", Stance.CON),
        ("neutral", Stance.NEUTRAL),
        ("I am pro this idea", Stance.PRO),
        ("My position: neutral for now", Stance.NEUTRAL),
        ("", None),
        ("undecided maybe", Stance.NEUTRAL),
        # Punctuation and markdown around a one-word answer.
        ("Con.", Stance.CON),
        ("**pro**", Stance.PRO),
        ("  NEUTRAL  ", Stance.NEUTRAL),
        # Words the poll never names but models reach for when they concede.
        ("against", Stance.CON),
        ("Against the motion.", Stance.CON),
        ("opposed", Stance.CON),
        ("no", Stance.CON),
        ("yes", Stance.PRO),
        ("support", Stance.PRO),
    ],
)
def test_parse_stance(text: str, expected: Stance | None) -> None:
    assert parse_stance(text) is expected


@pytest.mark.parametrize(
    "text",
    [
        # The bug this replaced: substring matching read a debater arguing to
        # *prohibit* the motion as being *pro* it, exactly inverting a
        # concession. Same for "protect"/"propose", and "con" in "context",
        # "concede", "concern", "conflating".
        "prohibit",
        "Prohibited",
        "I now support prohibiting the shirt.",
        "My position is that we should protect this speech.",
        "The workplace context changes things.",
        "I concede.",
        "That is a serious concern.",
        "I have been persuaded to change my position.",
    ],
)
def test_parse_stance_never_guesses_from_a_substring(text: str) -> None:
    # Unreadable is the honest answer; a wrong stance is worse than no stance.
    assert parse_stance(text) is None


def test_parse_stance_ignores_reasoning_narration() -> None:
    # Reasoning models argue both sides before answering; scanning that would
    # score whichever side they happened to consider first.
    reply = "<think>They want one word. Is it pro? No, I am against now.</think>\ncon"
    assert parse_stance(reply) is Stance.CON
    assert parse_stance("<think>pro pro pro</think> neutral") is Stance.NEUTRAL


def test_parse_stance_prefers_earliest_keyword() -> None:
    # "con" appears before "pro" here → con wins.
    assert parse_stance("I lean con, definitely not pro") is Stance.CON


def test_is_consensus_rules() -> None:
    assert is_consensus({}) is False
    assert is_consensus({"a": Stance.PRO}) is True
    assert is_consensus({"a": Stance.PRO, "b": Stance.PRO}) is True
    assert is_consensus({"a": Stance.PRO, "b": Stance.CON}) is False


async def test_poll_stances_parses_each_participant() -> None:
    pro = make_participant("Pro", Stance.PRO)
    con = make_participant("Con", Stance.CON)
    chamber = make_chamber(pro, con)
    factory = StubFactory(
        {
            pro.id: ScriptedProvider(stance_word="pro"),
            con.id: ScriptedProvider(stance_word="con"),
        }
    )
    engine = ConsensusEngine(factory, ScriptedProvider(), MOD_OPTS)
    report = await engine.poll_stances(chamber)
    assert report.stances == {str(pro.id): Stance.PRO, str(con.id): Stance.CON}
    assert report.unparsed == ()


async def test_poll_uses_parsed_stance_over_declared_stance() -> None:
    # Declared NEUTRAL but the model now reports "pro": the parsed value must win,
    # proving the poll actually parses the response (not just echoes the stance).
    p = make_participant("Mover", Stance.NEUTRAL)
    chamber = make_chamber(p, make_participant("Other", Stance.CON))
    factory = StubFactory(
        {pt.id: ScriptedProvider(stance_word="pro") for pt in chamber.participants}
    )
    engine = ConsensusEngine(factory, ScriptedProvider(), MOD_OPTS)
    report = await engine.poll_stances(chamber)
    assert report.stances[str(p.id)] is Stance.PRO


async def test_poll_reports_unparseable_replies_instead_of_hiding_them() -> None:
    p = make_participant("P", Stance.CON)
    chamber = make_chamber(p, make_participant("Q", Stance.PRO))
    factory = StubFactory(
        {pid: ScriptedProvider(stance_word="???") for pid in [pt.id for pt in chamber.participants]}
    )
    engine = ConsensusEngine(factory, ScriptedProvider(), MOD_OPTS)
    report = await engine.poll_stances(chamber)

    # A value is still supplied so the tally is complete...
    assert report.stances[str(p.id)] is Stance.CON
    # ...but it is flagged as carried over, not measured. Without this, a run
    # where every poll failed is indistinguishable from one where nobody moved.
    assert set(report.unparsed) == {str(pt.id) for pt in chamber.participants}


async def test_poll_disables_reasoning() -> None:
    # The poll wants one word. A thinking model left to reason can spend the
    # whole budget and answer nothing at all (qwen3 does).
    seen: list[GenerateOptions] = []

    class Recording(ScriptedProvider):
        async def generate(self, messages, options):  # type: ignore[no-untyped-def]
            seen.append(options)
            return await super().generate(messages, options)

    p = make_participant("P", Stance.PRO)
    chamber = make_chamber(p, make_participant("Q", Stance.CON))
    factory = StubFactory({pt.id: Recording(stance_word="pro") for pt in chamber.participants})
    engine = ConsensusEngine(factory, ScriptedProvider(), MOD_OPTS)

    await engine.poll_stances(chamber)

    assert seen and all(options.allow_reasoning is False for options in seen)


async def test_a_never_measured_stance_does_not_decide_the_outcome() -> None:
    # Two debaters: A measured PRO, B never readable so carrying its assigned
    # CON. Counting B's phantom vote would make this a tie and send it to the
    # judge; only A's real vote should decide it.
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    factory = StubFactory({pt.id: ScriptedProvider() for pt in chamber.participants})
    engine = ConsensusEngine(factory, ScriptedProvider(moderator_reply="S."), MOD_OPTS)

    result = await engine.finalize(
        chamber,
        {str(a.id): Stance.PRO, str(b.id): Stance.CON},
        unparsed=[str(b.id)],
    )

    assert result.outcome is ConsensusOutcome.CONSENSUS
    assert result.winning_stance is Stance.PRO
    # Every stance is still recorded, including the one that did not vote.
    assert result.final_stances[str(b.id)] is Stance.CON


async def test_poll_carries_the_previous_measurement_not_the_declared_role() -> None:
    # A debater who already crossed the floor must not be silently reset to the
    # stance they were assigned at the start just because one reply was unclear.
    p = make_participant("Mover", Stance.PRO)
    other = make_participant("Other", Stance.CON)
    chamber = make_chamber(p, other)
    chamber.stance_history.append(
        StancePoll(round_index=0, stances={str(p.id): Stance.CON, str(other.id): Stance.CON})
    )
    factory = StubFactory(
        {pt.id: ScriptedProvider(stance_word="???") for pt in chamber.participants}
    )
    engine = ConsensusEngine(factory, ScriptedProvider(), MOD_OPTS)
    report = await engine.poll_stances(chamber)

    assert report.stances[str(p.id)] is Stance.CON  # kept, not reverted to PRO
    assert str(p.id) in report.unparsed


async def test_finalize_consensus_outcome() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.PRO)
    chamber = make_chamber(a, b)
    moderator = ScriptedProvider(moderator_reply="We agree Mars matters.")
    engine = ConsensusEngine(StubFactory({}), moderator, MOD_OPTS)
    stances = {str(a.id): Stance.PRO, str(b.id): Stance.PRO}
    result = await engine.finalize(chamber, stances)
    assert result.outcome is ConsensusOutcome.CONSENSUS
    assert result.statement == "We agree Mars matters."
    assert result.final_stances == stances


def test_majority_stance_rules() -> None:
    assert majority_stance({}) is None
    assert majority_stance({"a": Stance.PRO}) is Stance.PRO
    assert majority_stance({"a": Stance.PRO, "b": Stance.CON}) is None  # tie
    assert (
        majority_stance({"a": Stance.PRO, "b": Stance.PRO, "c": Stance.CON}) is Stance.PRO
    )
    assert (
        majority_stance({"a": Stance.CON, "b": Stance.CON, "c": Stance.PRO, "d": Stance.NEUTRAL})
        is Stance.CON
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", {}),
        ("Just prose.", {}),
        ("WINNER: pro\nBody.", {"WINNER": "pro"}),
        ("winner: CON\nBody.", {"WINNER": "CON"}),
        ("HEADLINE: We should go.\nWINNER: pro\nBody.",
         {"HEADLINE": "We should go.", "WINNER": "pro"}),
        # Order must not matter: the two prompts are written independently.
        ("WINNER: pro\nHEADLINE: We should go.\nBody.",
         {"WINNER": "pro", "HEADLINE": "We should go."}),
        ("  HEADLINE: Indented.\nBody.", {"HEADLINE": "Indented."}),
        ("HEADLINE:\nBody.", {"HEADLINE": ""}),
        # An unrecognised key is prose, and stops the peel.
        ("VERDICT: pro\nHEADLINE: never reached.", {}),
        # A directive after prose is prose.
        ("Body.\nHEADLINE: too late.", {}),
        # A repeated key is not a second directive: the first occurrence wins
        # and the repeat stops the peel, same as an unrecognised key would.
        ("WINNER: pro\nWINNER: con\nBody.", {"WINNER": "pro"}),
        # Real Ollama models reliably put a blank line between HEADLINE and
        # WINNER; the peel must not stop there, or WINNER is silently dropped.
        (
            "HEADLINE: We should go.\n\nWINNER: pro\n\nBody.",
            {"HEADLINE": "We should go.", "WINNER": "pro"},
        ),
        # Multiple consecutive blank lines between directives.
        (
            "HEADLINE: We should go.\n\n\nWINNER: pro\nBody.",
            {"HEADLINE": "We should go.", "WINNER": "pro"},
        ),
        # A blank line before the very first directive: already handled by the
        # leading strip(), pinned here so a future change cannot regress it.
        (
            "\nHEADLINE: We should go.\nWINNER: pro\nBody.",
            {"HEADLINE": "We should go.", "WINNER": "pro"},
        ),
    ],
)
def test_parse_directives_peels_leading_keys(text: str, expected: dict[str, str]) -> None:
    directives, _ = parse_directives(text, frozenset({"HEADLINE", "WINNER"}))
    assert directives == expected


@pytest.mark.parametrize(
    ("text", "body"),
    [
        ("WINNER: pro\nBody.", "Body."),
        ("HEADLINE: One.\nWINNER: pro\nBody one.\nBody two.", "Body one.\nBody two."),
        ("Just prose.", "Just prose."),
        # Nothing left after the directives: the caller needs *something*, so the
        # original text is returned rather than an empty statement.
        ("WINNER: pro", "WINNER: pro"),
        # The repeated key falls through to the body, pinning where it lands.
        ("WINNER: pro\nWINNER: con\nBody.", "WINNER: con\nBody."),
        # Blank lines between the two directives must not surface in the body,
        # and the blank right before the body must not leave a leading blank.
        ("HEADLINE: One.\n\nWINNER: pro\n\nBody one.\nBody two.", "Body one.\nBody two."),
        # A blank line internal to the body itself (a paragraph break) is real
        # content and must survive, not just the separator blanks are dropped.
        (
            "HEADLINE: One.\n\nWINNER: pro\n\nBody one.\n\nBody two.",
            "Body one.\n\nBody two.",
        ),
    ],
)
def test_parse_directives_returns_remaining_body(text: str, body: str) -> None:
    _, remaining = parse_directives(text, frozenset({"HEADLINE", "WINNER"}))
    assert remaining == body


@pytest.mark.parametrize(
    ("text", "winner", "body"),
    [
        ("WINNER: pro\nStrong case.", Stance.PRO, "Strong case."),
        ("winner: CON\nThe cons had it.", Stance.CON, "The cons had it."),
        ("WINNER: neutral\nNobody moved.", Stance.NEUTRAL, "Nobody moved."),
        ("No verdict line here.", None, "No verdict line here."),
        # A failed directive is not content: it is consumed, and no winner read.
        ("WINNER: maybe\ntext", None, "text"),
    ],
)
def test_parse_moderator_reply_reads_the_winner(
    text: str, winner: Stance | None, body: str
) -> None:
    reply = parse_moderator_reply(text)
    assert reply.winner is winner
    assert reply.body == body


def test_parse_moderator_reply_without_body_keeps_full_text() -> None:
    reply = parse_moderator_reply("WINNER: pro")
    assert reply.winner is Stance.PRO
    assert reply.body == "WINNER: pro"


def test_parse_moderator_reply_reads_the_headline() -> None:
    reply = parse_moderator_reply("HEADLINE: Remote work should be the default.\nBecause X.")
    assert reply.headline == "Remote work should be the default."
    assert reply.body == "Because X."


def test_parse_moderator_reply_without_a_headline_reports_none() -> None:
    assert parse_moderator_reply("Just the statement.").headline == ""


def test_parse_moderator_reply_ignores_an_empty_headline() -> None:
    assert parse_moderator_reply("HEADLINE:\nStatement.").headline == ""


def test_parse_moderator_reply_keeps_a_headline_at_the_length_cap() -> None:
    # Pins the boundary as inclusive: exactly MAX_HEADLINE_LENGTH is still a
    # headline, only *longer than* the cap is dropped.
    headline = "x" * MAX_HEADLINE_LENGTH
    reply = parse_moderator_reply(f"HEADLINE: {headline}\nStatement.")
    assert reply.headline == headline
    assert reply.body == "Statement."


def test_parse_moderator_reply_drops_an_overlong_headline() -> None:
    # A moderator that put its whole statement on the HEADLINE line has not
    # written a headline. Dropping the value (rather than truncating it) is what
    # makes the display fall back instead of showing half a sentence as the
    # chamber's conclusion.
    reply = parse_moderator_reply("HEADLINE: " + "x" * 501 + "\nStatement.")
    assert reply.headline == ""
    assert reply.body == "Statement."


def test_parse_moderator_reply_takes_only_the_first_line_as_headline() -> None:
    reply = parse_moderator_reply("HEADLINE: One sentence.\nThe longer statement.\nMore.")
    assert reply.headline == "One sentence."
    assert reply.body == "The longer statement.\nMore."


def test_parse_moderator_reply_reads_both_directives_across_a_blank_line() -> None:
    # Real Ollama models reliably separate HEADLINE and WINNER with a blank
    # line (see the module docstring on parse_directives). Before the fix this
    # broke the peel after HEADLINE, so WINNER was never read: winning_stance
    # stayed None and the literal "WINNER: pro" leaked into the statement.
    text = (
        "HEADLINE: Cities should invest in zero-emission transit.\n"
        "\n"
        "WINNER: pro\n"
        "\n"
        "The pro side presented a compelling case."
    )
    reply = parse_moderator_reply(text)
    assert reply.headline == "Cities should invest in zero-emission transit."
    assert reply.winner is Stance.PRO
    assert reply.body == "The pro side presented a compelling case."


def test_parse_moderator_reply_reads_the_winner_with_no_blank_line() -> None:
    # Pins the pre-existing (non-blank-line) shape so the fix cannot regress it.
    reply = parse_moderator_reply("HEADLINE: Title.\nWINNER: pro\nBody.")
    assert reply.headline == "Title."
    assert reply.winner is Stance.PRO
    assert reply.body == "Body."


def test_parse_moderator_reply_with_a_narrowed_key_set_keeps_a_winner_shaped_sentence() -> None:
    # Only a VERDICT reply is actually asked for WINNER:. Outside that, a body
    # sentence that happens to start "Winner: ..." must not be peeled off as a
    # directive and silently deleted from the statement — passing a key set
    # without DIRECTIVE_WINNER is what keeps it in the body.
    text = (
        "HEADLINE: Cities should invest in transit.\n"
        "\n"
        "Winner: the pro side, because the cost case was decisive.\n"
        "\n"
        "More reasoning."
    )
    reply = parse_moderator_reply(text, keys=frozenset({DIRECTIVE_HEADLINE}))
    assert reply.headline == "Cities should invest in transit."
    assert reply.winner is None
    assert reply.body == (
        "Winner: the pro side, because the cost case was decisive.\n\nMore reasoning."
    )


async def test_finalize_keeps_a_winner_shaped_sentence_for_a_non_verdict_outcome() -> None:
    # End-to-end reproduction of the regression introduced by 475c20b: a
    # CONSENSUS reply whose body opens with "Winner: ..." must keep that
    # sentence in the published statement, because only VERDICT outcomes were
    # ever prompted for a WINNER: directive.
    ada = make_participant("Ada", Stance.PRO)
    zeno = make_participant("Zeno", Stance.PRO)
    chamber = make_chamber(ada, zeno)
    provider = ScriptedProvider(
        moderator_reply=(
            "HEADLINE: Cities should invest in transit.\n"
            "\n"
            "Winner: the pro side, because the cost case was decisive.\n"
            "\n"
            "More reasoning."
        )
    )
    engine = ConsensusEngine(ConstantFactory(provider), provider, GenerateOptions(model="m"))

    result = await engine.finalize(
        chamber, {str(ada.id): Stance.PRO, str(zeno.id): Stance.PRO}
    )

    assert result.outcome is ConsensusOutcome.CONSENSUS
    assert result.statement == (
        "Winner: the pro side, because the cost case was decisive.\n\nMore reasoning."
    )


def test_decide_outcome_per_rule() -> None:
    unanimous = {"a": Stance.PRO, "b": Stance.PRO}
    majority = {"a": Stance.PRO, "b": Stance.PRO, "c": Stance.CON}
    tie = {"a": Stance.PRO, "b": Stance.CON}

    for rule in DecisionRule:
        assert decide_outcome(unanimous, rule) == (ConsensusOutcome.CONSENSUS, Stance.PRO)

    assert decide_outcome(majority, DecisionRule.UNANIMOUS) == (
        ConsensusOutcome.DISAGREEMENT,
        None,
    )
    assert decide_outcome(majority, DecisionRule.MAJORITY) == (
        ConsensusOutcome.MAJORITY,
        Stance.PRO,
    )
    assert decide_outcome(majority, DecisionRule.JUDGE) == (
        ConsensusOutcome.MAJORITY,
        Stance.PRO,
    )
    assert decide_outcome(tie, DecisionRule.MAJORITY) == (ConsensusOutcome.DISAGREEMENT, None)
    # A judge always produces a winner — the tie goes to the moderator's verdict.
    assert decide_outcome(tie, DecisionRule.JUDGE) == (ConsensusOutcome.VERDICT, None)


async def test_finalize_disagreement_outcome_under_unanimous_rule() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    chamber.settings.decision_rule = DecisionRule.UNANIMOUS
    engine = ConsensusEngine(StubFactory({}), ScriptedProvider(), MOD_OPTS)
    stances = {str(a.id): Stance.PRO, str(b.id): Stance.CON}
    result = await engine.finalize(chamber, stances)
    assert result.outcome is ConsensusOutcome.DISAGREEMENT
    assert result.winning_stance is None


async def test_finalize_majority_names_the_winner() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.PRO)
    c = make_participant("C", Stance.CON)
    chamber = make_chamber(a, b, c)  # default rule: judge → majority path
    moderator = ScriptedProvider(moderator_reply="Pro carried the day.")
    engine = ConsensusEngine(StubFactory({}), moderator, MOD_OPTS)
    stances = {str(a.id): Stance.PRO, str(b.id): Stance.PRO, str(c.id): Stance.CON}
    result = await engine.finalize(chamber, stances)
    assert result.outcome is ConsensusOutcome.MAJORITY
    assert result.winning_stance is Stance.PRO
    assert result.statement == "Pro carried the day."


async def test_finalize_judge_breaks_a_tie_with_a_verdict() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)  # default rule: judge; pro/con is a tie
    moderator = ScriptedProvider(moderator_reply="WINNER: con\nThe risks outweighed.")
    engine = ConsensusEngine(StubFactory({}), moderator, MOD_OPTS)
    stances = {str(a.id): Stance.PRO, str(b.id): Stance.CON}
    result = await engine.finalize(chamber, stances)
    assert result.outcome is ConsensusOutcome.VERDICT
    assert result.winning_stance is Stance.CON
    assert result.statement == "The risks outweighed."


async def test_finalize_uses_fallback_when_moderator_is_empty() -> None:
    from cicero.core.prompts import EMPTY_MODERATOR_STATEMENT

    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.PRO)
    chamber = make_chamber(a, b)
    moderator = ScriptedProvider(moderator_reply="   ")  # whitespace only
    engine = ConsensusEngine(StubFactory({}), moderator, MOD_OPTS)
    result = await engine.finalize(chamber, {str(a.id): Stance.PRO, str(b.id): Stance.PRO})
    assert result.statement == EMPTY_MODERATOR_STATEMENT


async def test_finalize_records_the_moderator_headline() -> None:
    ada = make_participant("Ada", Stance.PRO)
    zeno = make_participant("Zeno", Stance.PRO)
    chamber = make_chamber(ada, zeno)
    provider = ScriptedProvider(
        moderator_reply="HEADLINE: Mars should wait.\nThe cost case was decisive."
    )
    engine = ConsensusEngine(ConstantFactory(provider), provider, GenerateOptions(model="m"))

    result = await engine.finalize(
        chamber, {str(ada.id): Stance.PRO, str(zeno.id): Stance.PRO}
    )

    assert result.headline == "Mars should wait."
    assert result.statement == "The cost case was decisive."


async def test_finalize_leaves_the_headline_empty_when_the_moderator_omits_it() -> None:
    ada = make_participant("Ada", Stance.PRO)
    zeno = make_participant("Zeno", Stance.PRO)
    chamber = make_chamber(ada, zeno)
    provider = ScriptedProvider(moderator_reply="They agreed on the cost case.")
    engine = ConsensusEngine(ConstantFactory(provider), provider, GenerateOptions(model="m"))

    result = await engine.finalize(
        chamber, {str(ada.id): Stance.PRO, str(zeno.id): Stance.PRO}
    )

    # Never back-filled from the statement: a summary the moderator did not write
    # must not be presented as one it did.
    assert result.headline == ""
    assert result.statement == "They agreed on the cost case."


async def test_finalize_records_the_unparsed_set_it_decided_on() -> None:
    ada = make_participant("Ada", Stance.PRO)
    zeno = make_participant("Zeno", Stance.CON)
    chamber = make_chamber(ada, zeno)
    provider = ScriptedProvider(moderator_reply="HEADLINE: Unclear.\nNo agreement.")
    engine = ConsensusEngine(ConstantFactory(provider), provider, GenerateOptions(model="m"))

    result = await engine.finalize(
        chamber, {str(ada.id): Stance.PRO, str(zeno.id): Stance.CON}, unparsed=[str(zeno.id)]
    )

    assert result.unparsed == [str(zeno.id)]


async def test_finalize_records_an_empty_unparsed_set_rather_than_none() -> None:
    ada = make_participant("Ada", Stance.PRO)
    zeno = make_participant("Zeno", Stance.PRO)
    chamber = make_chamber(ada, zeno)
    provider = ScriptedProvider(moderator_reply="HEADLINE: Agreed.\nBoth sides aligned.")
    engine = ConsensusEngine(ConstantFactory(provider), provider, GenerateOptions(model="m"))

    result = await engine.finalize(
        chamber, {str(ada.id): Stance.PRO, str(zeno.id): Stance.PRO}
    )

    # A freshly concluded chamber always knows; only historical ones say None.
    assert result.unparsed == []


async def test_finalize_reads_a_verdict_that_also_carries_a_headline() -> None:
    ada = make_participant("Ada", Stance.PRO)
    zeno = make_participant("Zeno", Stance.CON)
    chamber = make_chamber(ada, zeno)
    chamber.settings.decision_rule = DecisionRule.JUDGE
    provider = ScriptedProvider(
        moderator_reply="HEADLINE: The pro case won.\nWINNER: pro\nBetter evidence."
    )
    engine = ConsensusEngine(ConstantFactory(provider), provider, GenerateOptions(model="m"))

    result = await engine.finalize(
        chamber, {str(ada.id): Stance.PRO, str(zeno.id): Stance.CON}
    )

    assert result.outcome is ConsensusOutcome.VERDICT
    assert result.winning_stance is Stance.PRO
    assert result.headline == "The pro case won."
    assert result.statement == "Better evidence."


async def test_finalize_reads_a_verdict_with_a_blank_line_before_winner() -> None:
    # Reproduces the shape real Ollama models actually send: a blank line
    # between HEADLINE and WINNER. Without the fix, WINNER is never parsed,
    # winning_stance stays None on a VERDICT outcome, and "WINNER: pro" leaks
    # into the published statement.
    ada = make_participant("Ada", Stance.PRO)
    zeno = make_participant("Zeno", Stance.CON)
    chamber = make_chamber(ada, zeno)
    chamber.settings.decision_rule = DecisionRule.JUDGE
    provider = ScriptedProvider(
        moderator_reply="HEADLINE: The pro case won.\n\nWINNER: pro\n\nBetter evidence."
    )
    engine = ConsensusEngine(ConstantFactory(provider), provider, GenerateOptions(model="m"))

    result = await engine.finalize(
        chamber, {str(ada.id): Stance.PRO, str(zeno.id): Stance.CON}
    )

    assert result.outcome is ConsensusOutcome.VERDICT
    assert result.winning_stance is Stance.PRO
    assert result.headline == "The pro case won."
    assert result.statement == "Better evidence."
