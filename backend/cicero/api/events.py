"""Debate events streamed to clients over Server-Sent Events (SSE)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class DebateEventType(StrEnum):
    """The kinds of events emitted during a debate run."""

    STATUS = "status"
    TURN = "turn"
    CONSENSUS = "consensus"
    ERROR = "error"
    DONE = "done"


class DebateEvent(BaseModel):
    """A single streamed event."""

    type: DebateEventType
    payload: dict[str, Any] = Field(default_factory=dict)

    def to_sse(self) -> str:
        """Serialise to the SSE wire format (an ``event:``/``data:`` frame)."""
        return f"event: {self.type.value}\ndata: {self.model_dump_json()}\n\n"
