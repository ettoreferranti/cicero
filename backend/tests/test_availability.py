"""Unit tests for model-availability matching (C4 / FR-12)."""

from __future__ import annotations

from cicero.providers.availability import (
    describe_available,
    model_is_available,
    normalise_model,
)


def test_normalise_trims_cases_and_the_redundant_latest_tag() -> None:
    assert normalise_model("  Llama3  ") == "llama3"
    assert normalise_model("llama3:latest") == "llama3"
    assert normalise_model("LLAMA3:LATEST") == "llama3"
    # A meaningful tag is part of the identity and must be kept.
    assert normalise_model("llama3:70b") == "llama3:70b"
    # Registry-style names keep every earlier colon.
    assert normalise_model("registry:5000/llama3:latest") == "registry:5000/llama3"


def test_exact_and_tag_tolerant_matching() -> None:
    available = ["llama3:latest", "mistral:7b"]
    assert model_is_available("llama3:latest", available)
    assert model_is_available("llama3", available)  # the tag is implicit
    assert model_is_available("LLaMA3", available)  # casing varies
    assert model_is_available("mistral:7b", available)
    # A different tag is a different model, and must not silently pass.
    assert not model_is_available("mistral", available)
    assert not model_is_available("mistral:latest", available)
    assert not model_is_available("gpt-4", available)


def test_an_empty_listing_never_rejects() -> None:
    # "The provider told us nothing useful" is not "no models exist" — with
    # nothing to check against, blocking the user would be pure obstruction.
    assert model_is_available("anything", [])


def test_describe_available_is_short_and_truthful() -> None:
    assert describe_available(["a", "b"]) == "a, b"
    assert describe_available([]) == ""
    many = [f"m{n}" for n in range(10)]
    described = describe_available(many, limit=3)
    assert described == "m0, m1, m2, … (10 total)"
    # At the limit exactly, nothing is elided.
    assert describe_available(["a", "b", "c"], limit=3) == "a, b, c"


def test_describe_available_defaults_to_eight_models() -> None:
    # The default bounds an error message a human has to read: nine models
    # would be elided to eight plus a count.
    nine = [f"m{n}" for n in range(9)]
    assert describe_available(nine) == "m0, m1, m2, m3, m4, m5, m6, m7, … (9 total)"
    assert describe_available(nine[:8]) == ", ".join(nine[:8])
