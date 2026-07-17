"""Tests for the SSE event wire format."""

from __future__ import annotations

import json

from cicero.api.events import DebateEvent, DebateEventType


def test_to_sse_frame_shape() -> None:
    event = DebateEvent(type=DebateEventType.STATUS, payload={"status": "running"})
    frame = event.to_sse()
    assert frame.startswith("event: status\n")
    assert "data: " in frame
    assert frame.endswith("\n\n")


def test_to_sse_data_is_valid_json_with_type_and_payload() -> None:
    event = DebateEvent(type=DebateEventType.TURN, payload={"n": 1})
    data_line = [ln for ln in event.to_sse().splitlines() if ln.startswith("data: ")][0]
    parsed = json.loads(data_line.removeprefix("data: "))
    assert parsed["type"] == "turn"
    assert parsed["payload"] == {"n": 1}


def test_done_event_has_empty_payload() -> None:
    assert DebateEvent(type=DebateEventType.DONE).payload == {}
