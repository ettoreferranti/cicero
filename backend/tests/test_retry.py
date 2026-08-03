"""Unit tests for the provider retry policy (C5 / NFR-R-1).

The schedule is pure and the sleep is injected, so pacing is asserted exactly —
no real waiting anywhere in this module.
"""

from __future__ import annotations

import dataclasses

import httpx
import pytest

from cicero.providers.retry import (
    RETRYABLE_STATUS,
    RetryPolicy,
    call_with_retry,
    is_retryable,
    retry_after_seconds,
)


def _status_error(status: int, headers: dict[str, str] | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.test/v1")
    response = httpx.Response(status, headers=headers or {}, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


def test_default_policy_is_the_shipped_behaviour() -> None:
    # These defaults apply whenever no PROVIDER_* config is supplied, so they
    # are behaviour, not decoration: three tries, 0.5s then 1s, capped at 8s.
    policy = RetryPolicy()
    assert policy.max_attempts == 3
    assert policy.base_delay_seconds == 0.5
    assert policy.max_delay_seconds == 8.0
    assert [policy.delay_for(n) for n in (1, 2)] == [0.5, 1.0]


def test_policy_is_immutable_so_it_is_safe_to_share() -> None:
    # One policy instance is handed to every cached provider; a mutable one
    # could be retuned underneath a debate that is already running.
    policy = RetryPolicy()
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.max_attempts = 99  # type: ignore[misc]


def test_policy_rejects_impossible_configurations() -> None:
    with pytest.raises(ValueError, match=r"^max_attempts must be at least 1$"):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValueError, match=r"^base_delay_seconds cannot be negative$"):
        RetryPolicy(base_delay_seconds=-1)
    with pytest.raises(ValueError, match=r"^max_delay_seconds cannot be below"):
        RetryPolicy(base_delay_seconds=10, max_delay_seconds=1)
    RetryPolicy(max_attempts=1)  # 1 attempt == retrying disabled, and is legal


def test_backoff_doubles_and_is_capped() -> None:
    policy = RetryPolicy(base_delay_seconds=0.5, max_delay_seconds=4)
    assert policy.delay_for(1) == 0.5
    assert policy.delay_for(2) == 1.0
    assert policy.delay_for(3) == 2.0
    assert policy.delay_for(4) == 4.0
    assert policy.delay_for(5) == 4.0  # capped, not 8
    with pytest.raises(ValueError, match=r"^retry_number must be at least 1$"):
        policy.delay_for(0)


def test_retry_after_overrides_the_schedule_but_is_still_capped() -> None:
    policy = RetryPolicy(base_delay_seconds=0.5, max_delay_seconds=4)
    assert policy.delay_for(1, retry_after=3) == 3  # server knows best
    assert policy.delay_for(1, retry_after=99) == 4  # ... within reason
    assert policy.delay_for(1, retry_after=0) == 0
    assert policy.delay_for(3, retry_after=None) == 2.0  # falls back to backoff
    # A negative header value is nonsense: ignore it and use the schedule.
    assert policy.delay_for(3, retry_after=-5) == 2.0


def test_only_transient_failures_are_retryable() -> None:
    for status in (408, 425, 429, 500, 502, 503, 504, 529):
        assert status in RETRYABLE_STATUS
        assert is_retryable(_status_error(status))
    # Client mistakes fail identically on a retry.
    for status in (400, 401, 403, 404, 422):
        assert not is_retryable(_status_error(status))
    # Transport-level failures are all plausibly transient.
    request = httpx.Request("POST", "https://example.test/v1")
    assert is_retryable(httpx.ConnectError("refused", request=request))
    assert is_retryable(httpx.ReadTimeout("slow", request=request))
    assert not is_retryable(ValueError("not an httpx failure"))


def test_retry_after_header_parsing() -> None:
    assert retry_after_seconds(_status_error(429, {"Retry-After": "2"})) == 2
    assert retry_after_seconds(_status_error(429, {"Retry-After": " 1.5 "})) == 1.5
    # Sub-second and zero delays are real instructions ("go again now"), not
    # missing headers — they must not fall back to the backoff schedule.
    assert retry_after_seconds(_status_error(429, {"Retry-After": "0.5"})) == 0.5
    assert retry_after_seconds(_status_error(429, {"Retry-After": "0"})) == 0
    assert retry_after_seconds(_status_error(429, {})) is None
    # The HTTP-date form is not parsed — better None than a wrong zero.
    http_date = _status_error(429, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
    assert retry_after_seconds(http_date) is None
    assert retry_after_seconds(_status_error(429, {"Retry-After": "-3"})) is None
    request = httpx.Request("POST", "https://example.test/v1")
    assert retry_after_seconds(httpx.ConnectError("refused", request=request)) is None


class _Sleeper:
    """Records the delays it was asked to wait, without waiting."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


async def test_call_with_retry_returns_first_success_without_sleeping() -> None:
    sleeper = _Sleeper()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    assert await call_with_retry(operation, RetryPolicy(), sleeper) == "ok"
    assert calls == 1
    assert sleeper.delays == []


async def test_call_with_retry_paces_retries_then_succeeds() -> None:
    sleeper = _Sleeper()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise _status_error(503)
        return "ok"

    policy = RetryPolicy(max_attempts=4, base_delay_seconds=0.5, max_delay_seconds=10)
    assert await call_with_retry(operation, policy, sleeper) == "ok"
    assert calls == 3
    assert sleeper.delays == [0.5, 1.0]


async def test_call_with_retry_honours_retry_after() -> None:
    sleeper = _Sleeper()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _status_error(429, {"Retry-After": "7"})
        return "ok"

    policy = RetryPolicy(max_attempts=3, base_delay_seconds=0.5, max_delay_seconds=10)
    assert await call_with_retry(operation, policy, sleeper) == "ok"
    assert sleeper.delays == [7]


async def test_call_with_retry_gives_up_and_reraises_the_last_failure() -> None:
    sleeper = _Sleeper()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise _status_error(500)

    policy = RetryPolicy(max_attempts=3, base_delay_seconds=1, max_delay_seconds=10)
    with pytest.raises(httpx.HTTPStatusError):
        await call_with_retry(operation, policy, sleeper)
    assert calls == 3
    assert sleeper.delays == [1, 2]  # slept between attempts, not after the last


async def test_call_with_retry_does_not_retry_permanent_failures() -> None:
    sleeper = _Sleeper()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise _status_error(401)

    with pytest.raises(httpx.HTTPStatusError):
        await call_with_retry(operation, RetryPolicy(max_attempts=5), sleeper)
    assert calls == 1
    assert sleeper.delays == []


async def test_single_attempt_policy_never_retries() -> None:
    sleeper = _Sleeper()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise _status_error(503)

    with pytest.raises(httpx.HTTPStatusError):
        await call_with_retry(operation, RetryPolicy(max_attempts=1), sleeper)
    assert calls == 1
    assert sleeper.delays == []
