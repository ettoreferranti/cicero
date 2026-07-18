"""Runtime configuration, sourced from the environment (NFR-SEC-1/3).

Secrets are held as :class:`~pydantic.SecretStr` so they are never accidentally
printed, logged, or serialised. Nothing here is committed with a value — see
``.env.example`` for variable names only.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings resolved from environment variables / ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Persistence -----------------------------------------------------
    database_url: str = "sqlite:///./cicero.db"

    # --- Providers -------------------------------------------------------
    # Secret: supplied only via env; never committed or logged.
    anthropic_api_key: SecretStr | None = None
    ollama_host: str = "http://localhost:11434"

    # --- API surface (localhost by default — NFR-SEC-9) ------------------
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_allow_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # --- Debate budgets (defaults for new chambers; per-chamber settings
    # override them within their own hard caps — NFR-SEC-8) ----------------
    max_rounds: int = Field(default=8, gt=0, le=100)
    max_total_tokens: int = Field(default=200_000, gt=0)
    turn_timeout_seconds: float = Field(default=120.0, gt=0)

    # --- API hardening (NFR-SEC-8/9) -------------------------------------
    # Secret: when set, every request (except /health) must send
    # ``Authorization: Bearer <token>``.
    api_auth_token: SecretStr | None = None
    # Requests per client IP per minute; 0 disables rate limiting.
    rate_limit_per_minute: int = Field(default=240, ge=0)

    # --- Web evidence (off by default; per-chamber opt-in — NFR-SEC-4) ---
    web_access_enabled: bool = False
    web_domain_allowlist: list[str] = Field(default_factory=list)
    web_domain_denylist: list[str] = Field(default_factory=list)
    web_fetch_timeout_seconds: float = Field(default=10.0, gt=0)
    web_max_response_bytes: int = Field(default=2_000_000, gt=0)
    web_max_results: int = Field(default=3, gt=0, le=10)


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
