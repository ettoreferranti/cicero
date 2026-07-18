"""Runs debates in the background and streams their events to subscribers.

A debate is executed as an asyncio task; each turn (via the engine's
``TurnListener`` hook) and each status change is published to any connected SSE
subscribers, and retained so a client that connects mid-debate can replay what it
missed. Supports live streaming (FR-18) and a stop control (FR-19).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from uuid import UUID

from cicero.api.events import DebateEvent, DebateEventType
from cicero.core.budget import DebateBudget
from cicero.core.orchestrator import DebateEngine, NoteSource, TurnListener
from cicero.domain.models import Chamber, Turn

# Builds the engine (wired with the manager's listener + note queue) and its budget.
BuildEngine = Callable[[TurnListener, NoteSource], tuple[DebateEngine, DebateBudget]]


class _NoteQueue:
    """Moderator notes queued for injection between turns (FR-21)."""

    def __init__(self) -> None:
        self._notes: list[str] = []

    def add(self, content: str) -> None:
        self._notes.append(content)

    def drain(self) -> list[str]:
        notes, self._notes = self._notes, []
        return notes


class _RunningDebate:
    def __init__(self) -> None:
        self.history: list[DebateEvent] = []
        self.subscribers: set[asyncio.Queue[DebateEvent | None]] = set()
        self.task: asyncio.Task[None] | None = None
        self.done: bool = False
        self.notes = _NoteQueue()


class _PublishingListener:
    """Bridges the engine's turn hook to the manager's event stream."""

    def __init__(self, manager: DebateManager, chamber_id: UUID) -> None:
        self._manager = manager
        self._chamber_id = chamber_id

    async def on_turn(self, chamber: Chamber, turn: Turn) -> None:
        self._manager._emit(
            self._chamber_id,
            DebateEvent(
                type=DebateEventType.TURN,
                payload={"turn": turn.model_dump(mode="json")},
            ),
        )


class DebateManager:
    """Owns the set of in-flight debates and their event streams."""

    def __init__(self) -> None:
        self._debates: dict[UUID, _RunningDebate] = {}

    def is_running(self, chamber_id: UUID) -> bool:
        debate = self._debates.get(chamber_id)
        return debate is not None and not debate.done

    def exists(self, chamber_id: UUID) -> bool:
        return chamber_id in self._debates

    def start(self, chamber: Chamber, build_engine: BuildEngine) -> None:
        """Spawn a background debate for ``chamber``."""
        if self.is_running(chamber.id):
            raise RuntimeError("a debate is already running for this chamber")
        listener = _PublishingListener(self, chamber.id)
        debate = _RunningDebate()
        # Build before registering so a build failure (e.g. missing API key) does
        # not leave a broken entry behind.
        engine, budget = build_engine(listener, debate.notes)
        self._debates[chamber.id] = debate
        debate.task = asyncio.create_task(self._run(chamber, engine, budget))

    def add_note(self, chamber_id: UUID, content: str) -> bool:
        """Queue a moderator note for a running debate. False if none is running."""
        debate = self._debates.get(chamber_id)
        if debate is None or debate.done:
            return False
        debate.notes.add(content)
        return True

    async def stop(self, chamber_id: UUID) -> bool:
        """Cancel a running debate. Returns True if one was stopped."""
        debate = self._debates.get(chamber_id)
        if debate is None or debate.task is None or debate.done:
            return False
        debate.task.cancel()
        try:
            await debate.task
        except asyncio.CancelledError:
            pass
        return True

    async def subscribe(self, chamber_id: UUID) -> AsyncIterator[DebateEvent]:
        """Yield past events, then live events until the debate finishes."""
        debate = self._debates.get(chamber_id)
        if debate is None:
            return
        if debate.done:
            for event in list(debate.history):
                yield event
            return
        queue: asyncio.Queue[DebateEvent | None] = asyncio.Queue()
        debate.subscribers.add(queue)
        snapshot = list(debate.history)  # sync snapshot; no await before this
        try:
            for event in snapshot:
                yield event
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        finally:
            debate.subscribers.discard(queue)

    async def _run(
        self, chamber: Chamber, engine: DebateEngine, budget: DebateBudget
    ) -> None:
        cid = chamber.id
        self._emit(cid, DebateEvent(type=DebateEventType.STATUS, payload={"status": "running"}))
        try:
            result = await engine.run(chamber, budget)
            if result.consensus is not None:
                self._emit(
                    cid,
                    DebateEvent(
                        type=DebateEventType.CONSENSUS,
                        payload={
                            "consensus": result.consensus.model_dump(mode="json"),
                            "config": result.config,
                        },
                    ),
                )
            self._emit(
                cid,
                DebateEvent(type=DebateEventType.STATUS, payload={"status": "concluded"}),
            )
        except asyncio.CancelledError:
            self._emit(
                cid, DebateEvent(type=DebateEventType.STATUS, payload={"status": "stopped"})
            )
            raise
        except Exception as exc:  # noqa: BLE001 (surface any engine error to the stream)
            self._emit(
                cid, DebateEvent(type=DebateEventType.ERROR, payload={"message": str(exc)})
            )
        finally:
            self._finish(cid)

    def _emit(self, chamber_id: UUID, event: DebateEvent) -> None:
        debate = self._debates.get(chamber_id)
        if debate is None:
            return
        debate.history.append(event)
        for queue in list(debate.subscribers):
            queue.put_nowait(event)

    def _finish(self, chamber_id: UUID) -> None:
        debate = self._debates.get(chamber_id)
        if debate is None:
            return
        debate.done = True
        done = DebateEvent(type=DebateEventType.DONE)
        debate.history.append(done)
        for queue in list(debate.subscribers):
            queue.put_nowait(done)
            queue.put_nowait(None)  # sentinel closes the stream
