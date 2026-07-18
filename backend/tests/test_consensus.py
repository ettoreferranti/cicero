"""Tests for the hybrid consensus engine."""

from __future__ import annotations

import pytest

from cicero.core.consensus import (
    ConsensusEngine,
    decide_outcome,
    is_consensus,
    majority_stance,
    parse_stance,
    parse_verdict,
)
from cicero.domain.enums import ConsensusOutcome, DecisionRule, Stance
from cicero.providers.base import GenerateOptions
from tests.conftest import ScriptedProvider, StubFactory, make_chamber, make_participant

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
        ("undecided maybe", None),
    ],
)
def test_parse_stance(text: str, expected: Stance | None) -> None:
    assert parse_stance(text) is expected


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
    stances = await engine.poll_stances(chamber)
    assert stances == {str(pro.id): Stance.PRO, str(con.id): Stance.CON}


async def test_poll_uses_parsed_stance_over_declared_stance() -> None:
    # Declared NEUTRAL but the model now reports "pro": the parsed value must win,
    # proving the poll actually parses the response (not just echoes the stance).
    p = make_participant("Mover", Stance.NEUTRAL)
    chamber = make_chamber(p, make_participant("Other", Stance.CON))
    factory = StubFactory(
        {pt.id: ScriptedProvider(stance_word="pro") for pt in chamber.participants}
    )
    engine = ConsensusEngine(factory, ScriptedProvider(), MOD_OPTS)
    stances = await engine.poll_stances(chamber)
    assert stances[str(p.id)] is Stance.PRO


async def test_poll_falls_back_to_prior_stance_when_unparseable() -> None:
    p = make_participant("P", Stance.CON)
    chamber = make_chamber(p, make_participant("Q", Stance.PRO))
    factory = StubFactory(
        {pid: ScriptedProvider(stance_word="???") for pid in [pt.id for pt in chamber.participants]}
    )
    engine = ConsensusEngine(factory, ScriptedProvider(), MOD_OPTS)
    stances = await engine.poll_stances(chamber)
    # Unparseable → keep the participant's declared stance.
    assert stances[str(p.id)] is Stance.CON


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
    ("text", "winner", "body"),
    [
        ("WINNER: pro\nStrong case.", Stance.PRO, "Strong case."),
        ("winner: CON\nThe cons had it.", Stance.CON, "The cons had it."),
        ("WINNER: neutral\nNobody moved.", Stance.NEUTRAL, "Nobody moved."),
        ("No verdict line here.", None, "No verdict line here."),
        ("WINNER: maybe\ntext", None, "WINNER: maybe\ntext"),
    ],
)
def test_parse_verdict(text: str, winner: Stance | None, body: str) -> None:
    assert parse_verdict(text) == (winner, body)


def test_parse_verdict_without_body_keeps_full_text() -> None:
    assert parse_verdict("WINNER: pro") == (Stance.PRO, "WINNER: pro")


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
