"""End-to-end release demo, run for real (J4 — requirements.md §9).

Unlike the rest of the suite, this spawns ``scripts/demo.py`` as its own
process: it starts a real uvicorn server on a free port, talks to it over HTTP
(including the SSE stream), and walks the whole release path — create chamber,
add participants, run, converge, metrics, export, clone-and-compare.

It is pinned to the offline ``mock`` provider so it stays deterministic and
network-free. Marked ``e2e`` so the mutation gate can skip it (see
``setup.cfg``); ordinary ``pytest`` runs it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEMO = BACKEND_ROOT / "scripts" / "demo.py"

pytestmark = pytest.mark.e2e


def _run_demo(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 (fixed argv built from this file; no shell)
        [sys.executable, str(DEMO), *args],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def test_release_demo_passes_end_to_end(tmp_path: Path) -> None:
    result = _run_demo(
        "--participant",
        "Ada:mock:mock-small:pro",
        "--participant",
        "Zeno:mock:mock-large:con",
        "--max-rounds",
        "2",
        "--compare",
        "--out",
        str(tmp_path),
    )

    assert result.returncode == 0, f"demo failed:\n{result.stdout}\n{result.stderr}"
    assert "DEMO PASSED" in result.stdout
    assert "FAIL" not in result.stdout

    # Every release-critical step left evidence behind.
    assert list(tmp_path.glob("chamber-*.json")), "no JSON export was written"
    markdown = next(iter(tmp_path.glob("chamber-*.md")))
    assert "## Transcript" in markdown.read_text(encoding="utf-8")


def test_demo_reports_failure_when_a_criterion_cannot_be_met(tmp_path: Path) -> None:
    """``--strict-providers`` turns a single-provider run into a failing demo."""
    result = _run_demo(
        "--participant",
        "Ada:mock:mock-small:pro",
        "--participant",
        "Zeno:mock:mock-large:con",
        "--max-rounds",
        "1",
        "--strict-providers",
        "--out",
        str(tmp_path),
    )

    assert result.returncode == 1
    assert "DEMO FAILED" in result.stdout
    assert "debate spans >=2 providers" in result.stdout


def test_demo_rejects_a_malformed_participant_spec(tmp_path: Path) -> None:
    result = _run_demo("--participant", "Ada:mock:mock-small", "--out", str(tmp_path))
    assert result.returncode != 0
    assert "NAME:PROVIDER:MODEL:STANCE" in result.stderr
