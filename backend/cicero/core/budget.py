"""Debate budgets and stop conditions (FR-16, NFR-SEC-8).

Hard caps on rounds and tokens prevent runaway loops and cost. This module is
pure and deterministic, and is part of the mutation-testing gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class StopReason(StrEnum):
    """Why a debate stopped."""

    CONSENSUS = "consensus"
    STANCES_STABLE = "stances_stable"
    MAX_ROUNDS = "max_rounds"
    TOKEN_BUDGET = "token_budget"  # noqa: S105 (enum label, not a secret)


@dataclass(frozen=True)
class DebateBudget:
    """The hard limits for a debate run."""

    max_rounds: int
    max_total_tokens: int
    min_rounds: int = 1

    def __post_init__(self) -> None:
        if self.max_rounds <= 0:
            raise ValueError("max_rounds must be positive")
        if self.max_total_tokens <= 0:
            raise ValueError("max_total_tokens must be positive")
        if self.min_rounds < 1:
            raise ValueError("min_rounds must be at least 1")
        if self.min_rounds > self.max_rounds:
            raise ValueError("min_rounds cannot exceed max_rounds")


class BudgetTracker:
    """Mutable running totals checked against a :class:`DebateBudget`."""

    def __init__(self, budget: DebateBudget) -> None:
        self._budget = budget
        self.tokens_used = 0
        self.rounds_completed = 0

    def add_tokens(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.tokens_used += prompt_tokens + completion_tokens

    def complete_round(self) -> None:
        self.rounds_completed += 1

    def token_budget_exhausted(self) -> bool:
        return self.tokens_used >= self._budget.max_total_tokens

    def rounds_exhausted(self) -> bool:
        return self.rounds_completed >= self._budget.max_rounds

    def may_stop_early(self) -> bool:
        """Whether enough rounds have run to allow a stability-based early stop."""
        return self.rounds_completed >= self._budget.min_rounds
