"""Provider factory: resolves a participant to a concrete provider.

The debate engine depends on the :class:`ProviderFactory` protocol, never on
concrete providers, so tests can inject deterministic mock providers.
"""

from __future__ import annotations

from typing import Protocol

from cicero.config import Settings, get_settings
from cicero.domain.enums import ProviderType
from cicero.domain.models import Participant
from cicero.providers.anthropic import AnthropicProvider
from cicero.providers.base import Provider, ProviderError
from cicero.providers.mock import MockProvider
from cicero.providers.ollama import OllamaProvider
from cicero.providers.retry import RetryPolicy


class ProviderFactory(Protocol):
    """Resolves a participant (or a provider type) to a provider."""

    def get(self, participant: Participant) -> Provider: ...

    def get_for_type(self, provider_type: ProviderType) -> Provider: ...


class SettingsProviderFactory:
    """Builds providers from application settings, caching one per provider type."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._cache: dict[ProviderType, Provider] = {}

    def get(self, participant: Participant) -> Provider:
        return self.get_for_type(participant.provider)

    def get_for_type(self, provider_type: ProviderType) -> Provider:
        cached = self._cache.get(provider_type)
        if cached is not None:
            return cached
        provider = self._build(provider_type)
        self._cache[provider_type] = provider
        return provider

    def _retry_policy(self) -> RetryPolicy:
        return RetryPolicy(
            max_attempts=self._settings.provider_max_attempts,
            base_delay_seconds=self._settings.provider_retry_base_delay_seconds,
        )

    def _build(self, provider_type: ProviderType) -> Provider:
        if provider_type is ProviderType.MOCK:
            return MockProvider()
        if provider_type is ProviderType.OLLAMA:
            return OllamaProvider(
                host=self._settings.ollama_host, retry=self._retry_policy()
            )
        if provider_type is ProviderType.ANTHROPIC:
            key = self._settings.anthropic_api_key
            if key is None:
                raise ProviderError("Anthropic API key is not configured")
            return AnthropicProvider(
                api_key=key.get_secret_value(), retry=self._retry_policy()
            )
        raise ProviderError(f"unsupported provider: {provider_type}")
