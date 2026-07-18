"""A small in-memory, per-client fixed-window rate limiter (J2, NFR-SEC-8).

Suitable for the single-process, self-hosted deployment Cicero targets; a
multi-instance deployment would need a shared store instead. Pure logic (the
clock is injected) so it is unit-testable and deterministic.
"""

from __future__ import annotations

import time
from collections.abc import Callable

_WINDOW_SECONDS = 60.0
_PRUNE_THRESHOLD = 1024


class FixedWindowRateLimiter:
    """Allows up to ``limit`` requests per key per minute (0 disables)."""

    def __init__(self, limit: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._limit = limit
        self._clock = clock
        self._windows: dict[str, tuple[float, int]] = {}

    def allow(self, key: str) -> bool:
        """Record a request for ``key``; False when it exceeds the window limit."""
        if self._limit <= 0:
            return True
        now = self._clock()
        start, count = self._windows.get(key, (now, 0))
        if now - start >= _WINDOW_SECONDS:
            start, count = now, 0
        count += 1
        if len(self._windows) >= _PRUNE_THRESHOLD:
            self._prune(now)
        self._windows[key] = (start, count)
        return count <= self._limit

    def _prune(self, now: float) -> None:
        expired = [
            key
            for key, (start, _) in self._windows.items()
            if now - start >= _WINDOW_SECONDS
        ]
        for key in expired:
            del self._windows[key]
