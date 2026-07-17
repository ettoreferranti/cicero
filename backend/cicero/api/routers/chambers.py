"""Chamber, participant, debate-run, streaming, export and metrics endpoints.

Covers FR-1/2/6/7 (chambers/participants), E1 (run), E6/FR-18 (live SSE stream),
E5/FR-19 (stop control), H2/FR-31 (export), and H3/FR-33 (metrics).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from cicero.api.debate_manager import DebateManager
from cicero.api.dependencies import (
    get_debate_manager,
    get_provider_factory,
    get_repository,
)
from cicero.api.schemas import ChamberCreate, ParticipantCreate
from cicero.config import get_settings
from cicero.core.budget import DebateBudget
from cicero.core.consensus import ConsensusEngine
from cicero.core.export import to_export_dict, to_markdown
from cicero.core.metrics import ParticipantMetrics, compute_participant_metrics
from cicero.core.orchestrator import DebateEngine, TurnListener
from cicero.domain.enums import ChamberStatus
from cicero.domain.models import Chamber, Participant, ParticipantTuning
from cicero.persistence import ChamberRepository
from cicero.providers import GenerateOptions, ProviderError, ProviderFactory

router = APIRouter(prefix="/chambers", tags=["chambers"])

RepoDep = Annotated[ChamberRepository, Depends(get_repository)]
FactoryDep = Annotated[ProviderFactory, Depends(get_provider_factory)]
ManagerDep = Annotated[DebateManager, Depends(get_debate_manager)]

MIN_PARTICIPANTS = 2
_MODERATOR_MAX_TOKENS = 1024
_MODERATOR_TEMPERATURE = 0.3


def _require_chamber(repo: ChamberRepository, chamber_id: UUID) -> Chamber:
    chamber = repo.get(chamber_id)
    if chamber is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chamber not found")
    return chamber


def _build_engine(
    chamber: Chamber,
    repo: ChamberRepository,
    factory: ProviderFactory,
    listener: TurnListener | None,
) -> tuple[DebateEngine, DebateBudget]:
    """Assemble the engine + budget for a debate. May raise ProviderError."""
    moderator_source = chamber.participants[0]
    moderator = factory.get(moderator_source)  # impartial moderator = first participant
    moderator_options = GenerateOptions(
        model=moderator_source.model,
        max_tokens=_MODERATOR_MAX_TOKENS,
        temperature=_MODERATOR_TEMPERATURE,
    )
    consensus = ConsensusEngine(factory, moderator, moderator_options)
    engine = DebateEngine(factory, repo, consensus, listener=listener)
    settings = get_settings()
    budget = DebateBudget(
        max_rounds=settings.max_rounds, max_total_tokens=settings.max_total_tokens
    )
    return engine, budget


# --- Chamber CRUD ---------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED, response_model=Chamber)
def create_chamber(payload: ChamberCreate, repo: RepoDep) -> Chamber:
    chamber = Chamber(
        topic=payload.topic, category=payload.category, description=payload.description
    )
    return repo.add(chamber)


@router.get("", response_model=list[Chamber])
def list_chambers(repo: RepoDep) -> list[Chamber]:
    return repo.list()


@router.get("/{chamber_id}", response_model=Chamber)
def get_chamber(chamber_id: UUID, repo: RepoDep) -> Chamber:
    return _require_chamber(repo, chamber_id)


@router.delete("/{chamber_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_chamber(chamber_id: UUID, repo: RepoDep) -> None:
    if not repo.delete(chamber_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chamber not found")


@router.post(
    "/{chamber_id}/participants",
    status_code=status.HTTP_201_CREATED,
    response_model=Chamber,
)
def add_participant(
    chamber_id: UUID, payload: ParticipantCreate, repo: RepoDep
) -> Chamber:
    chamber = _require_chamber(repo, chamber_id)
    if chamber.status is not ChamberStatus.DRAFT:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "participants can only be added while the chamber is a draft",
        )
    tuning = (
        ParticipantTuning(**payload.tuning.model_dump())
        if payload.tuning is not None
        else ParticipantTuning()
    )
    chamber.participants.append(
        Participant(
            display_name=payload.display_name,
            provider=payload.provider,
            model=payload.model,
            stance=payload.stance,
            tuning=tuning,
        )
    )
    return repo.update(chamber)


# --- Running a debate -----------------------------------------------------


@router.post("/{chamber_id}/run", response_model=None)
async def run_debate(
    chamber_id: UUID,
    repo: RepoDep,
    factory: FactoryDep,
    manager: ManagerDep,
    wait: bool = False,
) -> Chamber | JSONResponse:
    """Start a debate. Async by default (202 + stream via /events); ``wait=true``
    runs it synchronously and returns the concluded chamber."""
    chamber = _require_chamber(repo, chamber_id)
    if chamber.status is not ChamberStatus.DRAFT:
        raise HTTPException(status.HTTP_409_CONFLICT, "only a draft chamber can be run")
    if len(chamber.participants) < MIN_PARTICIPANTS:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "a debate needs at least two participants"
        )

    def build(listener: TurnListener | None) -> tuple[DebateEngine, DebateBudget]:
        return _build_engine(chamber, repo, factory, listener)

    if wait:
        try:
            engine, budget = build(None)
            return await engine.run(chamber, budget)
        except ValueError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except ProviderError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    try:
        manager.start(chamber, build)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"status": "running", "chamber_id": str(chamber_id)},
    )


@router.get("/{chamber_id}/events")
async def stream_events(
    chamber_id: UUID, repo: RepoDep, manager: ManagerDep
) -> StreamingResponse:
    """Stream debate events (turns, status, consensus) as Server-Sent Events."""
    _require_chamber(repo, chamber_id)

    async def event_source() -> AsyncIterator[str]:
        async for event in manager.subscribe(chamber_id):
            yield event.to_sse()

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{chamber_id}/stop")
async def stop_debate(
    chamber_id: UUID, repo: RepoDep, manager: ManagerDep
) -> dict[str, str]:
    _require_chamber(repo, chamber_id)
    if not await manager.stop(chamber_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "no running debate for this chamber"
        )
    return {"status": "stopped"}


# --- Review / export / metrics -------------------------------------------


@router.get("/{chamber_id}/export", response_model=None)
def export_chamber(
    chamber_id: UUID, repo: RepoDep, format: str = "json"
) -> JSONResponse | PlainTextResponse:
    chamber = _require_chamber(repo, chamber_id)
    if format == "markdown":
        return PlainTextResponse(
            to_markdown(chamber), media_type="text/markdown; charset=utf-8"
        )
    if format == "json":
        return JSONResponse(to_export_dict(chamber))
    raise HTTPException(status.HTTP_400_BAD_REQUEST, "format must be 'json' or 'markdown'")


@router.get("/{chamber_id}/metrics")
def get_metrics(chamber_id: UUID, repo: RepoDep) -> list[ParticipantMetrics]:
    chamber = _require_chamber(repo, chamber_id)
    return compute_participant_metrics(chamber)
