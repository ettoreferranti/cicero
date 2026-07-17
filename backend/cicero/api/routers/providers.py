"""Provider discovery endpoints (C4 / FR-12).

Lets clients list the models actually available from a provider — e.g. the
models currently loaded in a local Ollama server — so the UI can offer a
selector instead of a free-text field.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from cicero.api.dependencies import get_provider_factory
from cicero.domain.enums import ProviderType
from cicero.providers import ProviderError, ProviderFactory

router = APIRouter(prefix="/providers", tags=["providers"])

FactoryDep = Annotated[ProviderFactory, Depends(get_provider_factory)]


class ProviderModels(BaseModel):
    """The models a provider can currently serve."""

    provider: ProviderType
    models: list[str]


@router.get("", response_model=list[str])
def list_providers() -> list[str]:
    """The provider types this deployment supports."""
    return [p.value for p in ProviderType]


@router.get("/{provider_type}/models", response_model=ProviderModels)
async def list_models(provider_type: ProviderType, factory: FactoryDep) -> ProviderModels:
    """List the models available from ``provider_type`` right now.

    Returns 502 when the provider is unreachable or not configured (e.g. no
    Ollama server running, or no Anthropic API key in the environment).
    """
    try:
        provider = factory.get_for_type(provider_type)
        models = await provider.list_models()
    except ProviderError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return ProviderModels(provider=provider_type, models=models)
