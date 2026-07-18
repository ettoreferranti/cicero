"""Tests for the per-turn research protocol and session."""

from __future__ import annotations

import pytest

from cicero.core import prompts
from cicero.core.research import (
    ResearchSession,
    format_search_results,
    parse_search_request,
)
from cicero.domain.models import Citation


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("SEARCH: mars radiation shielding", "mars radiation shielding"),
        ("  SEARCH: mars costs  ", "mars costs"),
        ("search: lowercase works", "lowercase works"),
        ("SEARCH:", None),  # no query
        ("SEARCH:   ", None),
        ("I think we should SEARCH: for truth", None),  # not at the start
        ("SEARCH: q\nand my argument", None),  # must be a single line
        ("My argument about Mars.", None),
    ],
)
def test_parse_search_request(text: str, expected: str | None) -> None:
    assert parse_search_request(text) == expected


def test_format_search_results_with_citations() -> None:
    citations = [
        Citation(url="https://example.org/a", title="Study A", excerpt="Finding one."),
        Citation(url="https://example.org/b", title="", excerpt="Finding two."),
    ]
    block = format_search_results("mars costs", citations)
    assert block.startswith(prompts.SEARCH_RESULTS_OPEN)
    assert block.endswith(prompts.SEARCH_RESULTS_CLOSE)
    assert '"mars costs"' in block
    assert "[Study A]" in block
    assert "Finding one." in block
    # Untitled sources fall back to the URL as the label.
    assert "[https://example.org/b]" in block
    assert "(Source: https://example.org/a)" in block


def test_format_search_results_empty() -> None:
    block = format_search_results("obscure", [])
    assert prompts.NO_SEARCH_RESULTS in block


class StubGatherer:
    def __init__(self, citations: list[Citation] | None = None, fail: bool = False) -> None:
        self._citations = citations or []
        self._fail = fail
        self.queries: list[str] = []

    async def gather(self, topic: str) -> list[Citation]:
        self.queries.append(topic)
        if self._fail:
            raise RuntimeError("backend down")
        return list(self._citations)


async def test_session_records_queries_and_citations() -> None:
    citation = Citation(url="https://example.org", title="T", excerpt="E")
    gatherer = StubGatherer([citation])
    session = ResearchSession(gatherer)

    block = await session.run("first query")
    assert gatherer.queries == ["first query"]
    assert session.queries == ["first query"]
    assert session.citations == [citation]
    assert "E" in block

    await session.run("second query")
    assert session.queries == ["first query", "second query"]
    assert session.citations == [citation, citation]


async def test_session_survives_gatherer_failure() -> None:
    session = ResearchSession(StubGatherer(fail=True))
    block = await session.run("q")
    assert session.queries == ["q"]
    assert session.citations == []
    assert prompts.NO_SEARCH_RESULTS in block
