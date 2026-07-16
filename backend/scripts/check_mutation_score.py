#!/usr/bin/env python3
"""Enforce a minimum mutation score from a completed ``mutmut run``.

Reads mutmut's results via ``mutmut junitxml`` (each mutant is a JUnit test
case; survived / timed-out / suspicious mutants are reported as failures) and
fails the build if the killed ratio falls below ``--min``.

Usage:
    mutmut run || true
    python scripts/check_mutation_score.py --min 80
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import xml.etree.ElementTree as ET


def _junit_xml() -> str:
    """Return mutmut's results as JUnit XML."""
    result = subprocess.run(
        ["mutmut", "junitxml"],  # noqa: S607 (fixed, trusted argv; no shell)
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 and not result.stdout.strip():
        sys.stderr.write(result.stderr)
        raise SystemExit("could not read mutmut results (did 'mutmut run' execute?)")
    return result.stdout


def score(junit_xml: str) -> tuple[int, int]:
    """Return ``(killed, total)`` from mutmut JUnit XML."""
    root = ET.fromstring(junit_xml)  # noqa: S314 (trusted local tool output)
    testcases = root.iter("testcase")
    total = 0
    killed = 0
    for case in testcases:
        total += 1
        # A mutant that was caught by the tests has no failure/error child.
        survived = case.find("failure") is not None or case.find("error") is not None
        if not survived:
            killed += 1
    return killed, total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--min",
        type=float,
        default=80.0,
        help="Minimum required mutation score, as a percentage (default: 80).",
    )
    args = parser.parse_args()

    killed, total = score(_junit_xml())
    if total == 0:
        raise SystemExit("no mutants were generated — check mutmut configuration")

    pct = 100.0 * killed / total
    print(f"Mutation score: {killed}/{total} killed = {pct:.1f}% (min {args.min:.1f}%)")
    if pct + 1e-9 < args.min:
        print("FAIL: mutation score below threshold", file=sys.stderr)
        return 1
    print("OK: mutation score meets threshold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
