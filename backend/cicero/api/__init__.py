"""HTTP API layer (FastAPI). Thin adapter over the debate core."""

from cicero.api.app import create_app

__all__ = ["create_app"]
