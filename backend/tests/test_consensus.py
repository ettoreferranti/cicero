"""Tests for the hybrid consensus engine."""

from __future__ import annotations

import pytest

from cicero.core.consensus import ConsensusEngine, is_consensus, parse_stance
from cicero.domain.enums import ConsensusOutcome, Stance
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


async def test_finalize_disagreement_outcome() -> None:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    engine = ConsensusEngine(StubFactory({}), ScriptedProvider(), MOD_OPTS)
    stances = {str(a.id): Stance.PRO, str(b.id): Stance.CON}
    result = await engine.finalize(chamber, stances)
    assert result.outcome is ConsensusOutcome.DISAGREEMENT


async def test_finalize_uses_fallback_when_moderator_is_empty() -> None:
    from cicero.core.prompts import EMPTY_MODERATOR_STATEMENT

    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.PRO)
    chamber = make_chamber(a, b)
    moderator = ScriptedProvider(moderator_reply="   ")  # whitespace only
    engine = ConsensusEngine(StubFactory({}), moderator, MOD_OPTS)
    result = await engine.finalize(chamber, {str(a.id): Stance.PRO, str(b.id): Stance.PRO})
    assert result.statement == EMPTY_MODERATOR_STATEMENT
