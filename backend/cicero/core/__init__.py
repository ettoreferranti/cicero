"""Debate core: pure, framework-free orchestration and rules.

Everything here depends only on the domain model and provider/repository
*interfaces* — never on frameworks or I/O — so it is deterministic and the prime
target for mutation testing (see docs/testing.md).
"""

from cicero.core.state_machine import (
    ALLOWED_TRANSITIONS,
    InvalidTransition,
    can_transition,
    transition,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "InvalidTransition",
    "can_transition",
    "transition",
]
