"""Dependency providers for the API (overridable in tests)."""

from __future__ import annotations

from functools import lru_cache

from cicero.config import get_settings
from cicero.persistence import ChamberRepository, create_repository
from cicero.providers import ProviderFactory, SettingsProviderFactory


@lru_cache
def _repository_singleton() -> ChamberRepository:
    return create_repository(get_settings().database_url)


def get_repository() -> ChamberRepository:
    """The process-wide chamber repository."""
    return _repository_singleton()


def get_provider_factory() -> ProviderFactory:
    """A provider factory bound to current settings."""
    return SettingsProviderFactory(get_settings())
