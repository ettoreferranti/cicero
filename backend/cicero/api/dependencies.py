"""Dependency providers for the API (overridable in tests)."""

from __future__ import annotations

from functools import lru_cache

from cicero.api.debate_manager import DebateManager
from cicero.config import get_settings
from cicero.core.recovery import recover_interrupted_debates
from cicero.persistence import ChamberRepository, create_repository
from cicero.providers import ProviderFactory, SettingsProviderFactory
from cicero.tools.web import DuckDuckGoBackend, EvidenceService, SafeWebClient


@lru_cache
def _repository_singleton() -> ChamberRepository:
    repo = create_repository(get_settings().database_url)
    # Debates interrupted by the previous shutdown become resumable (J3).
    recover_interrupted_debates(repo)
    return repo


@lru_cache
def _debate_manager_singleton() -> DebateManager:
    return DebateManager()


def get_repository() -> ChamberRepository:
    """The process-wide chamber repository."""
    return _repository_singleton()


def get_provider_factory() -> ProviderFactory:
    """A provider factory bound to current settings."""
    return SettingsProviderFactory(get_settings())


def get_debate_manager() -> DebateManager:
    """The process-wide manager of in-flight debates."""
    return _debate_manager_singleton()


def get_evidence_service() -> EvidenceService | None:
    """The web-evidence service, or ``None`` unless globally enabled (FR-26).

    Chambers additionally opt in per debate via ``settings.web_evidence``.
    """
    settings = get_settings()
    if not settings.web_access_enabled:
        return None
    client = SafeWebClient(
        timeout_seconds=settings.web_fetch_timeout_seconds,
        max_response_bytes=settings.web_max_response_bytes,
        allowlist=settings.web_domain_allowlist,
        denylist=settings.web_domain_denylist,
    )
    return EvidenceService(
        search=DuckDuckGoBackend(timeout_seconds=settings.web_fetch_timeout_seconds),
        client=client,
        max_results=settings.web_max_results,
    )
