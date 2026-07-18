"""Tests for the fixed-window rate limiter (J2)."""

from __future__ import annotations

from cicero.api.rate_limit import FixedWindowRateLimiter


def test_limit_enforced_per_key() -> None:
    now = 0.0
    limiter = FixedWindowRateLimiter(2, clock=lambda: now)
    assert limiter.allow("a") is True
    assert limiter.allow("a") is True
    assert limiter.allow("a") is False  # third within the window
    assert limiter.allow("b") is True  # other clients unaffected


def test_window_resets_after_a_minute() -> None:
    now = 0.0
    limiter = FixedWindowRateLimiter(1, clock=lambda: now)
    assert limiter.allow("a") is True
    assert limiter.allow("a") is False
    now = 59.9
    assert limiter.allow("a") is False  # still the same window
    now = 60.0
    assert limiter.allow("a") is True  # new window


def test_zero_limit_disables() -> None:
    limiter = FixedWindowRateLimiter(0, clock=lambda: 0.0)
    assert all(limiter.allow("a") for _ in range(100))


def test_stale_windows_are_pruned() -> None:
    now = 0.0
    limiter = FixedWindowRateLimiter(1, clock=lambda: now)
    for i in range(2000):
        limiter.allow(f"client-{i}")
    now = 120.0
    limiter.allow("fresh")
    # All 60s-old windows were dropped; only the fresh key remains tracked.
    assert len(limiter._windows) <= 2  # noqa: SLF001
