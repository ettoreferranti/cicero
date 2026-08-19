"""The F10 benchmark harness: replay hand-read turns past a judge and score it.

The fixtures this drives (``f10_invasion_turns.md``, ``f10_hand_labels.json``,
``f10_baseline_verdicts.json``) were the expensive part of F10 and were kept when
the feature was reverted — but nothing executed them, so the benchmark was a
frozen record rather than a test anyone could re-run. These tests cover the
parsing and the arithmetic; the live judge run is a manual measurement step and
no test here touches a provider that leaves the process.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = BACKEND_ROOT / "tests" / "fixtures"


def _load_script() -> Any:
    """Import ``scripts/score_compliance_judge.py`` as a module.

    It lives outside the ``cicero`` package on purpose — it is measurement code,
    not something a debate imports — but its parsing and scoring decide what a
    measurement *concludes*, so it is tested like anything else.
    """
    path = BACKEND_ROOT / "scripts" / "score_compliance_judge.py"
    spec = importlib.util.spec_from_file_location("score_compliance_judge", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: @dataclass resolves annotations through
    # sys.modules[cls.__module__], and raises without it.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


script = _load_script()


# --- Parsing the turns fixture ----------------------------------------------

TURNS_MD = """## Turn abc-123

- chamber: 832b06b5
- motion: Cats are better than dogs.
- round: 0
- speaker: JD
- assigned stance: pro

Cats are clearly superior.

They need no walking.

## Turn def-456

- chamber: 832b06b5
- motion: Cats are better than dogs.
- round: 1
- speaker: Kamala
- assigned stance: con

Dogs are loyal.
"""


def test_parse_reads_every_turn() -> None:
    turns = script.parse_turns_markdown(TURNS_MD)
    assert [t.turn_id for t in turns] == ["abc-123", "def-456"]


def test_parse_reads_the_metadata_of_a_turn() -> None:
    first = script.parse_turns_markdown(TURNS_MD)[0]
    assert first.topic == "Cats are better than dogs."
    assert first.speaker == "JD"
    assert first.assigned == "pro"
    assert first.round_index == 0


def test_parse_keeps_multi_paragraph_content_whole() -> None:
    """Turns run to several paragraphs; a parser that stopped at the first blank
    line would silently judge only the opening sentence."""
    first = script.parse_turns_markdown(TURNS_MD)[0]
    assert first.content == "Cats are clearly superior.\n\nThey need no walking."


def test_parse_does_not_leak_the_next_turn_into_this_one() -> None:
    first = script.parse_turns_markdown(TURNS_MD)[0]
    assert "Dogs are loyal" not in first.content
    assert "## Turn" not in first.content


def test_parse_excludes_the_metadata_block_from_the_content() -> None:
    """The judge must see the argument and nothing else — the assigned stance in
    particular, which is exactly what it must not be told (FR-34)."""
    first = script.parse_turns_markdown(TURNS_MD)[0]
    assert "assigned stance" not in first.content
    assert "speaker" not in first.content


def test_parse_reads_the_real_fixture() -> None:
    turns = script.parse_turns_markdown(
        (FIXTURES / "f10_invasion_turns.md").read_text(encoding="utf-8")
    )
    labels = json.loads((FIXTURES / "f10_hand_labels.json").read_text(encoding="utf-8"))
    assert len(turns) == 32
    assert {t.turn_id for t in turns} == set(labels)
    assert all(t.content.strip() for t in turns)
    assert all(t.topic.startswith("Switzerland") for t in turns)


# --- Scoring -----------------------------------------------------------------

LABELS = {"a": "pro", "b": "con", "c": "con", "d": "pro"}


def test_score_counts_agreement() -> None:
    board = script.score({"a": "pro", "b": "con", "c": "con", "d": "pro"}, LABELS)
    assert (board.agreed, board.total, board.unmeasured) == (4, 4, 0)


def test_score_counts_an_unreadable_verdict_as_unmeasured_not_wrong() -> None:
    """Unmeasured and wrong are different failures with different fixes, and the
    F10 gate sets a separate threshold for each."""
    board = script.score({"a": "pro", "b": None, "c": "con", "d": "pro"}, LABELS)
    assert (board.agreed, board.unmeasured, len(board.wrong)) == (3, 1, 0)


def test_score_records_which_turns_were_wrong_and_how() -> None:
    board = script.score({"a": "con", "b": "con", "c": "con", "d": "pro"}, LABELS)
    assert board.wrong == [("a", "pro", "con")]


def test_score_counts_a_pro_con_flip_as_an_inversion() -> None:
    """The defect this benchmark exists to track: a turn that dismantles the pro
    case being read as pro. A 'neutral' miss is a different, milder failure."""
    board = script.score({"a": "con", "b": "neutral", "c": "con", "d": "pro"}, LABELS)
    assert board.inversions == 1
    assert len(board.wrong) == 2


def test_score_treats_a_turn_missing_from_the_verdicts_as_unmeasured() -> None:
    """A run that died halfway must not score as a run that judged nothing wrong."""
    board = script.score({"a": "pro"}, LABELS)
    assert (board.total, board.agreed, board.unmeasured) == (4, 1, 3)


def test_score_ignores_a_verdict_for_a_turn_that_was_never_hand_read() -> None:
    board = script.score({"a": "pro", "b": "con", "c": "con", "d": "pro", "z": "pro"}, LABELS)
    assert (board.total, board.agreed) == (4, 4)


def test_score_reproduces_the_committed_baseline() -> None:
    """The load-bearing check on this harness. The decision record puts the
    shipped one-word judge at 25 of 32 with 0 unmeasured and all 7 errors pro/con
    inversions; a scorer that does not reproduce those numbers from the committed
    fixtures is wrong, and no arm it scores can be believed."""
    verdicts = json.loads(
        (FIXTURES / "f10_baseline_verdicts.json").read_text(encoding="utf-8")
    )
    labels = json.loads((FIXTURES / "f10_hand_labels.json").read_text(encoding="utf-8"))
    board = script.score(verdicts, labels)
    assert (board.total, board.agreed, board.unmeasured) == (32, 25, 0)
    assert len(board.wrong) == 7
    assert board.inversions == 6  # the seventh is a con read as neutral


# --- Comparing two arms -------------------------------------------------------


def test_compare_splits_fixed_from_broken() -> None:
    """Aggregate agreement can stay flat while an arm fixes three turns and breaks
    three others, which is not the same result at all."""
    baseline = {"a": "con", "b": "con", "c": "con", "d": "pro"}  # 'a' wrong
    treatment = {"a": "pro", "b": "pro", "c": "con", "d": "pro"}  # 'b' wrong instead
    delta = script.compare(baseline, treatment, LABELS)
    assert delta.fixed == ["a"]
    assert delta.broken == ["b"]


def test_compare_reports_a_turn_that_became_unmeasured_as_broken() -> None:
    baseline = {"a": "pro", "b": "con", "c": "con", "d": "pro"}
    treatment = {"a": None, "b": "con", "c": "con", "d": "pro"}
    delta = script.compare(baseline, treatment, LABELS)
    assert delta.broken == ["a"]
    assert delta.fixed == []


def test_compare_ignores_turns_both_arms_got_right() -> None:
    same = {"a": "pro", "b": "con", "c": "con", "d": "pro"}
    delta = script.compare(same, same, LABELS)
    assert delta.fixed == delta.broken == []


@pytest.mark.parametrize("missing", ["baseline", "treatment"])
def test_compare_needs_both_arms_to_cover_the_turn(missing: str) -> None:
    """A turn one arm never judged is not evidence either way about the change."""
    full = {"a": "con", "b": "con", "c": "con", "d": "pro"}
    partial = {k: v for k, v in full.items() if k != "a"}
    baseline, treatment = (partial, full) if missing == "baseline" else (full, partial)
    delta = script.compare(baseline, treatment, LABELS)
    assert "a" not in delta.fixed + delta.broken


# --- The parser, against real model output (offline) --------------------------
# f10_judge_replies.json holds the 32 verbatim qwen3:30b replies. Re-reading them
# with the current parse_stance scores a parser change against hand-read ground
# truth in milliseconds, with no model call. The </think> defect these tests pin
# survived because nothing ever looked at what the judge actually said.

REPLIES = FIXTURES / "f10_judge_replies.json"


def _stored_replies() -> dict[str, dict[str, str | None]]:
    return json.loads(REPLIES.read_text(encoding="utf-8"))


def _labels() -> dict[str, str]:
    return json.loads((FIXTURES / "f10_hand_labels.json").read_text(encoding="utf-8"))


def test_stored_replies_cover_every_hand_read_turn() -> None:
    assert set(_stored_replies()) == set(_labels())


def test_stored_replies_carry_the_narration_that_broke_the_parse() -> None:
    """The fixture is only worth keeping if it still contains the defect's shape:
    a reasoning block closed by a bare ``</think>`` that was never opened."""
    replies = [r["reply"] or "" for r in _stored_replies().values()]
    assert sum("</think>" in r for r in replies) == 32
    assert sum("<think>" in r for r in replies) == 0


def test_current_parser_reads_the_stored_replies_correctly() -> None:
    """The regression bar. Before the unopened-``</think>`` strip this scored 25
    of 32 on these exact replies: the judge reasoned to the right answer, said
    so, and was recorded as its opposite on 6 turns.

    31 rather than 32 because one turn (614d9e5b) is a genuine judge error — a
    pro rebuttal read as con — which no parser can fix.
    """
    board = script.score(script.reparse(_stored_replies()), _labels())
    assert board.unmeasured == 0
    assert board.agreed == 31


def test_reparse_recovers_the_answer_after_the_narration() -> None:
    """Spot-check the mechanism on the turn from the decision record: the reply
    argues its way to 'con' and ends with 'con', and used to score 'pro' because
    the narration quotes the instruction's word list."""
    verdicts = script.reparse(_stored_replies())
    reply = _stored_replies()["10bf5837-4e46-4da4-8313-5103edb2986c"]["reply"] or ""
    assert "pro" in reply.split("</think>")[0]  # the narration mentions it
    assert verdicts["10bf5837-4e46-4da4-8313-5103edb2986c"] == "con"


def test_reparse_reports_an_empty_reply_as_unmeasured() -> None:
    assert script.reparse({"a": {"reply": None, "error": "boom"}}) == {"a": None}
    assert script.reparse({"a": {"reply": "", "error": None}}) == {"a": None}
