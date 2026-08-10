"""Chamber, participant, debate-run, streaming, export and metrics endpoints.

Covers FR-1/2/6/7 (chambers/participants), D4/FR-3 (draft editing), E1 (run),
E6/FR-18 (live SSE stream), E5/FR-19 (start/step/pause/resume/stop),
H2/FR-31 (export), and H3/FR-33 (metrics).
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
    get_evidence_service,
    get_provider_factory,
    get_repository,
)
from cicero.api.schemas import (
    ChamberCreate,
    ChamberUpdate,
    DebateSettingsIn,
    ModeratorIn,
    ModeratorNoteIn,
    MuteIn,
    ParticipantCreate,
    ParticipantUpdate,
)
from cicero.core.budget import DebateBudget
from cicero.core.compare import compare_chambers
from cicero.core.compliance import ComplianceJudge
from cicero.core.consensus import ConsensusEngine
from cicero.core.export import to_export_dict, to_markdown
from cicero.core.metrics import ParticipantMetrics, compute_participant_metrics
from cicero.core.orchestrator import (
    DebateEngine,
    MuteSource,
    NoteSource,
    TurnLimit,
    TurnListener,
)
from cicero.core.outcome import summarize_outcome
from cicero.core.prompt_builder import KIND_MODERATOR_NOTE
from cicero.core.roster import active_participants
from cicero.domain.enums import ChamberStatus, ProviderType
from cicero.domain.models import (
    Chamber,
    DebateSettings,
    Moderator,
    Participant,
    ParticipantTuning,
    Turn,
)
from cicero.persistence import ChamberRepository
from cicero.providers import GenerateOptions, ProviderError, ProviderFactory
from cicero.providers.availability import describe_available, model_is_available
from cicero.tools.web import EvidenceService

router = APIRouter(prefix="/chambers", tags=["chambers"])

RepoDep = Annotated[ChamberRepository, Depends(get_repository)]
FactoryDep = Annotated[ProviderFactory, Depends(get_provider_factory)]
ManagerDep = Annotated[DebateManager, Depends(get_debate_manager)]
EvidenceDep = Annotated[EvidenceService | None, Depends(get_evidence_service)]

MIN_PARTICIPANTS = 2
# Spelled as a literal: Starlette renamed HTTP_422_UNPROCESSABLE_ENTITY to
# ..._CONTENT, and the constant we can rely on across the supported range is the
# number itself.
_UNPROCESSABLE = 422


def _require_chamber(repo: ChamberRepository, chamber_id: UUID) -> Chamber:
    chamber = repo.get(chamber_id)
    if chamber is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chamber not found")
    return chamber


def _require_draft(repo: ChamberRepository, chamber_id: UUID, subject: str) -> Chamber:
    """Fetch a chamber that must still be editable (FR-3).

    A clone is created as a fresh `draft`, so this also covers editing a cloned
    chamber before its rerun.
    """
    chamber = _require_chamber(repo, chamber_id)
    if chamber.status is not ChamberStatus.DRAFT:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{subject} can only be changed while the chamber is a draft",
        )
    return chamber


def _require_participant(chamber: Chamber, participant_id: UUID) -> Participant:
    participant = chamber.participant_by_id(participant_id)
    if participant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "participant not found")
    return participant


async def _validate_model(
    factory: ProviderFactory, provider_type: ProviderType, model: str
) -> None:
    """Check the provider is reachable and can serve ``model`` (C4/FR-12).

    Catching this at add/edit time turns a mid-debate empty turn into an
    immediate, fixable error. Unreachable provider → 502 (that is the
    connectivity half of FR-12); reachable but unknown model → 422, naming what
    it *can* serve.
    """
    try:
        provider = factory.get_for_type(provider_type)
        available = await provider.list_models()
    except ProviderError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    if not model_is_available(model, available):
        raise HTTPException(
            _UNPROCESSABLE,
            f"{provider_type.value} cannot serve model {model!r}; "
            f"available: {describe_available(available)}",
        )


def _moderator_from(payload: ModeratorIn) -> Moderator:
    """Convert the wire shape, dropping unset tuning so domain defaults apply."""
    return Moderator(**payload.model_dump(exclude_none=True))


def _build_engine(
    chamber: Chamber,
    repo: ChamberRepository,
    factory: ProviderFactory,
    listener: TurnListener | None,
    evidence: EvidenceService | None = None,
    notes: NoteSource | None = None,
    mutes: MuteSource | None = None,
) -> tuple[DebateEngine, DebateBudget]:
    """Assemble the engine + budget for a debate. May raise ProviderError."""
    moderator_config = chamber.moderator
    if moderator_config is None:
        # Pre-F8 chambers, and any caller that omits the field: the arbiter is the
        # first participant, which is what the engine did before it was explicit.
        source = chamber.participants[0]
        moderator_config = Moderator(provider=source.provider, model=source.model)
    # get_for_type, not get: the factory's get takes a Participant, and a moderator
    # is deliberately not one.
    moderator = factory.get_for_type(moderator_config.provider)
    moderator_options = GenerateOptions(
        model=moderator_config.model,
        max_tokens=moderator_config.max_tokens,
        temperature=moderator_config.temperature,
    )
    consensus = ConsensusEngine(factory, moderator, moderator_options)
    # The moderator judges compliance too: it is the chamber's designated
    # impartial party, and ComplianceJudge supplies its own options because a
    # one-word answer wants none of the moderator's synthesis budget.
    judge = ComplianceJudge(moderator, moderator_config.model)
    engine = DebateEngine(
        factory,
        repo,
        consensus,
        listener=listener,
        evidence=evidence,
        notes=notes,
        mutes=mutes,
        judge=judge,
    )
    settings = chamber.settings  # per-chamber tuning (FR-16/FR-11)
    budget = DebateBudget(
        max_rounds=settings.max_rounds,
        max_total_tokens=settings.max_total_tokens,
        min_rounds=settings.min_rounds,
        max_duration_seconds=settings.max_duration_seconds,
    )
    return engine, budget


# --- Chamber CRUD ---------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED, response_model=Chamber)
async def create_chamber(
    payload: ChamberCreate, repo: RepoDep, factory: FactoryDep
) -> Chamber:
    settings = (
        DebateSettings(**payload.settings.model_dump())
        if payload.settings is not None
        else DebateSettings()
    )
    moderator = None
    if payload.moderator is not None:
        # Checked here for the same reason a participant's model is (C4/FR-12):
        # otherwise a wrong moderator model surfaces only after the whole debate
        # has run and been paid for.
        await _validate_model(factory, payload.moderator.provider, payload.moderator.model)
        moderator = _moderator_from(payload.moderator)
    chamber = Chamber(
        topic=payload.topic,
        category=payload.category,
        description=payload.description,
        settings=settings,
        moderator=moderator,
    )
    return repo.add(chamber)


@router.patch("/{chamber_id}", response_model=Chamber)
async def update_chamber(
    chamber_id: UUID, payload: ChamberUpdate, repo: RepoDep, factory: FactoryDep
) -> Chamber:
    """Edit topic/category/description while the chamber is a draft (FR-3).

    Only the fields actually sent are applied. A cloned chamber is a fresh
    draft, so this is how a rerun gets retargeted before it starts.
    """
    chamber = _require_draft(repo, chamber_id, "a chamber")
    changes = payload.model_dump(exclude_unset=True)
    if payload.moderator is not None:
        await _validate_model(factory, payload.moderator.provider, payload.moderator.model)
        # The dump would otherwise assign a ModeratorIn where a Moderator belongs.
        changes["moderator"] = _moderator_from(payload.moderator)
    for field, value in changes.items():
        setattr(chamber, field, value)
    return repo.update(chamber)


@router.put("/{chamber_id}/settings", response_model=Chamber)
def update_settings(
    chamber_id: UUID, payload: DebateSettingsIn, repo: RepoDep
) -> Chamber:
    """Tune the debate (rounds, token/time budgets, decision rule) while a draft."""
    chamber = _require_draft(repo, chamber_id, "settings")
    chamber.settings = DebateSettings(**payload.model_dump())
    return repo.update(chamber)


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


@router.post("/{chamber_id}/clone", status_code=status.HTTP_201_CREATED, response_model=Chamber)
def clone_chamber(chamber_id: UUID, repo: RepoDep) -> Chamber:
    """Create a fresh draft copy (topic, settings, participants) to rerun a debate.

    Turns and the consensus result are not copied; participants get new ids.
    Pairs with ``GET /chambers/{a}/compare/{b}`` for run-vs-run comparison (FR-32).
    """
    source = _require_chamber(repo, chamber_id)
    clone = Chamber(
        topic=source.topic,
        category=source.category,
        description=source.description,
        settings=source.settings.model_copy(deep=True),
        participants=[
            Participant(
                display_name=participant.display_name,
                provider=participant.provider,
                model=participant.model,
                stance=participant.stance,
                tuning=participant.tuning.model_copy(deep=True),
            )
            for participant in source.participants
        ],
    )
    return repo.add(clone)


@router.post(
    "/{chamber_id}/participants",
    status_code=status.HTTP_201_CREATED,
    response_model=Chamber,
)
async def add_participant(
    chamber_id: UUID, payload: ParticipantCreate, repo: RepoDep, factory: FactoryDep
) -> Chamber:
    chamber = _require_draft(repo, chamber_id, "the participant roster")
    await _validate_model(factory, payload.provider, payload.model)
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


@router.patch("/{chamber_id}/participants/{participant_id}", response_model=Chamber)
async def update_participant(
    chamber_id: UUID,
    participant_id: UUID,
    payload: ParticipantUpdate,
    repo: RepoDep,
    factory: FactoryDep,
) -> Chamber:
    """Edit a debater while the chamber is a draft (FR-3).

    Only the fields actually sent are applied, so a cloned chamber can be rerun
    with (say) one debater on a different model, everything else held constant.
    A changed provider/model is re-validated the same way an add is (C4/FR-12).
    """
    chamber = _require_draft(repo, chamber_id, "the participant roster")
    participant = _require_participant(chamber, participant_id)
    changes = payload.model_dump(exclude_unset=True)
    if "provider" in changes or "model" in changes:
        await _validate_model(
            factory,
            changes.get("provider", participant.provider),
            changes.get("model", participant.model),
        )
    tuning = changes.pop("tuning", None)
    for field, value in changes.items():
        setattr(participant, field, value)
    if tuning is not None:
        participant.tuning = ParticipantTuning(**tuning)
    return repo.update(chamber)


@router.post("/{chamber_id}/participants/{participant_id}/mute")
def set_participant_muted(
    chamber_id: UUID,
    participant_id: UUID,
    payload: MuteIn,
    repo: RepoDep,
    manager: ManagerDep,
) -> dict[str, str]:
    """Mute or unmute a debater, including mid-debate (D6/FR-13).

    A muted debater stops taking turns and stops counting toward the decision
    rule, but stays in the chamber and is still polled for its stance, so the
    stance history keeps a continuous record. Because it changes the tally,
    muting can change the outcome — that is the point of the control.

    While a debate is running the change is queued and applied at the **next
    round boundary**, never mid-round: a debater's muted state stays constant
    for a whole round, which is what keeps round completion — and so resume and
    step — coherent. On a draft or paused chamber it applies immediately.
    """
    chamber = _require_chamber(repo, chamber_id)
    participant = _require_participant(chamber, participant_id)

    if payload.muted:
        remaining = [p for p in active_participants(chamber) if p.id != participant_id]
        if len(remaining) < MIN_PARTICIPANTS:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "a debate needs at least two unmuted participants",
            )

    if manager.set_muted(chamber_id, participant_id, payload.muted):
        return {"status": "queued"}
    if chamber.status is ChamberStatus.RUNNING:
        # Running, but no live task owns it (e.g. after a restart) — the change
        # would be silently lost, so say so rather than pretend.
        raise HTTPException(
            status.HTTP_409_CONFLICT, "no running debate for this chamber"
        )
    participant.muted = payload.muted
    repo.update(chamber)
    return {"status": "muted" if payload.muted else "unmuted"}


@router.delete("/{chamber_id}/participants/{participant_id}", response_model=Chamber)
def remove_participant(
    chamber_id: UUID, participant_id: UUID, repo: RepoDep
) -> Chamber:
    """Remove a debater while the chamber is a draft (FR-3).

    A draft may drop below two participants — `/run` is what enforces the
    minimum, so a roster can be rebuilt freely before the debate starts.
    """
    chamber = _require_draft(repo, chamber_id, "the participant roster")
    _require_participant(chamber, participant_id)
    chamber.participants = [
        participant
        for participant in chamber.participants
        if participant.id != participant_id
    ]
    return repo.update(chamber)


# --- Running a debate -----------------------------------------------------


@router.post("/{chamber_id}/run", response_model=None)
async def run_debate(
    chamber_id: UUID,
    repo: RepoDep,
    factory: FactoryDep,
    manager: ManagerDep,
    evidence: EvidenceDep,
    wait: bool = False,
) -> Chamber | JSONResponse:
    """Start a debate. Async by default (202 + stream via /events); ``wait=true``
    runs it synchronously and returns the concluded chamber."""
    chamber = _require_chamber(repo, chamber_id)
    if chamber.status is not ChamberStatus.DRAFT:
        raise HTTPException(status.HTTP_409_CONFLICT, "only a draft chamber can be run")
    return await _launch_debate(chamber, repo, factory, manager, evidence, wait)


@router.post("/{chamber_id}/resume", response_model=None)
async def resume_debate(
    chamber_id: UUID,
    repo: RepoDep,
    factory: FactoryDep,
    manager: ManagerDep,
    evidence: EvidenceDep,
    wait: bool = False,
) -> Chamber | JSONResponse:
    """Resume a paused debate from where it stopped (J3/NFR-R-3, FR-19).

    Chambers land in ``paused`` when stopped mid-debate or when a restart
    interrupted them; the engine continues at the first round with a missing
    turn, and prior token/round spend still counts against the budget.
    """
    chamber = _require_chamber(repo, chamber_id)
    if chamber.status is not ChamberStatus.PAUSED:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "only a paused chamber can be resumed"
        )
    return await _launch_debate(chamber, repo, factory, manager, evidence, wait)


@router.post("/{chamber_id}/step", response_model=Chamber)
async def step_debate(
    chamber_id: UUID,
    repo: RepoDep,
    factory: FactoryDep,
    manager: ManagerDep,
    evidence: EvidenceDep,
) -> Chamber:
    """Advance the debate by a single turn, then park it as `paused` (E5/FR-19).

    Works from `draft` (the debate starts) and from `paused` (it continues where
    it stopped). Runs synchronously and returns the updated chamber, so the
    caller sees the new turn without subscribing to the stream. A debate whose
    rounds or budget are spent — or one whose stepped turn closed a round on
    consensus — concludes on this step instead of parking.
    """
    chamber = _require_chamber(repo, chamber_id)
    if chamber.status not in (ChamberStatus.DRAFT, ChamberStatus.PAUSED):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "only a draft or paused chamber can be stepped"
        )
    if manager.is_running(chamber_id):
        # The chamber can still read as `draft` in the window between /run
        # returning 202 and the background task starting.
        raise HTTPException(
            status.HTTP_409_CONFLICT, "a debate is already running for this chamber"
        )
    if len(chamber.participants) < MIN_PARTICIPANTS:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "a debate needs at least two participants"
        )
    try:
        engine, budget = _build_engine(chamber, repo, factory, None, evidence, None)
        return await engine.run(chamber, budget, TurnLimit(1))
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


async def _launch_debate(
    chamber: Chamber,
    repo: ChamberRepository,
    factory: ProviderFactory,
    manager: DebateManager,
    evidence: EvidenceService | None,
    wait: bool,
) -> Chamber | JSONResponse:
    if len(chamber.participants) < MIN_PARTICIPANTS:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "a debate needs at least two participants"
        )

    def build(
        listener: TurnListener | None,
        notes: NoteSource | None,
        mutes: MuteSource | None = None,
    ) -> tuple[DebateEngine, DebateBudget]:
        return _build_engine(chamber, repo, factory, listener, evidence, notes, mutes)

    if wait:
        try:
            engine, budget = build(None, None)
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
        content={"status": "running", "chamber_id": str(chamber.id)},
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


@router.post("/{chamber_id}/notes", status_code=status.HTTP_202_ACCEPTED)
def add_moderator_note(
    chamber_id: UUID, payload: ModeratorNoteIn, repo: RepoDep, manager: ManagerDep
) -> dict[str, str]:
    """Inject a moderator note into the debate (FR-21).

    Queued between turns while a debate is running; appended directly to the
    transcript while the chamber is a draft or paused.
    """
    chamber = _require_chamber(repo, chamber_id)
    if manager.add_note(chamber_id, payload.content):
        return {"status": "queued"}
    if chamber.status in (ChamberStatus.DRAFT, ChamberStatus.PAUSED):
        next_round = max((turn.round_index for turn in chamber.turns), default=0)
        chamber.turns.append(
            Turn(
                participant_id=None,
                round_index=next_round,
                content=payload.content,
                metadata={"kind": KIND_MODERATOR_NOTE},
            )
        )
        repo.update(chamber)
        return {"status": "added"}
    raise HTTPException(
        status.HTTP_409_CONFLICT, "notes cannot be added to a concluded chamber"
    )


@router.get("/{chamber_id}/compare/{other_id}")
def compare_runs(
    chamber_id: UUID, other_id: UUID, repo: RepoDep
) -> JSONResponse:
    """Compare two debate runs side by side (FR-32)."""
    a = _require_chamber(repo, chamber_id)
    b = _require_chamber(repo, other_id)
    return JSONResponse(compare_chambers(a, b))


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


@router.get("/{chamber_id}/outcome")
def get_outcome(chamber_id: UUID, repo: RepoDep) -> dict[str, object]:
    """The concluded debate's headline and derived facts (F5, FR-23).

    Served rather than re-derived in the UI so the wording has exactly one
    implementation — the same reason ``/metrics`` is an endpoint.
    """
    chamber = _require_chamber(repo, chamber_id)
    summary = summarize_outcome(chamber)
    if summary is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chamber has no outcome yet")
    return {
        "headline": summary.headline,
        "support": summary.support,
        "decided_by": summary.decided_by,
        "movements": list(summary.movements),
        "caveat": summary.caveat,
    }
