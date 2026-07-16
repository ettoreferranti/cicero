"""Debate core: pure, framework-free orchestration and rules.

Everything here depends only on the domain model and provider/repository
*interfaces* — never on frameworks or I/O — so it is deterministic and the prime
target for mutation testing (see docs/testing.md).
"""

from cicero.core.budget import BudgetTracker, DebateBudget, StopReason
from cicero.core.consensus import ConsensusEngine, is_consensus, parse_stance
from cicero.core.orchestrator import DebateEngine, TurnListener
from cicero.core.prompt_builder import (
    build_moderator_messages,
    build_stance_poll_messages,
    build_turn_messages,
    render_transcript,
)
from cicero.core.state_machine import (
    ALLOWED_TRANSITIONS,
    InvalidTransition,
    can_transition,
    transition,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "BudgetTracker",
    "ConsensusEngine",
    "DebateBudget",
    "DebateEngine",
    "InvalidTransition",
    "StopReason",
    "TurnListener",
    "build_moderator_messages",
    "build_stance_poll_messages",
    "build_turn_messages",
    "can_transition",
    "is_consensus",
    "parse_stance",
    "render_transcript",
    "transition",
]
