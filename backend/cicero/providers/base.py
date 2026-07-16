"""The provider interface and its request/response value objects."""

from __future__ import annotations

import abc
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from cicero.domain.enums import ProviderType


class Role(StrEnum):
    """Chat message roles understood by every provider."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class Message(BaseModel):
    """A single chat message passed to a provider."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Role
    content: str


class GenerateOptions(BaseModel):
    """Generation parameters, provider-agnostic."""

    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=800, gt=0, le=32768)
    stop: list[str] = Field(default_factory=list)


class GenerateResult(BaseModel):
    """The result of a generation call."""

    model_config = ConfigDict(extra="forbid")

    content: str
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    metadata: dict[str, object] = Field(default_factory=dict)


class ProviderError(RuntimeError):
    """Raised when a provider cannot fulfil a request.

    Carries no secrets — messages are safe to surface to callers (NFR-SEC-3).
    """


class Provider(abc.ABC):
    """Common interface every LLM backend implements.

    Implementations must not leak credentials in results, logs, or errors.
    """

    #: The provider family this adapter implements.
    provider_type: ProviderType

    @abc.abstractmethod
    async def generate(
        self, messages: list[Message], options: GenerateOptions
    ) -> GenerateResult:
        """Produce a completion for ``messages`` under ``options``."""
        raise NotImplementedError

    @abc.abstractmethod
    async def list_models(self) -> list[str]:
        """Return the model identifiers available from this provider."""
        raise NotImplementedError
