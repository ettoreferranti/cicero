"""Provider abstraction: pluggable LLM backends behind one interface.

The debate core depends only on :class:`~cicero.providers.base.Provider`; concrete
adapters (Ollama, Anthropic, Mock) implement it. New providers are added here
without touching the engine (NFR-M-1).
"""

from cicero.providers.anthropic import AnthropicProvider
from cicero.providers.base import (
    GenerateOptions,
    GenerateResult,
    Message,
    Provider,
    ProviderError,
    Role,
)
from cicero.providers.factory import ProviderFactory, SettingsProviderFactory
from cicero.providers.mock import MockProvider
from cicero.providers.ollama import OllamaProvider

__all__ = [
    "AnthropicProvider",
    "GenerateOptions",
    "GenerateResult",
    "Message",
    "MockProvider",
    "OllamaProvider",
    "Provider",
    "ProviderError",
    "ProviderFactory",
    "Role",
    "SettingsProviderFactory",
]
