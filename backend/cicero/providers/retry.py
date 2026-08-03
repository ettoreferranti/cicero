"""Retry policy for transient provider failures (C5 / NFR-R-1).

A provider call fails for two very different reasons. Some failures are
**permanent** — a model name that does not exist, a rejected API key, a
malformed request — and retrying them only doubles the cost of the mistake.
Others are **transient** — a dropped connection, a read timeout, a rate limit, a
5xx — and not retrying those loses a debater its entire turn for something that
would have worked a second later.

This module classifies the failure and paces the retries. Retries are the first
line of defence; the engine's existing per-turn error handling stays the second,
so a debate still survives a provider that is genuinely down.

The delay schedule is pure and the sleep is injected, so the whole module is
deterministic under test and sits in the mutation gate.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

import httpx

T = TypeVar("T")

#: Status codes worth trying again: request timeout, too-early, rate limit, the
#: 5xx family, and Anthropic's 529 "overloaded". Every other 4xx is the caller's
#: fault and will fail identically on a retry.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504, 529})


@dataclass(frozen=True)
class RetryPolicy:
    """How many times to retry a transient provider failure, and how fast."""

    #: Total attempts including the first; 1 disables retrying.
    max_attempts: int = 3
    #: Delay before the first retry; doubles each time (capped).
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 8.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.base_delay_seconds < 0:
            raise ValueError("base_delay_seconds cannot be negative")
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError("max_delay_seconds cannot be below base_delay_seconds")

    def delay_for(self, retry_number: int, retry_after: float | None = None) -> float:
        """Seconds to wait before retry ``retry_number`` (1 = the first retry).

        A server-supplied ``Retry-After`` wins over the exponential schedule —
        it is the only party that knows when its rate limit resets — but is
        still capped, so a hostile or mistaken header cannot stall a debate.
        """
        if retry_number < 1:
            raise ValueError("retry_number must be at least 1")
        if retry_after is not None and retry_after >= 0:
            return min(retry_after, self.max_delay_seconds)
        backoff: float = self.base_delay_seconds * (2 ** (retry_number - 1))
        return min(backoff, self.max_delay_seconds)


def is_retryable(exc: Exception) -> bool:
    """Whether ``exc`` looks transient enough to be worth another attempt."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS
    # Connect errors, read/write timeouts, protocol errors: all transport-level
    # and all plausibly transient.
    return isinstance(exc, httpx.TransportError)


def retry_after_seconds(exc: Exception) -> float | None:
    """The response's ``Retry-After`` delay in seconds, when it gives one.

    Only the delta-seconds form is honoured; the HTTP-date form is rare in
    practice and a bad parse should never be mistaken for "wait zero".
    """
    if not isinstance(exc, httpx.HTTPStatusError):
        return None
    raw = exc.response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        seconds = float(raw.strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


async def call_with_retry(
    operation: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Run ``operation``, retrying transient failures per ``policy``.

    The last failure is re-raised unchanged, so callers keep translating it into
    their own :class:`~cicero.providers.base.ProviderError` exactly as before.
    """
    retries = 0
    while True:
        try:
            return await operation()
        except httpx.HTTPError as exc:
            retries += 1
            if retries >= policy.max_attempts or not is_retryable(exc):
                raise
            await sleep(policy.delay_for(retries, retry_after_seconds(exc)))
