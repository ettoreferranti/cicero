"""Per-participant debate metrics (FR-33), computed purely from a chamber.

Pure and deterministic, so it is part of the mutation-testing gate.
"""

from __future__ import annotations

from dataclasses import dataclass

from cicero.domain.models import Chamber


def _as_int(value: object) -> int:
    """Coerce a turn-metadata value to a non-negative int (0 if absent/odd)."""
    if isinstance(value, bool):  # bool is an int subclass; treat as not-a-count
        return 0
    if isinstance(value, int):
        return value
    return 0


@dataclass(frozen=True)
class ParticipantMetrics:
    """Aggregated counts for one participant across a debate."""

    participant_id: str
    display_name: str
    provider: str
    turns: int
    prompt_tokens: int
    completion_tokens: int
    errors: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def compute_participant_metrics(chamber: Chamber) -> list[ParticipantMetrics]:
    """Return metrics for each participant, in participant order."""
    turns = 0
    prompt = 0
    completion = 0
    errors = 0
    per_id: dict[str, tuple[int, int, int, int]] = {}
    for participant in chamber.participants:
        per_id[str(participant.id)] = (0, 0, 0, 0)

    for turn in chamber.turns:
        pid = str(turn.participant_id)
        if pid not in per_id:
            continue
        turns, prompt, completion, errors = per_id[pid]
        turns += 1
        if "error" in turn.metadata:
            errors += 1
        prompt += _as_int(turn.metadata.get("prompt_tokens", 0))
        completion += _as_int(turn.metadata.get("completion_tokens", 0))
        per_id[pid] = (turns, prompt, completion, errors)

    result: list[ParticipantMetrics] = []
    for participant in chamber.participants:
        turns, prompt, completion, errors = per_id[str(participant.id)]
        result.append(
            ParticipantMetrics(
                participant_id=str(participant.id),
                display_name=participant.display_name,
                provider=participant.provider.value,
                turns=turns,
                prompt_tokens=prompt,
                completion_tokens=completion,
                errors=errors,
            )
        )
    return result
