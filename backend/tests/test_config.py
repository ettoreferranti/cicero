"""Tests for settings, focusing on secret hygiene (NFR-SEC-1/3)."""

from __future__ import annotations

import pytest

from cicero.config import Settings


def test_defaults_are_safe() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.api_host == "127.0.0.1"  # localhost by default (NFR-SEC-9)
    assert s.web_access_enabled is False  # web off by default (NFR-SEC-4)
    assert s.anthropic_api_key is None
    assert s.max_rounds > 0


def test_secret_not_exposed_in_repr_or_str() -> None:
    s = Settings(_env_file=None, anthropic_api_key="super-secret-key")  # type: ignore[call-arg]
    assert "super-secret-key" not in repr(s)
    assert "super-secret-key" not in str(s)
    # The real value is only available via an explicit unwrap.
    assert s.anthropic_api_key is not None
    assert s.anthropic_api_key.get_secret_value() == "super-secret-key"


def test_secret_not_in_model_dump() -> None:
    s = Settings(_env_file=None, anthropic_api_key="leak-me")  # type: ignore[call-arg]
    dumped = str(s.model_dump())
    assert "leak-me" not in dumped


def test_env_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_HOST", "0.0.0.0")  # noqa: S104 (test asserts override works)
    monkeypatch.setenv("MAX_ROUNDS", "3")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.api_host == "0.0.0.0"  # noqa: S104
    assert s.max_rounds == 3


def test_rejects_invalid_budget() -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, max_rounds=0)  # type: ignore[call-arg]
