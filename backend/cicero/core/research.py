"""Per-turn web research for debaters (FR-26/27/28).

Two mechanisms share this module, and both funnel every query through the same
sandboxed evidence gatherer (SSRF guard, allow/deny lists, caps — see
``cicero.tools.web``):

- **Text protocol** (works with any provider): the turn prompt invites the
  model to reply with a single ``SEARCH: <query>`` line; the engine executes
  the search and lets the model continue with the results in context.
- **Native tool use** (providers with ``supports_native_search``, e.g.
  Anthropic): the provider runs its own tool loop and calls back into the
  :class:`ResearchSession` to execute each search.

A :class:`ResearchSession` is created per participant-turn and records the
queries made and the citations gathered, so sources are attributed to the
exact turn that used them (FR-28). Pure parts are in the mutation gate.
"""

from __future__ import annotations

from typing import Protocol

from cicero.core import prompts
from cicero.domain.models import Citation

#: A turn reply of exactly this form (one line) requests a web search.
SEARCH_PREFIX = "SEARCH:"

#: Hard cap on searches per participant-turn (cost/loop control, NFR-SEC-8).
MAX_SEARCHES_PER_TURN = 1


class EvidenceGatherer(Protocol):
    """Collects sanitised, cited web evidence for a query (Epic G)."""

    async def gather(self, topic: str) -> list[Citation]: ...


def parse_search_request(text: str) -> str | None:
    """Return the query if ``text`` is a well-formed search request, else ``None``.

    The protocol requires the whole reply to be a single ``SEARCH: <query>``
    line; anything else (extra lines, missing query) is treated as an ordinary
    argument so a stray mention of the word cannot trigger a search.
    """
    stripped = text.strip()
    if "\n" in stripped:
        return None
    if not stripped.upper().startswith(SEARCH_PREFIX):
        return None
    query = stripped[len(SEARCH_PREFIX) :].strip()
    return query or None


def format_search_results(query: str, citations: list[Citation]) -> str:
    """Render search results as a delimited, source-attributed block.

    The delimiters mark the content as untrusted web data (NFR-SEC-5); the
    accompanying rule text lives in the turn prompt.
    """
    if not citations:
        body = prompts.NO_SEARCH_RESULTS
    else:
        body = "\n\n".join(
            f"[{citation.title.strip() or citation.url}]\n"
            f"{citation.excerpt}\n(Source: {citation.url})"
            for citation in citations
        )
    return (
        f"{prompts.SEARCH_RESULTS_OPEN}\n"
        f'Results for "{query}":\n{body}\n'
        f"{prompts.SEARCH_RESULTS_CLOSE}"
    )


class ResearchSession:
    """One participant-turn's research: runs queries and records attribution."""

    def __init__(self, gatherer: EvidenceGatherer) -> None:
        self._gatherer = gatherer
        self.queries: list[str] = []
        self.citations: list[Citation] = []

    async def run(self, query: str) -> str:
        """Execute one sandboxed search; failures yield an empty result block."""
        self.queries.append(query)
        try:
            citations = await self._gatherer.gather(query)
        except Exception:  # noqa: BLE001 (research is best-effort; never kills a turn)
            citations = []
        self.citations.extend(citations)
        return format_search_results(query, citations)
