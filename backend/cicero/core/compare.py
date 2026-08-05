"""Compare two debate runs side by side (H4, FR-32).

Pure and deterministic — part of the mutation-testing gate.
"""

from __future__ import annotations

from typing import Any

from cicero.domain.models import Chamber, Participant


def _final_stance(chamber: Chamber, participant: Participant) -> str | None:
    if chamber.consensus is None:
        return None
    stance = chamber.consensus.final_stances.get(str(participant.id))
    return stance.value if stance is not None else None


def summarize_run(chamber: Chamber) -> dict[str, Any]:
    """A compact, comparison-oriented snapshot of one debate run."""
    consensus = chamber.consensus
    return {
        "chamber_id": str(chamber.id),
        "topic": chamber.topic,
        "status": chamber.status.value,
        "outcome": consensus.outcome.value if consensus is not None else None,
        # The one line that makes two runs comparable at a glance (F5).
        "headline": consensus.headline or None if consensus is not None else None,
        "winning_stance": (
            consensus.winning_stance.value
            if consensus is not None and consensus.winning_stance is not None
            else None
        ),
        "statement": consensus.statement if consensus is not None else None,
        "stop_reason": chamber.config.get("stop_reason"),
        "rounds_completed": chamber.config.get("rounds_completed"),
        "tokens_used": chamber.config.get("tokens_used"),
        "turn_count": len(chamber.turns),
        "participants": [
            {
                "display_name": participant.display_name,
                "provider": participant.provider.value,
                "model": participant.model,
                "initial_stance": participant.stance.value,
                "final_stance": _final_stance(chamber, participant),
            }
            for participant in chamber.participants
        ],
    }


def compare_chambers(a: Chamber, b: Chamber) -> dict[str, Any]:
    """Two run summaries plus whether they debated the same topic."""
    return {
        "a": summarize_run(a),
        "b": summarize_run(b),
        "same_topic": a.topic.strip().lower() == b.topic.strip().lower(),
    }
