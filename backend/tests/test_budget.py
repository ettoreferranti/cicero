"""Tests for debate budgets and stop conditions."""

from __future__ import annotations

import dataclasses

import pytest

from cicero.core.budget import BudgetTracker, DebateBudget


def test_budget_validation() -> None:
    with pytest.raises(ValueError, match=r"^max_rounds must be positive$"):
        DebateBudget(max_rounds=0, max_total_tokens=100)
    with pytest.raises(ValueError, match=r"^max_total_tokens must be positive$"):
        DebateBudget(max_rounds=1, max_total_tokens=0)
    with pytest.raises(ValueError, match=r"^min_rounds must be at least 1$"):
        DebateBudget(max_rounds=2, max_total_tokens=100, min_rounds=0)
    with pytest.raises(ValueError, match=r"^min_rounds cannot exceed max_rounds$"):
        DebateBudget(max_rounds=2, max_total_tokens=100, min_rounds=3)


def test_budget_accepts_minimal_valid_values() -> None:
    # Boundary: 1 token / 1 round must be accepted (guards <=0 vs <=1 mutations).
    budget = DebateBudget(max_rounds=1, max_total_tokens=1, min_rounds=1)
    assert budget.max_total_tokens == 1


def test_budget_is_frozen() -> None:
    budget = DebateBudget(max_rounds=2, max_total_tokens=100)
    with pytest.raises(dataclasses.FrozenInstanceError):
        budget.max_rounds = 5  # type: ignore[misc]


def test_token_tracking_and_exhaustion() -> None:
    tracker = BudgetTracker(DebateBudget(max_rounds=5, max_total_tokens=10))
    assert tracker.token_budget_exhausted() is False
    tracker.add_tokens(4, 5)  # 9 < 10
    assert tracker.token_budget_exhausted() is False
    tracker.add_tokens(1, 0)  # 10 >= 10
    assert tracker.token_budget_exhausted() is True
    assert tracker.tokens_used == 10


def test_round_tracking_and_exhaustion() -> None:
    tracker = BudgetTracker(DebateBudget(max_rounds=2, max_total_tokens=100))
    assert tracker.rounds_exhausted() is False
    tracker.complete_round()
    assert tracker.rounds_exhausted() is False
    tracker.complete_round()
    assert tracker.rounds_exhausted() is True
    assert tracker.rounds_completed == 2


def test_may_stop_early_respects_min_rounds() -> None:
    tracker = BudgetTracker(DebateBudget(max_rounds=5, max_total_tokens=100, min_rounds=2))
    assert tracker.may_stop_early() is False
    tracker.complete_round()
    assert tracker.may_stop_early() is False
    tracker.complete_round()
    assert tracker.may_stop_early() is True
