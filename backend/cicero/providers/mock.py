"""A deterministic, offline provider for tests and demos.

``MockProvider`` performs **no network calls** and returns reproducible output,
so the debate engine, consensus logic, and stop conditions can be tested exactly
(NFR-Q-1/3). It can replay a scripted sequence of responses or fall back to a
deterministic, content-derived reply.
"""

from __future__ import annotations

from collections.abc import Sequence

from cicero.domain.enums import ProviderType
from cicero.providers.base import (
    GenerateOptions,
    GenerateResult,
    Message,
    Provider,
    ProviderError,
)

_DEFAULT_MODELS = ("mock-small", "mock-large")

#: Identifies a stance poll from its instruction text. Kept as a literal rather
#: than imported so the provider layer stays independent of ``core.prompts``;
#: ``test_mock_provider`` asserts the two never drift apart.
POLL_MARKER = "reply with exactly one word"

#: A mock cannot be persuaded, so it reports no position rather than pretending
#: to hold one. Deterministic, always parseable, and it keeps an offline debate
#: converging instead of resolving on parser noise.
_DEFAULT_POLL_ANSWER = "neutral"

#: Identifies a moderator prompt from its system text, on the same terms as
#: ``POLL_MARKER``: a literal, so the provider layer stays independent of
#: ``core.prompts``, with a test asserting the two never drift apart.
MODERATOR_MARKER = "impartial moderator"

#: A moderator reply that exercises the real directive parser. A mock has no
#: view to summarise, so it says so — but it says so in the right shape, which
#: is what makes the offline acceptance path meaningful.
_DEFAULT_MODERATOR_REPLY = (
    "HEADLINE: The chamber reached a deterministic mock outcome.\n"
    "This is a mock synthesis of the debate."
)

#: Identifies a compliance-judge prompt from its system text, on the same terms as
#: ``POLL_MARKER``: a literal, so the provider layer stays independent of
#: ``core.prompts``, with a test asserting the two never drift apart.
COMPLIANCE_MARKER = "impartial reader"

#: A mock has no view on which side a turn argues, so it reports none — but it
#: reports it in a shape ``parse_stance`` can read, which is what keeps the offline
#: acceptance path exercising the real code.
_DEFAULT_COMPLIANCE_ANSWER = "neutral"

#: The delimiters the compliance prompt wraps the judged turn in. Literals, so
#: the provider layer stays independent of ``core.prompts``; a test asserts the
#: two never drift apart.
TRANSCRIPT_OPEN_MARKER = "<<<TRANSCRIPT>>>"
TRANSCRIPT_CLOSE_MARKER = "<<<END_TRANSCRIPT>>>"


def _count_tokens(text: str) -> int:
    """Deterministic, whitespace-based token estimate (never network-derived)."""
    return len(text.split())


def _first_sentence_of_judged_turn(user_message: str) -> str:
    """The first non-empty line of the turn the judge was asked to read.

    A mock has no view on which side an argument takes, but its answer still has
    to be *grounded* or the real parser discards it and the offline path stops
    exercising the feature. Copying a line out of the turn is the cheapest way to
    produce a quote that genuinely appears in it.
    """
    _, _, rest = user_message.partition(TRANSCRIPT_OPEN_MARKER)
    body, _, _ = rest.partition(TRANSCRIPT_CLOSE_MARKER)
    for line in body.splitlines():
        if line.strip():
            return line.strip()
    return ""


class MockProvider(Provider):
    """Deterministic provider.

    Args:
        scripted: Replies returned in order; once exhausted, a deterministic
            content-derived reply is produced.
        models: Model identifiers reported by :meth:`list_models`.
        fail_after: If set, the ``fail_after``-th call (1-indexed) and beyond
            raise :class:`ProviderError`, to exercise failure handling (C5).
        poll_answer: The stance reported when asked for one; set it to drive a
            specific outcome in a test or demo.
        compliance_answer: The side reported when asked which side a turn argues.
    """

    provider_type = ProviderType.MOCK

    def __init__(
        self,
        scripted: Sequence[str] | None = None,
        models: Sequence[str] | None = None,
        fail_after: int | None = None,
        poll_answer: str = _DEFAULT_POLL_ANSWER,
        compliance_answer: str = _DEFAULT_COMPLIANCE_ANSWER,
    ) -> None:
        self._scripted: list[str] = list(scripted or [])
        self._models: list[str] = list(models or _DEFAULT_MODELS)
        self._fail_after = fail_after
        self._poll_answer = poll_answer
        self._compliance_answer = compliance_answer
        self.call_count = 0

    async def generate(
        self, messages: list[Message], options: GenerateOptions
    ) -> GenerateResult:
        self.call_count += 1
        if self._fail_after is not None and self.call_count >= self._fail_after:
            raise ProviderError("mock provider failure (simulated)")

        last = messages[-1].content if messages else ""
        system = messages[0].content if messages else ""
        if self._scripted:
            content = self._scripted.pop(0)
        elif COMPLIANCE_MARKER in system.lower():
            quoted = _first_sentence_of_judged_turn(last)
            content = f"POSITION: {quoted}\nSIDE: {self._compliance_answer}"
        elif POLL_MARKER in last.lower():
            # A stance poll needs an *answer*, not an echo. Echoing used to
            # "work" only because the parser matched pro/con out of the quoted
            # transcript — the mock never reported a position at all.
            content = self._poll_answer
        elif MODERATOR_MARKER in system.lower():
            content = _DEFAULT_MODERATOR_REPLY
        else:
            snippet = last[:80]
            content = f"[mock:{options.model}] response to: {snippet}"

        prompt_tokens = sum(_count_tokens(m.content) for m in messages)
        return GenerateResult(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=_count_tokens(content),
            metadata={"provider": self.provider_type.value, "call": self.call_count},
        )

    async def list_models(self) -> list[str]:
        return list(self._models)
