"""Model-availability matching for participant validation (C4 / FR-12).

Adding a debater on a model the provider cannot serve is a mistake you would
otherwise only discover mid-debate, as an empty turn with an ``error`` in its
metadata. Checking at add time turns that into an immediate, fixable error.

The matching has to be forgiving in one specific way: providers report *tagged*
identifiers (Ollama's ``llama3:latest``) while people type the bare name, and
casing varies. Rejecting a model the provider would actually have accepted is
worse than not checking at all, so the comparison normalises both sides and
treats a bare name as matching its ``:latest`` tag. Anything looser would defeat
the purpose.

Pure and dependency-free, so it is deterministic under test and in the mutation
gate.
"""

from __future__ import annotations

from collections.abc import Iterable

#: Ollama (and others) report an implicit ``:latest`` tag that users omit.
_DEFAULT_TAG = "latest"


def normalise_model(name: str) -> str:
    """Casefold, trim, and drop a redundant ``:latest`` tag."""
    cleaned = name.strip().casefold()
    prefix, separator, tag = cleaned.rpartition(":")
    if separator and tag == _DEFAULT_TAG:
        return prefix
    return cleaned


def model_is_available(requested: str, available: Iterable[str]) -> bool:
    """Whether ``requested`` names one of the provider's ``available`` models.

    An **empty** listing means "the provider told us nothing useful", not "no
    models exist", so it never rejects — there is nothing to check against.
    """
    known = {normalise_model(name) for name in available}
    if not known:
        return True
    return normalise_model(requested) in known


def describe_available(available: Iterable[str], limit: int = 8) -> str:
    """A short, stable rendering of the model list for an error message."""
    models = list(available)
    shown = ", ".join(models[:limit])
    if len(models) > limit:
        return f"{shown}, … ({len(models)} total)"
    return shown
