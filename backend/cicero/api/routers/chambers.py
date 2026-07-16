"""Chamber, participant, and debate-run endpoints (FR-1/2/6/7, E1, F1/2)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from cicero.api.dependencies import get_provider_factory, get_repository
from cicero.api.schemas import ChamberCreate, ParticipantCreate
from cicero.config import get_settings
from cicero.core.budget import DebateBudget
from cicero.core.consensus import ConsensusEngine
from cicero.core.orchestrator import DebateEngine
from cicero.domain.enums import ChamberStatus
from cicero.domain.models import Chamber, Participant, ParticipantTuning
from cicero.persistence import ChamberRepository
from cicero.providers import GenerateOptions, ProviderError, ProviderFactory

router = APIRouter(prefix="/chambers", tags=["chambers"])

RepoDep = Annotated[ChamberRepository, Depends(get_repository)]
FactoryDep = Annotated[ProviderFactory, Depends(get_provider_factory)]

_MODERATOR_MAX_TOKENS = 1024
_MODERATOR_TEMPERATURE = 0.3


def _require_chamber(repo: ChamberRepository, chamber_id: UUID) -> Chamber:
    chamber = repo.get(chamber_id)
    if chamber is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chamber not found")
    return chamber


@router.post("", status_code=status.HTTP_201_CREATED, response_model=Chamber)
def create_chamber(
    payload: ChamberCreate, repo: RepoDep
) -> Chamber:
    chamber = Chamber(
        topic=payload.topic, category=payload.category, description=payload.description
    )
    return repo.add(chamber)


@router.get("", response_model=list[Chamber])
def list_chambers(repo: RepoDep) -> list[Chamber]:
    return repo.list()


@router.get("/{chamber_id}", response_model=Chamber)
def get_chamber(
    chamber_id: UUID, repo: RepoDep
) -> Chamber:
    return _require_chamber(repo, chamber_id)


@router.delete("/{chamber_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_chamber(
    chamber_id: UUID, repo: RepoDep
) -> None:
    if not repo.delete(chamber_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chamber not found")


@router.post(
    "/{chamber_id}/participants",
    status_code=status.HTTP_201_CREATED,
    response_model=Chamber,
)
def add_participant(
    chamber_id: UUID,
    payload: ParticipantCreate,
    repo: RepoDep,
) -> Chamber:
    chamber = _require_chamber(repo, chamber_id)
    if chamber.status is not ChamberStatus.DRAFT:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "participants can only be added while the chamber is a draft"
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


@router.post("/{chamber_id}/run", response_model=Chamber)
async def run_debate(
    chamber_id: UUID,
    repo: RepoDep,
    factory: FactoryDep,
) -> Chamber:
    chamber = _require_chamber(repo, chamber_id)
    if chamber.status is not ChamberStatus.DRAFT:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "only a draft chamber can be run"
        )

    settings = get_settings()
    moderator_source = chamber.participants[0] if chamber.participants else None
    if moderator_source is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "a debate needs at least two participants")

    try:
        moderator = factory.get(moderator_source)
    except ProviderError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    moderator_options = GenerateOptions(
        model=moderator_source.model,
        max_tokens=_MODERATOR_MAX_TOKENS,
        temperature=_MODERATOR_TEMPERATURE,
    )
    consensus = ConsensusEngine(factory, moderator, moderator_options)
    engine = DebateEngine(factory, repo, consensus)
    budget = DebateBudget(
        max_rounds=settings.max_rounds, max_total_tokens=settings.max_total_tokens
    )

    try:
        return await engine.run(chamber, budget)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
