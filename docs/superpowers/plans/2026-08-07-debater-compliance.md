# Debater Compliance Measurement (F9) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure, per turn, which side of the motion the prose actually argues, and report when a chamber's winning position was never opposed.

**Architecture:** The chamber's moderator judges each turn's prose — never shown the debater's name or assigned stance — and answers with one word parsed by the existing `parse_stance`. The answer is stored on `Turn.metadata["argued"]` before the turn is appended. All comparison against the assignment happens in pure Python in a new `core/compliance.py`, which `core/outcome.py` reads to derive a caveat and per-debater lines. Nothing consults this signal to make a decision.

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI, pytest, mutmut; React + TypeScript + Vitest on the frontend.

**Spec:** [`docs/superpowers/specs/2026-08-07-debater-compliance-design.md`](../specs/2026-08-07-debater-compliance-design.md)

## Global Constraints

- **Absence of `Turn.metadata["argued"]` means *not measured*, never *compliant*.** Every function that reads it must treat a missing key as unknown, not as agreement.
- **The compliance judge prompt must never contain the debater's name or assigned stance.** It carries the motion and the turn text only.
- **The judged turn text is untrusted model output.** It is wrapped in `prompts.TRANSCRIPT_OPEN` / `prompts.TRANSCRIPT_CLOSE` and the prompt carries `prompts.SAFETY_RULE`.
- **No decision consults this signal.** No change to `decide_outcome`, any `StopReason`, any decision rule, or the stance poll.
- **Derive from turns, never from mutable roster state.** `Turn` is append-only and `Participant.stance` is frozen once a chamber leaves draft; `muted` is not, and must not be an input here.
- **"Polar opposite" means `Stance.PRO` against `Stance.CON` and nothing else.** `Stance.NEUTRAL` is never opposition.
- Backend commands run from `backend/`. Verify with `make lint`, `make type`, `make test`. Frontend from `frontend/`: `npm run typecheck`, `npm run lint`, `npm test`.
- **Never run bare `make mutation`** (all gated modules, full suite per mutant). Scope it: `mutmut run --paths-to-mutate cicero/core/compliance.py`. The local `.venv` is Python 3.14 where mutmut crashes — use a Python 3.11 venv. Check `git status` afterwards for stray `.bak` files or a source file left carrying a mutant.

---

## File Structure

**Created:**
- `backend/cicero/core/compliance.py` — the pure readers, `ComplianceRecord`, and `ComplianceJudge`
- `backend/tests/test_compliance.py` — unit tests for the above
- `docs/model-selection.md` — the guidance page (part two of the spec)

**Modified:**
- `backend/cicero/domain/models.py` — `DebateSettings.measure_compliance`
- `backend/cicero/core/prompts.py` — `COMPLIANCE_SYSTEM`, `COMPLIANCE_USER_INSTRUCTION`
- `backend/cicero/core/prompt_builder.py` — `build_compliance_messages`
- `backend/cicero/core/orchestrator.py` — `DebateEngine.__init__` gains `judge`; `_run_round` calls it
- `backend/cicero/api/routers/chambers.py` — `_build_engine` constructs the judge; `get_outcome` returns two new fields
- `backend/cicero/providers/mock.py` — answers the compliance question offline
- `backend/cicero/core/outcome.py` — `OutcomeSummary` gains two fields
- `backend/cicero/core/export.py` — Markdown renders them
- `frontend/src/types.ts`, `frontend/src/ChamberDetail.tsx` — display
- `docs/testing.md`, `docs/requirements.md`, `docs/backlog.md`, `docs/architecture.md`, `README.md` — docs
- `backend/scripts/check_compliance_judge.py` — the hand-check harness (Task 10)

---

### Task 1: The setting

**Files:**
- Modify: `backend/cicero/domain/models.py:85-115` (`DebateSettings`)
- Test: `backend/tests/test_domain_models.py`

**Interfaces:**
- Produces: `DebateSettings.measure_compliance: bool` — defaults `True`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_domain_models.py`:

```python
def test_compliance_measurement_is_on_by_default() -> None:
    assert DebateSettings().measure_compliance is True


def test_compliance_measurement_can_be_disabled() -> None:
    assert DebateSettings(measure_compliance=False).measure_compliance is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_domain_models.py -k compliance -v`
Expected: FAIL — `ValidationError: Extra inputs are not permitted` (the model sets `extra="forbid"`).

- [ ] **Step 3: Write minimal implementation**

In `DebateSettings`, after `repetition_threshold`:

```python
    #: Judge each turn for which side it actually argues (FR-34). One extra
    #: model call per turn — 24 on an 8-round debate with three debaters — so
    #: this is a real cost, not a free measurement. Off means no calls, no
    #: metadata, and a silent caveat.
    measure_compliance: bool = True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_domain_models.py -k compliance -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/cicero/domain/models.py backend/tests/test_domain_models.py
git commit -m "feat: add the measure_compliance setting (F9)"
```

---

### Task 2: The pure readers

**Files:**
- Create: `backend/cicero/core/compliance.py`
- Test: `backend/tests/test_compliance.py`

**Interfaces:**
- Consumes: `DebateSettings.measure_compliance` (Task 1) — not referenced here, but this module is what it gates.
- Produces:
  - `ARGUED_KEY: str` = `"argued"`
  - `argued_stance(turn: Turn) -> Stance | None`
  - `ComplianceRecord` — frozen dataclass with `assigned: Stance`, `judged: int`, `held: int`, `argued_against: int`
  - `debater_compliance(chamber: Chamber, participant: Participant) -> ComplianceRecord`

Note for the implementer: `Turn.metadata` is `dict[str, object]`, so every read needs a type-narrowing check before it is handed to `Stance(...)`. `Stance` is a `StrEnum` in `cicero/domain/enums.py` with values `"pro"`, `"con"`, `"neutral"`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_compliance.py`:

```python
from uuid import uuid4

import pytest

from cicero.core.compliance import (
    ARGUED_KEY,
    ComplianceRecord,
    argued_stance,
    debater_compliance,
)
from cicero.domain.enums import ProviderType, Stance
from cicero.domain.models import Chamber, Participant, Turn


def _debater(stance: Stance, name: str = "Bob") -> Participant:
    return Participant(
        display_name=name,
        provider=ProviderType.MOCK,
        model="mock-small",
        stance=stance,
    )


def _turn(participant: Participant, argued: str | None, round_index: int = 0) -> Turn:
    metadata: dict[str, object] = {} if argued is None else {ARGUED_KEY: argued}
    return Turn(
        participant_id=participant.id,
        round_index=round_index,
        content="an argument",
        metadata=metadata,
    )


def test_argued_stance_reads_a_recorded_judgement() -> None:
    bob = _debater(Stance.CON)
    assert argued_stance(_turn(bob, "pro")) is Stance.PRO


def test_argued_stance_is_none_when_the_key_is_absent() -> None:
    bob = _debater(Stance.CON)
    assert argued_stance(_turn(bob, None)) is None


@pytest.mark.parametrize("value", ["", "sideways", "PRO ", 3, None, True])
def test_argued_stance_is_none_for_a_value_that_is_not_a_stance(value: object) -> None:
    """Metadata is dict[str, object] and survives a round trip through JSON, so
    anything at all can be sitting under the key. None of it may be read as a
    stance, and none of it may raise."""
    bob = _debater(Stance.CON)
    turn = _turn(bob, None)
    turn.metadata[ARGUED_KEY] = value
    assert argued_stance(turn) is None


def test_compliance_counts_turns_that_held_and_abandoned_the_side() -> None:
    bob = _debater(Stance.CON)
    chamber = Chamber(topic="a motion", participants=[bob])
    chamber.turns = [
        _turn(bob, "con", 0),
        _turn(bob, "pro", 1),
        _turn(bob, "pro", 2),
    ]
    assert debater_compliance(chamber, bob) == ComplianceRecord(
        assigned=Stance.CON, judged=3, held=1, argued_against=2
    )


def test_compliance_ignores_unjudged_turns_entirely() -> None:
    """An unjudged turn is not evidence of anything — it must not count as held,
    and it must not count as argued_against."""
    bob = _debater(Stance.CON)
    chamber = Chamber(topic="a motion", participants=[bob])
    chamber.turns = [_turn(bob, "con", 0), _turn(bob, None, 1)]
    assert debater_compliance(chamber, bob) == ComplianceRecord(
        assigned=Stance.CON, judged=1, held=1, argued_against=0
    )


def test_a_neutral_turn_is_neither_held_nor_against_for_a_con_debater() -> None:
    """neutral is not the polar opposite of con, and it is not con either."""
    bob = _debater(Stance.CON)
    chamber = Chamber(topic="a motion", participants=[bob])
    chamber.turns = [_turn(bob, "neutral", 0)]
    assert debater_compliance(chamber, bob) == ComplianceRecord(
        assigned=Stance.CON, judged=1, held=0, argued_against=0
    )


def test_compliance_counts_only_the_named_debaters_turns() -> None:
    bob = _debater(Stance.CON, "Bob")
    ada = _debater(Stance.PRO, "Ada")
    chamber = Chamber(topic="a motion", participants=[bob, ada])
    chamber.turns = [_turn(bob, "con", 0), _turn(ada, "pro", 0)]
    assert debater_compliance(chamber, bob).judged == 1


def test_a_system_turn_belongs_to_no_debater() -> None:
    """Moderator notes and injected evidence have participant_id None; they must
    not be attributed to anyone."""
    bob = _debater(Stance.CON)
    chamber = Chamber(topic="a motion", participants=[bob])
    system_turn = Turn(round_index=0, content="a note", metadata={ARGUED_KEY: "pro"})
    chamber.turns = [system_turn]
    assert debater_compliance(chamber, bob).judged == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_compliance.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cicero.core.compliance'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/cicero/core/compliance.py`:

```python
"""Did each debater argue the side it was assigned? (FR-34)

Read from the *prose* of each turn, not from the stance poll. The poll asks a
debater to self-report and is independently known to be unreliable; the finding
this module exists to surface — five of twelve con-assigned opening turns arguing
the pro case — was only recoverable by reading what was actually written. See
``docs/superpowers/decisions/2026-08-06-debaters-do-not-hold-assigned-sides.md``.

Everything here derives from ``chamber.turns``, which is append-only, and from
``Participant.stance``, which is frozen once a chamber leaves draft. Nothing reads
mutable roster state — ``muted`` in particular — so a change made mid-debate cannot
retroactively rewrite what this reports.
"""

from __future__ import annotations

from dataclasses import dataclass

from cicero.domain.enums import Stance
from cicero.domain.models import Chamber, Participant, Turn

#: Where a turn's judged side is recorded. Absent means **not measured** — never
#: "compliant". A judge call that failed, a reply that did not parse and a turn
#: that was never judged are indistinguishable here, and all three are silence.
ARGUED_KEY = "argued"

#: Which stance opposes which. ``NEUTRAL`` is deliberately absent: a neutral
#: debater was never asked to oppose anything, so its silence on the losing side
#: is not evidence about the debate.
_OPPOSITE: dict[Stance, Stance] = {Stance.PRO: Stance.CON, Stance.CON: Stance.PRO}


def opposite_of(stance: Stance) -> Stance | None:
    """The polar opposite of ``stance``, or ``None`` for ``NEUTRAL``."""
    return _OPPOSITE.get(stance)


def argued_stance(turn: Turn) -> Stance | None:
    """The side ``turn`` was judged to argue, or ``None`` if it was not judged.

    ``Turn.metadata`` is ``dict[str, object]`` and round-trips through JSON, so the
    value under the key is arbitrary. Anything that is not exactly a stance word
    reads as unmeasured rather than raising: a malformed record must not be able to
    crash a debate, and must not be guessed at either.
    """
    value = turn.metadata.get(ARGUED_KEY)
    if not isinstance(value, str):
        return None
    try:
        return Stance(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class ComplianceRecord:
    """How one debater's judged turns compare with the side it was assigned."""

    assigned: Stance
    #: Turns by this debater that were judged at all.
    judged: int
    #: Judged turns that argued the assigned side.
    held: int
    #: Judged turns that argued its polar opposite. ``judged - held -
    #: argued_against`` is the turns that argued neither.
    argued_against: int


def debater_compliance(chamber: Chamber, participant: Participant) -> ComplianceRecord:
    """Count ``participant``'s judged turns against its assigned stance."""
    against = opposite_of(participant.stance)
    judged = held = argued_against = 0
    for turn in chamber.turns:
        if turn.participant_id != participant.id:
            continue
        argued = argued_stance(turn)
        if argued is None:
            continue
        judged += 1
        if argued is participant.stance:
            held += 1
        elif against is not None and argued is against:
            argued_against += 1
    return ComplianceRecord(
        assigned=participant.stance,
        judged=judged,
        held=held,
        argued_against=argued_against,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_compliance.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/cicero/core/compliance.py backend/tests/test_compliance.py
git commit -m "feat: read each turn's judged side and count it against the assignment (F9)"
```

---

### Task 3: The caveat rule

**Files:**
- Modify: `backend/cicero/core/compliance.py`
- Test: `backend/tests/test_compliance.py`

**Interfaces:**
- Consumes: `argued_stance`, `debater_compliance`, `opposite_of`, `ComplianceRecord` (Task 2)
- Produces:
  - `UNOPPOSED_CAVEAT: str`
  - `compliance_caveat(chamber: Chamber) -> str` — empty string when it does not fire
  - `noncompliance_lines(chamber: Chamber) -> tuple[str, ...]`

**This is the task the spec calls out as where the bugs will be.** All four conditions must hold for the caveat to fire, and condition 3 is narrower than it looks — see the test named `...when_the_opposing_debater_was_never_judged`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_compliance.py`:

```python
from cicero.core.compliance import (
    UNOPPOSED_CAVEAT,
    compliance_caveat,
    noncompliance_lines,
)
from cicero.domain.enums import ConsensusOutcome
from cicero.domain.models import ConsensusResult


def _concluded(
    participants: list[Participant],
    turns: list[Turn],
    winner: Stance | None,
    outcome: ConsensusOutcome = ConsensusOutcome.CONSENSUS,
) -> Chamber:
    chamber = Chamber(topic="a motion", participants=participants)
    chamber.turns = turns
    chamber.consensus = ConsensusResult(
        outcome=outcome,
        statement="the chamber said something",
        winning_stance=winner,
        final_stances={str(p.id): p.stance for p in participants},
    )
    return chamber


def test_caveat_fires_when_the_losing_side_was_never_argued() -> None:
    ada, bob = _debater(Stance.PRO, "Ada"), _debater(Stance.CON, "Bob")
    chamber = _concluded(
        [ada, bob],
        [_turn(ada, "pro", 0), _turn(bob, "pro", 0)],
        winner=Stance.PRO,
    )
    assert compliance_caveat(chamber) == UNOPPOSED_CAVEAT


def test_no_caveat_when_the_losing_side_was_argued() -> None:
    ada, bob = _debater(Stance.PRO, "Ada"), _debater(Stance.CON, "Bob")
    chamber = _concluded(
        [ada, bob],
        [_turn(ada, "pro", 0), _turn(bob, "con", 0)],
        winner=Stance.PRO,
    )
    assert compliance_caveat(chamber) == ""


def test_no_caveat_when_the_opposing_debater_was_never_judged() -> None:
    """Condition 3, and the one a loose implementation gets wrong.

    Bob is the only con-assigned debater and none of his turns were judged, while
    every pro turn was. An implementation that asks only "was anything judged?"
    fires the caveat here — publishing a finding the debate never measured. The
    other side was not absent; it was unmeasured."""
    ada, bob = _debater(Stance.PRO, "Ada"), _debater(Stance.CON, "Bob")
    chamber = _concluded(
        [ada, bob],
        [_turn(ada, "pro", 0), _turn(bob, None, 0)],
        winner=Stance.PRO,
    )
    assert compliance_caveat(chamber) == ""


def test_no_caveat_when_nothing_at_all_was_judged() -> None:
    ada, bob = _debater(Stance.PRO, "Ada"), _debater(Stance.CON, "Bob")
    chamber = _concluded(
        [ada, bob],
        [_turn(ada, None, 0), _turn(bob, None, 0)],
        winner=Stance.PRO,
    )
    assert compliance_caveat(chamber) == ""


def test_no_caveat_when_nobody_was_assigned_the_opposing_side() -> None:
    """Two pro debaters agreeing is not a suppressed opposition — there was none."""
    ada, eve = _debater(Stance.PRO, "Ada"), _debater(Stance.PRO, "Eve")
    chamber = _concluded(
        [ada, eve],
        [_turn(ada, "pro", 0), _turn(eve, "pro", 0)],
        winner=Stance.PRO,
    )
    assert compliance_caveat(chamber) == ""


def test_no_caveat_for_a_neutral_winner() -> None:
    """NEUTRAL has no polar opposite, so 'the other side' names nothing."""
    ada, bob = _debater(Stance.PRO, "Ada"), _debater(Stance.CON, "Bob")
    chamber = _concluded(
        [ada, bob],
        [_turn(ada, "neutral", 0), _turn(bob, "neutral", 0)],
        winner=Stance.NEUTRAL,
    )
    assert compliance_caveat(chamber) == ""


def test_no_caveat_without_a_winner() -> None:
    ada, bob = _debater(Stance.PRO, "Ada"), _debater(Stance.CON, "Bob")
    chamber = _concluded(
        [ada, bob],
        [_turn(ada, "pro", 0), _turn(bob, "pro", 0)],
        winner=None,
        outcome=ConsensusOutcome.DISAGREEMENT,
    )
    assert compliance_caveat(chamber) == ""


def test_no_caveat_before_the_debate_concludes() -> None:
    ada, bob = _debater(Stance.PRO, "Ada"), _debater(Stance.CON, "Bob")
    chamber = Chamber(topic="a motion", participants=[ada, bob])
    chamber.turns = [_turn(ada, "pro", 0), _turn(bob, "pro", 0)]
    assert compliance_caveat(chamber) == ""


def test_the_opposite_side_counts_from_any_debater() -> None:
    """The claim is about the chamber, not about one debater: if anyone argued
    con, the chamber heard con, whoever was assigned it."""
    ada, bob = _debater(Stance.PRO, "Ada"), _debater(Stance.CON, "Bob")
    chamber = _concluded(
        [ada, bob],
        [_turn(ada, "con", 0), _turn(bob, "pro", 0)],
        winner=Stance.PRO,
    )
    assert compliance_caveat(chamber) == ""


def test_noncompliance_names_a_debater_that_abandoned_its_side() -> None:
    bob = _debater(Stance.CON, "Bob")
    chamber = _concluded(
        [bob], [_turn(bob, "pro", 0), _turn(bob, "pro", 1), _turn(bob, "pro", 2)],
        winner=Stance.PRO,
    )
    assert noncompliance_lines(chamber) == (
        "Bob (assigned con) argued pro in 3 of 3 judged turns",
    )


def test_noncompliance_is_silent_for_a_debater_that_held_its_side() -> None:
    bob = _debater(Stance.CON, "Bob")
    chamber = _concluded([bob], [_turn(bob, "con", 0)], winner=Stance.CON)
    assert noncompliance_lines(chamber) == ()


def test_noncompliance_reports_a_partial_count() -> None:
    """Held it, then conceded. That is the truth-seeking clause working, and the
    count says so rather than flattening it to non-compliance."""
    bob = _debater(Stance.CON, "Bob")
    chamber = _concluded(
        [bob], [_turn(bob, "con", 0), _turn(bob, "pro", 1)], winner=Stance.PRO
    )
    assert noncompliance_lines(chamber) == (
        "Bob (assigned con) argued pro in 1 of 2 judged turns",
    )


def test_noncompliance_never_reports_a_neutral_assigned_debater() -> None:
    """STANCE_INSTRUCTION[NEUTRAL] tells it to follow the evidence, so a neutral
    debater arguing pro is the instruction being obeyed, not broken."""
    eve = _debater(Stance.NEUTRAL, "Eve")
    chamber = _concluded([eve], [_turn(eve, "pro", 0)], winner=Stance.PRO)
    assert noncompliance_lines(chamber) == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_compliance.py -v`
Expected: FAIL — `ImportError: cannot import name 'UNOPPOSED_CAVEAT'`

- [ ] **Step 3: Write minimal implementation**

Append to `backend/cicero/core/compliance.py`:

```python
#: Shown when the chamber's winning position was never argued against by anyone,
#: while at least one debater was assigned to argue against it — and that debater
#: was actually measured. Deliberately narrow: it reports what the transcript
#: does not contain, which is a weaker and more defensible claim than saying a
#: debater failed.
UNOPPOSED_CAVEAT = (
    "No debater was judged to argue against the winning position, though one was "
    "assigned to. This outcome records agreement that was never contested — read "
    "the transcript before treating it as convergence."
)


def _judged_sides(chamber: Chamber) -> set[Stance]:
    """Every side any judged turn in the chamber was found to argue."""
    return {
        side for turn in chamber.turns if (side := argued_stance(turn)) is not None
    }


def compliance_caveat(chamber: Chamber) -> str:
    """Whether this chamber's outcome went uncontested (see ``UNOPPOSED_CAVEAT``).

    Fires only when all four hold:

    1. the outcome names a ``PRO`` or ``CON`` winner — ``NEUTRAL`` has no polar
       opposite and a disagreement has no winner at all;
    2. some debater was *assigned* that opposite;
    3. at least one turn **by such a debater** was judged;
    4. no judged turn in the chamber argued that opposite.

    Condition 3 is narrower than "anything was judged" on purpose. If the only
    con-assigned debater's turns all failed to judge while the pro turns
    succeeded, the loose version fires and reports an absence that was really a
    gap in measurement.
    """
    consensus = chamber.consensus
    if consensus is None or consensus.winning_stance is None:
        return ""
    opposite = opposite_of(consensus.winning_stance)
    if opposite is None:
        return ""
    opposing = [p for p in chamber.participants if p.stance is opposite]
    if not opposing:
        return ""
    if not any(debater_compliance(chamber, p).judged for p in opposing):
        return ""
    if opposite in _judged_sides(chamber):
        return ""
    return UNOPPOSED_CAVEAT


def noncompliance_lines(chamber: Chamber) -> tuple[str, ...]:
    """One line per debater that argued against its assigned side at least once.

    Only ``PRO``- and ``CON``-assigned debaters can appear: a neutral debater is
    told to follow the evidence, so it has no side to abandon.
    """
    lines: list[str] = []
    for participant in chamber.participants:
        against = opposite_of(participant.stance)
        if against is None:
            continue
        record = debater_compliance(chamber, participant)
        if record.argued_against == 0:
            continue
        lines.append(
            f"{participant.display_name} (assigned {record.assigned.value}) "
            f"argued {against.value} in {record.argued_against} of "
            f"{record.judged} judged turns"
        )
    return tuple(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_compliance.py -v`
Expected: PASS (22 tests)

- [ ] **Step 5: Run the mutation gate on this module**

Run (from a Python 3.11 venv, per Global Constraints):
```bash
cd backend && mutmut run --paths-to-mutate cicero/core/compliance.py
mutmut results
```
Expected: every mutant killed. Any survivor is either a missing test — write it — or genuinely equivalent, in which case say which mutant and why in the commit message. Afterwards run `git status` and confirm no `.bak` file and no mutated source was left behind.

- [ ] **Step 6: Commit**

```bash
git add backend/cicero/core/compliance.py backend/tests/test_compliance.py
git commit -m "feat: derive the unopposed-outcome caveat and per-debater lines (F9)"
```

---

### Task 4: The prompt

**Files:**
- Modify: `backend/cicero/core/prompts.py` (append after the moderator fragments, near line 154)
- Modify: `backend/cicero/core/prompt_builder.py` (append after `build_stance_poll_messages`, line 195)
- Test: `backend/tests/test_prompt_builder.py`

**Interfaces:**
- Produces: `prompts.COMPLIANCE_SYSTEM`, `prompts.COMPLIANCE_USER_INSTRUCTION`, and `build_compliance_messages(topic: str, content: str) -> list[Message]`

It takes `topic: str`, **not** a `Chamber` and **not** a `Participant`. That signature is the enforcement mechanism for the spec's central constraint: a function that never receives the debater cannot leak the debater's assignment into the prompt.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_prompt_builder.py`:

```python
from cicero.core.prompt_builder import build_compliance_messages


def test_compliance_prompt_carries_the_motion_and_the_turn() -> None:
    messages = build_compliance_messages("should we teach programming", "an argument")
    user = messages[-1].content
    assert "should we teach programming" in user
    assert "an argument" in user


def test_compliance_prompt_wraps_the_turn_in_transcript_markers() -> None:
    """The judged turn is model output being fed back into a model."""
    messages = build_compliance_messages("a motion", "an argument")
    user = messages[-1].content
    opened = user.index(prompts.TRANSCRIPT_OPEN)
    closed = user.index(prompts.TRANSCRIPT_CLOSE)
    assert opened < user.index("an argument") < closed


def test_compliance_prompt_carries_the_safety_rule() -> None:
    messages = build_compliance_messages("a motion", "an argument")
    assert prompts.SAFETY_RULE in messages[0].content


def test_compliance_prompt_asks_for_one_word() -> None:
    messages = build_compliance_messages("a motion", "an argument")
    assert prompts.COMPLIANCE_USER_INSTRUCTION in messages[-1].content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_prompt_builder.py -k compliance -v`
Expected: FAIL — `ImportError: cannot import name 'build_compliance_messages'`

- [ ] **Step 3: Write minimal implementation**

In `backend/cicero/core/prompts.py`, after `EMPTY_MODERATOR_STATEMENT`:

```python
# Compliance-judge fragments (FR-34).
#: The judge is told nothing about who wrote the turn or what they were assigned.
#: That omission is the design: the decision record measured that assigned-stance
#: labels in a prompt *change* a model's answer, so a judge that knows the expected
#: answer is a judge that can be anchored to it. The comparison happens in Python.
COMPLIANCE_SYSTEM = (
    "You are an impartial reader. You will be shown one argument from a debate. "
    "Report which side of the motion that argument supports, judging only what it "
    "actually says. " + SAFETY_RULE
)
COMPLIANCE_USER_INSTRUCTION = (
    "Reply with exactly one word — pro, con, or neutral. Use 'pro' if the argument "
    "supports the motion, 'con' if it argues against the motion, and 'neutral' if it "
    "takes no side or argues for neither. Judge only the argument above, not what a "
    "debater might be expected to say. Reply with only that word."
)
COMPLIANCE_MOTION_LABEL = "The motion under debate is: {topic}"
```

In `backend/cicero/core/prompt_builder.py`, after `build_stance_poll_messages`:

```python
def build_compliance_messages(topic: str, content: str) -> list[Message]:
    """Ask a judge which side one turn argues (FR-34).

    Takes the motion text and the turn text — deliberately not a ``Chamber`` and
    not a ``Participant``. The judge must not learn who wrote the turn or what
    stance they were assigned, and a signature that cannot receive those is a
    stronger guarantee than remembering not to pass them.

    ``content`` is untrusted: it is another model's output, so it goes inside the
    transcript markers that ``SAFETY_RULE`` (in ``COMPLIANCE_SYSTEM``) tells the
    judge to treat as data.
    """
    user = (
        f"{prompts.COMPLIANCE_MOTION_LABEL.format(topic=topic)}\n\n"
        f"{prompts.TRANSCRIPT_OPEN}\n{content}\n{prompts.TRANSCRIPT_CLOSE}\n\n"
        f"{prompts.COMPLIANCE_USER_INSTRUCTION}"
    )
    return [
        Message(role=Role.SYSTEM, content=prompts.COMPLIANCE_SYSTEM),
        Message(role=Role.USER, content=user),
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_prompt_builder.py -k compliance -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/cicero/core/prompts.py backend/cicero/core/prompt_builder.py backend/tests/test_prompt_builder.py
git commit -m "feat: add the compliance-judge prompt, blind to the assignment (F9)"
```

---

### Task 5: The judge

**Files:**
- Modify: `backend/cicero/core/compliance.py`
- Test: `backend/tests/test_compliance.py`

**Interfaces:**
- Consumes: `build_compliance_messages` (Task 4); `parse_stance` from `cicero.core.consensus`; `Provider`, `GenerateOptions`, `ProviderError` from `cicero.providers.base`
- Produces:
  - `COMPLIANCE_MAX_TOKENS: int` = `512`
  - `ComplianceJudge(provider: Provider, model: str)` with `async def judge(self, topic: str, content: str) -> Stance | None`

Import note: `cicero.core.consensus` imports from `cicero.core.prompt_builder`, and this module will import from `cicero.core.consensus`. Nothing in `consensus.py` imports `compliance.py`, so there is no cycle. Do not add one.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_compliance.py`:

```python
from cicero.core.compliance import COMPLIANCE_MAX_TOKENS, ComplianceJudge
from cicero.providers.mock import MockProvider


async def test_judge_reads_a_one_word_answer() -> None:
    judge = ComplianceJudge(MockProvider(scripted=["con"]), "mock-small")
    assert await judge.judge("a motion", "an argument") is Stance.CON


async def test_judge_returns_none_for_an_unreadable_answer() -> None:
    judge = ComplianceJudge(MockProvider(scripted=["it depends, really"]), "mock-small")
    assert await judge.judge("a motion", "an argument") is None


async def test_judge_returns_none_when_the_provider_fails() -> None:
    """A failing judge must not crash a debate (NFR-R-1)."""
    judge = ComplianceJudge(MockProvider(fail_after=1), "mock-small")
    assert await judge.judge("a motion", "an argument") is None


async def test_judge_suppresses_reasoning_and_pins_temperature() -> None:
    """A thinking model can spend its whole budget reasoning and return empty
    content — measured on qwen3 with the one-word stance poll."""
    captured: list[GenerateOptions] = []

    class Recording(MockProvider):
        async def generate(
            self, messages: list[Message], options: GenerateOptions
        ) -> GenerateResult:
            captured.append(options)
            return await super().generate(messages, options)

    judge = ComplianceJudge(Recording(scripted=["pro"]), "mock-small")
    await judge.judge("a motion", "an argument")
    assert captured[0].allow_reasoning is False
    assert captured[0].temperature == 0.0
    assert captured[0].max_tokens == COMPLIANCE_MAX_TOKENS
    assert captured[0].model == "mock-small"


async def test_judge_is_not_told_who_wrote_the_turn() -> None:
    """The load-bearing constraint: nothing identifying the debater or its
    assigned stance may reach the judge."""
    captured: list[list[Message]] = []

    class Recording(MockProvider):
        async def generate(
            self, messages: list[Message], options: GenerateOptions
        ) -> GenerateResult:
            captured.append(messages)
            return await super().generate(messages, options)

    judge = ComplianceJudge(Recording(scripted=["pro"]), "mock-small")
    await judge.judge("a motion", "Bartholomew thinks so too")
    prompt = " ".join(m.content for m in captured[0])
    assert "assigned" not in prompt.lower()
    # The only occurrence of a name is the one inside the judged text itself.
    assert prompt.count("Bartholomew") == 1
```

Add these imports at the top of the test file:

```python
from cicero.providers.base import GenerateOptions, GenerateResult, Message
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_compliance.py -k judge -v`
Expected: FAIL — `ImportError: cannot import name 'COMPLIANCE_MAX_TOKENS'`

- [ ] **Step 3: Write minimal implementation**

Append to `backend/cicero/core/compliance.py`, with the imports at the top of the file:

```python
from cicero.core.consensus import parse_stance
from cicero.core.prompt_builder import build_compliance_messages
from cicero.providers.base import GenerateOptions, Provider, ProviderError
```

```python
#: Matches ``_POLL_MAX_TOKENS``. Generous for a one-word reply, and the poll's
#: measured value for the same shape of task; diverging would buy nothing.
COMPLIANCE_MAX_TOKENS = 512


class ComplianceJudge:
    """Reads one turn and names the side it argues.

    Holds the moderator's provider — the chamber's designated impartial party —
    but calls it with its own options: a one-word answer wants no reasoning, no
    temperature and a small budget, exactly as ``poll_stances`` does.
    """

    def __init__(self, provider: Provider, model: str) -> None:
        self._provider = provider
        self._options = GenerateOptions(
            model=model,
            max_tokens=COMPLIANCE_MAX_TOKENS,
            temperature=0.0,
            allow_reasoning=False,
        )

    async def judge(self, topic: str, content: str) -> Stance | None:
        """The side ``content`` argues, or ``None`` if it could not be read.

        Both failure modes collapse to ``None`` on purpose: a provider error and
        an unparseable reply are equally "not measured", and the caller records
        the absence rather than guessing.
        """
        messages = build_compliance_messages(topic, content)
        try:
            result = await self._provider.generate(messages, self._options)
        except ProviderError:
            return None
        return parse_stance(result.content)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_compliance.py -v`
Expected: PASS (27 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/cicero/core/compliance.py backend/tests/test_compliance.py
git commit -m "feat: add the compliance judge (F9)"
```

---

### Task 6: Wire the judge into the debate loop

**Files:**
- Modify: `backend/cicero/core/orchestrator.py:161-179` (`DebateEngine.__init__`) and `:412-443` (inside `_run_round`)
- Modify: `backend/cicero/api/routers/chambers.py:134-167` (`_build_engine`)
- Test: `backend/tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `ComplianceJudge` (Task 5), `ARGUED_KEY` (Task 2), `DebateSettings.measure_compliance` (Task 1)
- Produces: `DebateEngine(..., judge: ComplianceJudge | None = None)`; turns carry `metadata[ARGUED_KEY]` when judged.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_orchestrator.py`:

First extend the file's existing `_engine` helper (line 85) to accept an optional judge, leaving every existing caller unchanged:

```python
def _engine(
    factory: StubFactory,
    repo: InMemoryChamberRepository,
    judge: ComplianceJudge | None = None,
) -> DebateEngine:
    consensus = ConsensusEngine(factory, ScriptedProvider(moderator_reply="STATEMENT."), MOD_OPTS)
    return DebateEngine(factory, repo, consensus, judge=judge)
```

Then add the tests. Note the house style this file already uses: no `@pytest.mark.asyncio` (`asyncio_mode = "auto"` in `pyproject.toml:53`), and participants built with `make_participant` / `make_chamber`.

```python
from cicero.core.compliance import ARGUED_KEY, ComplianceJudge
from cicero.providers.mock import MockProvider


def _judged_chamber() -> tuple[Chamber, StubFactory, InMemoryChamberRepository]:
    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    factory = StubFactory(
        {a.id: ScriptedProvider(stance_word="pro"), b.id: ScriptedProvider(stance_word="pro")}
    )
    return chamber, factory, InMemoryChamberRepository()


async def test_turns_record_the_side_they_were_judged_to_argue() -> None:
    chamber, factory, repo = _judged_chamber()
    judge = ComplianceJudge(MockProvider(scripted=["con"] * 50), "mock-small")
    result = await _engine(factory, repo, judge).run(
        chamber, DebateBudget(max_rounds=1, max_total_tokens=100_000)
    )
    debate_turns = [t for t in result.turns if t.participant_id is not None]
    assert debate_turns
    assert all(t.metadata[ARGUED_KEY] == "con" for t in debate_turns)


async def test_no_judgement_is_recorded_when_the_setting_is_off() -> None:
    chamber, factory, repo = _judged_chamber()
    chamber.settings.measure_compliance = False
    judge = ComplianceJudge(MockProvider(scripted=["con"] * 50), "mock-small")
    result = await _engine(factory, repo, judge).run(
        chamber, DebateBudget(max_rounds=1, max_total_tokens=100_000)
    )
    assert all(ARGUED_KEY not in t.metadata for t in result.turns)


async def test_a_failing_judge_leaves_the_key_absent_and_the_debate_running() -> None:
    """NFR-R-1: one failing judge call must not crash a debate, and must not be
    recorded as agreement."""
    chamber, factory, repo = _judged_chamber()
    judge = ComplianceJudge(MockProvider(fail_after=1), "mock-small")
    result = await _engine(factory, repo, judge).run(
        chamber, DebateBudget(max_rounds=1, max_total_tokens=100_000)
    )
    assert result.status is ChamberStatus.CONCLUDED
    debate_turns = [t for t in result.turns if t.participant_id is not None]
    assert debate_turns
    assert all(ARGUED_KEY not in t.metadata for t in debate_turns)


async def test_an_errored_turn_is_not_sent_to_the_judge() -> None:
    """An empty turn has no prose to read, so judging it would spend a call to
    learn nothing."""
    judged: list[str] = []

    class Counting(MockProvider):
        async def generate(
            self, messages: list[Message], options: GenerateOptions
        ) -> GenerateResult:
            judged.append(messages[-1].content)
            return await super().generate(messages, options)

    a = make_participant("A", Stance.PRO)
    b = make_participant("B", Stance.CON)
    chamber = make_chamber(a, b)
    # A debater whose own provider fails produces an empty turn.
    factory = StubFactory(
        {a.id: ScriptedProvider(fail=True), b.id: ScriptedProvider(fail=True)}
    )
    judge = ComplianceJudge(Counting(scripted=["pro"] * 50), "mock-small")
    await _engine(factory, InMemoryChamberRepository(), judge).run(
        chamber, DebateBudget(max_rounds=1, max_total_tokens=100_000)
    )
    assert judged == []
```

Check `ScriptedProvider`'s constructor in this file for how it is made to fail — if it has no failure flag, use `MockProvider(fail_after=1)` for the debaters instead, which does.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_orchestrator.py -k judged -v`
Expected: FAIL — `TypeError: DebateEngine.__init__() got an unexpected keyword argument 'judge'`

- [ ] **Step 3: Write minimal implementation**

In `DebateEngine.__init__`, add the parameter after `mutes` and store it:

```python
        mutes: MuteSource | None = None,
        judge: ComplianceJudge | None = None,
    ) -> None:
        ...
        self._mutes = mutes
        self._judge = judge
```

In `_run_round`, beside the existing `gatherer` line near the top:

```python
        judge = self._judge if chamber.settings.measure_compliance else None
```

In the `try` block, after the `if is_repeat(...)` block and before `tracker.add_tokens(...)`:

```python
                # Judged before the turn is appended, so the verdict is present the
                # first time the turn reaches the SSE stream — no update event, and
                # no re-persisting a turn a reader has already seen. An empty turn
                # has no prose to read.
                if judge is not None and content:
                    argued = await judge.judge(chamber.topic, content)
                    if argued is not None:
                        metadata[ARGUED_KEY] = argued.value
```

Add the import at the top of `orchestrator.py`:

```python
from cicero.core.compliance import ARGUED_KEY, ComplianceJudge
```

In `backend/cicero/api/routers/chambers.py`, in `_build_engine`, after `consensus = ConsensusEngine(...)`:

```python
    # The moderator judges compliance too: it is the chamber's designated
    # impartial party, and ComplianceJudge supplies its own options because a
    # one-word answer wants none of the moderator's synthesis budget.
    judge = ComplianceJudge(moderator, moderator_config.model)
```

and pass `judge=judge` to the `DebateEngine(...)` call. Add the import:

```python
from cicero.core.compliance import ComplianceJudge
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_orchestrator.py -v && make lint && make type`
Expected: PASS, clean lint and mypy.

- [ ] **Step 5: Commit**

```bash
git add backend/cicero/core/orchestrator.py backend/cicero/api/routers/chambers.py backend/tests/test_orchestrator.py
git commit -m "feat: judge each turn as it is taken (F9)"
```

---

### Task 7: Keep the offline path offline

**Files:**
- Modify: `backend/cicero/providers/mock.py:24-45, 88-101`
- Test: `backend/tests/test_mock_provider.py`

**Interfaces:**
- Produces: `mock.COMPLIANCE_MARKER: str`; `MockProvider(compliance_answer=...)`

Without this, `MockProvider` answers the compliance prompt with its `[mock:...] response to: ...` fallback, which `parse_stance` cannot read — so every offline debate records no compliance at all and `make demo` silently stops covering the feature. F5 hit exactly this with `HEADLINE:` and solved it the same way.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_mock_provider.py`:

```python
from cicero.core import prompts
from cicero.core.prompt_builder import build_compliance_messages
from cicero.providers.mock import COMPLIANCE_MARKER


async def test_mock_answers_the_compliance_question_with_a_stance() -> None:
    provider = MockProvider()
    messages = build_compliance_messages("a motion", "an argument")
    result = await provider.generate(messages, GenerateOptions(model="mock-small"))
    assert parse_stance(result.content) is not None


async def test_mock_compliance_answer_is_configurable() -> None:
    provider = MockProvider(compliance_answer="con")
    messages = build_compliance_messages("a motion", "an argument")
    result = await provider.generate(messages, GenerateOptions(model="mock-small"))
    assert parse_stance(result.content) is Stance.CON


def test_compliance_marker_matches_the_real_prompt() -> None:
    """The provider layer keeps its own literal so it does not import core.prompts;
    this is the test that stops the two drifting apart."""
    assert COMPLIANCE_MARKER in prompts.COMPLIANCE_SYSTEM.lower()


async def test_a_compliance_prompt_is_not_mistaken_for_a_stance_poll() -> None:
    """Both ask for one word. They must not collapse into the same branch, or the
    mock reports the debater's poll answer as the judge's reading."""
    provider = MockProvider(poll_answer="pro", compliance_answer="con")
    messages = build_compliance_messages("a motion", "an argument")
    result = await provider.generate(messages, GenerateOptions(model="mock-small"))
    assert parse_stance(result.content) is Stance.CON
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_mock_provider.py -k compliance -v`
Expected: FAIL — `ImportError: cannot import name 'COMPLIANCE_MARKER'`

- [ ] **Step 3: Write minimal implementation**

In `backend/cicero/providers/mock.py`, after `MODERATOR_MARKER`:

```python
#: Identifies a compliance-judge prompt from its system text, on the same terms as
#: ``POLL_MARKER``: a literal, so the provider layer stays independent of
#: ``core.prompts``, with a test asserting the two never drift apart.
COMPLIANCE_MARKER = "impartial reader"

#: A mock has no view on which side a turn argues, so it reports none — but it
#: reports it in a shape ``parse_stance`` can read, which is what keeps the offline
#: acceptance path exercising the real code.
_DEFAULT_COMPLIANCE_ANSWER = "neutral"
```

Add the constructor parameter, after `poll_answer`:

```python
        compliance_answer: str = _DEFAULT_COMPLIANCE_ANSWER,
    ) -> None:
        ...
        self._compliance_answer = compliance_answer
```

and document it in the class docstring's `Args:` block:

```
        compliance_answer: The side reported when asked which side a turn argues.
```

In `generate`, add the branch **before** the `POLL_MARKER` branch — both prompts ask for one word, and the compliance prompt is identified by its distinct system text:

```python
        if self._scripted:
            content = self._scripted.pop(0)
        elif COMPLIANCE_MARKER in system.lower():
            content = self._compliance_answer
        elif POLL_MARKER in last.lower():
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_mock_provider.py -v`
Expected: PASS

- [ ] **Step 5: Confirm the offline demo still passes**

Run: `cd backend && make demo`
Expected: all checks pass, 0 failed.

Note: `scripts/demo.py`'s `detect_roster()` prefers any reachable live provider over the mock (issue #15), so stop any local Ollama first, or the run is not the offline path it claims to be.

- [ ] **Step 6: Commit**

```bash
git add backend/cicero/providers/mock.py backend/tests/test_mock_provider.py
git commit -m "feat: answer the compliance question in the mock provider (F9)"
```

---

### Task 8: Surface it on the outcome

**Files:**
- Modify: `backend/cicero/core/outcome.py:49-60` (`OutcomeSummary`) and `:199-210` (`summarize_outcome`)
- Modify: `backend/cicero/api/routers/chambers.py:617-634` (`get_outcome`)
- Modify: `backend/cicero/core/export.py:102-129`
- Test: `backend/tests/test_outcome.py`, `backend/tests/test_api.py`, `backend/tests/test_export.py`

**Interfaces:**
- Consumes: `compliance_caveat`, `noncompliance_lines` (Task 3)
- Produces: `OutcomeSummary.compliance_caveat: str`, `OutcomeSummary.noncompliance: tuple[str, ...]`; the same two keys on `GET /chambers/{id}/outcome` (`noncompliance` as a JSON list).

`OutcomeSummary` is a frozen dataclass with no defaults, deliberately — the existing `caveat` field's comment says every construction should state whether the labels need qualifying. Follow that: give the new fields no defaults either. There are exactly two construction sites and both must be updated: `cicero/core/outcome.py:204` and `tests/test_outcome.py:392`. The test one will fail to compile until it passes the new fields, which is the point of having no defaults.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_outcome.py` (reuse that file's existing chamber helpers):

```python
def test_summary_carries_the_compliance_caveat_and_lines() -> None:
    ada, bob = _debater(Stance.PRO, "Ada"), _debater(Stance.CON, "Bob")
    chamber = _concluded_chamber([ada, bob], winner=Stance.PRO)
    chamber.turns = [
        Turn(participant_id=ada.id, round_index=0, content="x", metadata={"argued": "pro"}),
        Turn(participant_id=bob.id, round_index=0, content="y", metadata={"argued": "pro"}),
    ]
    summary = summarize_outcome(chamber)
    assert summary is not None
    assert summary.compliance_caveat == UNOPPOSED_CAVEAT
    assert summary.noncompliance == (
        "Bob (assigned con) argued pro in 1 of 1 judged turns",
    )


def test_summary_compliance_fields_are_empty_when_nothing_was_judged() -> None:
    ada, bob = _debater(Stance.PRO, "Ada"), _debater(Stance.CON, "Bob")
    chamber = _concluded_chamber([ada, bob], winner=Stance.PRO)
    summary = summarize_outcome(chamber)
    assert summary is not None
    assert summary.compliance_caveat == ""
    assert summary.noncompliance == ()


def test_the_stance_caveat_and_the_compliance_caveat_are_separate_fields() -> None:
    """Two unrelated qualifications. Conflating them would make one of them
    unreachable whenever the other fires."""
    ada, bob = _debater(Stance.PRO, "Ada"), _debater(Stance.CON, "Bob")
    chamber = _concluded_chamber([ada, bob], winner=Stance.PRO)
    chamber.turns = [
        Turn(participant_id=bob.id, round_index=0, content="y", metadata={"argued": "pro"}),
    ]
    summary = summarize_outcome(chamber)
    assert summary is not None
    assert summary.compliance_caveat != summary.caveat
```

Add to `backend/tests/test_api.py`, following the existing outcome-endpoint test's fixture style:

```python
def test_outcome_endpoint_serves_the_compliance_fields(client: TestClient) -> None:
    chamber_id = _concluded_chamber_id(client)
    body = client.get(f"/chambers/{chamber_id}/outcome").json()
    assert "compliance_caveat" in body
    assert isinstance(body["noncompliance"], list)
```

Add to `backend/tests/test_export.py`:

```python
def test_markdown_renders_the_compliance_caveat_and_lines() -> None:
    ada, bob = _debater(Stance.PRO, "Ada"), _debater(Stance.CON, "Bob")
    chamber = _concluded_chamber([ada, bob], winner=Stance.PRO)
    chamber.turns = [
        Turn(participant_id=bob.id, round_index=0, content="y", metadata={"argued": "pro"}),
    ]
    markdown = to_markdown(chamber)
    assert "Bob (assigned con) argued pro in 1 of 1 judged turns" in markdown
    assert UNOPPOSED_CAVEAT in markdown
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_outcome.py -k compliance -v`
Expected: FAIL — `AttributeError: 'OutcomeSummary' object has no attribute 'compliance_caveat'`

- [ ] **Step 3: Write minimal implementation**

In `outcome.py`, add to `OutcomeSummary` after `caveat`:

```python
    #: Fires when the winning position was never argued against, though a debater
    #: was assigned to. Separate from ``caveat``: that one qualifies the stance
    #: *labels*, this one qualifies whether the debate was contested at all.
    compliance_caveat: str
    #: One line per debater judged to have argued against its assigned side.
    noncompliance: tuple[str, ...]
```

and in `summarize_outcome`:

```python
        caveat=_stance_caveat(chamber, consensus),
        compliance_caveat=compliance_caveat(chamber),
        noncompliance=noncompliance_lines(chamber),
    )
```

with the import:

```python
from cicero.core.compliance import compliance_caveat, noncompliance_lines
```

In `chambers.py`'s `get_outcome`, add to the returned dict:

```python
        "caveat": summary.caveat,
        "compliance_caveat": summary.compliance_caveat,
        "noncompliance": list(summary.noncompliance),
    }
```

In `export.py`, after the `summary.movements` block and before the `summary.caveat` block:

```python
        if summary.noncompliance:
            lines.append(
                f"- **Argued against their assigned side:** "
                f"{', '.join(summary.noncompliance)}"
            )
        if summary.compliance_caveat:
            lines.append("")
            lines.append(f"*{summary.compliance_caveat}*")
```

Note: `to_export_dict` at `export.py:26` uses `asdict(summary) | {"movements": list(...)}`. `noncompliance` is a tuple too, so extend that override: `| {"movements": list(summary.movements), "noncompliance": list(summary.noncompliance)}`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && make test && make lint && make type`
Expected: PASS, clean.

- [ ] **Step 5: Commit**

```bash
git add backend/cicero/core/outcome.py backend/cicero/core/export.py backend/cicero/api/routers/chambers.py backend/tests/
git commit -m "feat: serve and export the compliance caveat and lines (F9)"
```

---

### Task 9: Show it in the UI

**Files:**
- Modify: `frontend/src/types.ts:99-109` (`OutcomeSummary`), `:19-23` (`DebateSettings`)
- Modify: `frontend/src/ChamberDetail.tsx:849-870` (the outcome card), `:538-556` (the settings form)
- Test: `frontend/src/ChamberDetail.outcome.test.tsx`

**Interfaces:**
- Consumes: the two new fields on `GET /chambers/{id}/outcome` (Task 8)

The existing outcome tests set every `DebateSettings` field explicitly, so adding `measure_compliance` to the interface means updating the fixtures in `ChamberDetail.outcome.test.tsx`, `ChamberDetail.mute.test.tsx` and `ChamberDetail.moderator.test.tsx`.

**Race warning:** the outcome fields arrive from a second effect (`getOutcome`) that resolves *after* the chamber load. Assert them with `await findByText(...)`, never a synchronous `getByText` — that exact shape caused a 25–40% flaky test during F5 and cost three review rounds to diagnose.

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/ChamberDetail.outcome.test.tsx`:

```tsx
it("names debaters that argued against their assigned side", async () => {
  mockOutcome({
    ...baseOutcome,
    noncompliance: ["Bob (assigned con) argued pro in 3 of 3 judged turns"],
  });
  render(<ChamberDetail id={chamberId} onBack={() => {}} />);
  expect(
    await screen.findByText(/Bob \(assigned con\) argued pro in 3 of 3 judged turns/),
  ).toBeInTheDocument();
});

it("shows the caveat when the winning side was never opposed", async () => {
  mockOutcome({ ...baseOutcome, compliance_caveat: "No debater was judged to argue against the winning position." });
  render(<ChamberDetail id={chamberId} onBack={() => {}} />);
  expect(
    await screen.findByText(/never judged to argue against|No debater was judged/),
  ).toBeInTheDocument();
});

it("shows neither when compliance was not measured", async () => {
  mockOutcome({ ...baseOutcome, compliance_caveat: "", noncompliance: [] });
  render(<ChamberDetail id={chamberId} onBack={() => {}} />);
  await screen.findByText(baseOutcome.support);
  expect(screen.queryByText(/assigned con/)).not.toBeInTheDocument();
});
```

Adapt `mockOutcome` / `baseOutcome` to the helpers already in that file.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- ChamberDetail.outcome`
Expected: FAIL — the text is not rendered.

- [ ] **Step 3: Write minimal implementation**

In `types.ts`, add to `OutcomeSummary`:

```ts
  // Fires when the winning position was never argued against, though a debater
  // was assigned to. Distinct from `caveat`, which qualifies the stance labels.
  compliance_caveat: string;
  // One line per debater judged to have argued against its assigned side.
  noncompliance: string[];
```

and to `DebateSettings`:

```ts
  measure_compliance: boolean;
```

In `ChamberDetail.tsx`, inside the `<dl className="outcome-facts">`, after the movements block:

```tsx
              {outcome.noncompliance.length > 0 && (
                <>
                  <dt>Argued against their assigned side</dt>
                  <dd>{outcome.noncompliance.join(", ")}</dd>
                </>
              )}
```

and after the existing `outcome?.caveat` paragraph:

```tsx
          {outcome?.compliance_caveat && (
            <p className="muted" style={{ fontSize: "0.85rem" }}>
              {outcome.compliance_caveat}
            </p>
          )}
```

In the settings form, beside the `web_evidence` checkbox:

```tsx
              <label title="Judge each turn for which side it argues — one extra model call per turn">
                <input
                  type="checkbox"
                  checked={settingsForm.measure_compliance}
                  onChange={(e) =>
                    setSettingsForm({
                      ...settingsForm,
                      measure_compliance: e.target.checked,
                    })
                  }
                />{" "}
                Measure compliance
              </label>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npm test && npm run typecheck && npm run lint && npm run build`
Expected: all PASS.

- [ ] **Step 5: Check for flakiness**

Run: `cd frontend && for i in $(seq 1 10); do npm test -- ChamberDetail.outcome --run || break; done`
Expected: 10 clean runs. Any failure means a synchronous assertion on second-effect text — convert it to `await findByText`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "feat: show compliance on the outcome card (F9)"
```

---

### Task 10: Validate the judge against hand-reading

**Files:**
- Create: `backend/scripts/check_compliance_judge.py`
- Modify: `docs/superpowers/decisions/2026-08-06-debaters-do-not-hold-assigned-sides.md` (append the measured agreement rate)

**This task is a gate, not a formality.** The decision record documents a keyword classifier for this exact task that was wrong by a factor of seven against hand-reading, matching words like "essential" inside passages *rebutting* the pro case. A judge that reads prose badly produces a confident, wrong compliance record — worse than none, because it looks authoritative. **If the judge disagrees materially with hand-reading, say so and stop; do not ship it on that model.**

- [ ] **Step 1: Write the script**

Create `backend/scripts/check_compliance_judge.py`:

```python
"""Print the compliance judge's reading of real turns, for hand-checking.

Reads an existing chamber from the API and judges each of its debate turns. It
writes nothing: the point is to compare the judge's answer against your own
reading of the same prose, because a keyword classifier for this exact task was
wrong by a factor of seven (see the decision record).

    python scripts/check_compliance_judge.py <chamber-id> --model llama3.1:latest
"""

from __future__ import annotations

import argparse
import asyncio

import httpx

from cicero.core.compliance import ComplianceJudge
from cicero.domain.enums import ProviderType
from cicero.domain.models import Chamber
from cicero.providers.factory import ProviderFactory


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("chamber_id")
    parser.add_argument("--model", default="llama3.1:latest")
    parser.add_argument("--provider", default="ollama", choices=[p.value for p in ProviderType])
    parser.add_argument("--api", default="http://localhost:8000")
    args = parser.parse_args()

    with httpx.Client(base_url=args.api, timeout=30.0) as client:
        response = client.get(f"/chambers/{args.chamber_id}")
        response.raise_for_status()
        chamber = Chamber.model_validate(response.json())

    provider = ProviderFactory().get_for_type(ProviderType(args.provider))
    judge = ComplianceJudge(provider, args.model)

    print(f"motion: {chamber.topic}\njudge: {args.provider}/{args.model}\n")
    print(f"{'rnd':>3}  {'speaker':<12} {'assigned':<9} {'judged':<8} excerpt")
    print("-" * 100)
    for turn in chamber.turns:
        if turn.participant_id is None or not turn.content:
            continue
        speaker = chamber.participant_by_id(turn.participant_id)
        if speaker is None:
            continue
        argued = await judge.judge(chamber.topic, turn.content)
        excerpt = " ".join(turn.content.split())[:200]
        print(
            f"{turn.round_index:>3}  {speaker.display_name:<12} "
            f"{speaker.stance.value:<9} {(argued.value if argued else '—'):<8} {excerpt}"
        )


if __name__ == "__main__":
    asyncio.run(main())
```

Note for the implementer: check `ProviderFactory`'s real constructor signature in `cicero/providers/factory.py` before running — it may need configuration passed in, and `scripts/demo.py` shows how the rest of the codebase builds one.

- [ ] **Step 2: Run it against at least 12 real turns**

Run: `cd backend && python scripts/check_compliance_judge.py --model llama3.1:latest`
Expected: a table of 12+ rows.

- [ ] **Step 3: Hand-read the same turns**

Read each printed excerpt yourself and record the side *you* judge it to argue. Do not use a keyword heuristic, and do not read the judge's answer first. The decision record is explicit that automated prose classification failed here by 7×, and that reading the turns is what produced the real finding.

- [ ] **Step 4: Compare and record**

Compute agreement between your reading and the judge's. Append the result to the decision record under a new "Judge agreement" heading, stating the model, the sample size, and the rate.

**Gate:** ≥10 of 12 agreement to proceed. Below that, stop and report — the feature does not ship on that model, and the finding belongs in the docs page instead of a shipped measurement.

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/check_compliance_judge.py docs/superpowers/decisions/2026-08-06-debaters-do-not-hold-assigned-sides.md
git commit -m "test: measure the compliance judge against hand-reading (F9)"
```

---

### Task 11: The guidance page

**Files:**
- Create: `docs/model-selection.md`
- Modify: `README.md:235-238` (the docs table)
- Modify: `docs/superpowers/decisions/2026-08-06-debaters-do-not-hold-assigned-sides.md` (link forward to the page)

- [ ] **Step 1: Write the page**

Create `docs/model-selection.md` covering exactly three things:

1. **Roster choice, not prompt wording, decides whether a debate is adversarial.** Give the measured table (`llama3.1` 8/8, `apertus:8b` 5/8, `command-r` 1/8 holding an assigned con side), and state that three prompt-level hypotheses — the poll's assigned-stance labels, question-versus-proposition phrasing, and the truth-seeking clause — were each refuted by measurement, so `prompts.py` is not where to look.
2. **How to build your own table**, using the compliance record: enable `measure_compliance`, run a debate, read `Argued against their assigned side` on the outcome and the `argued` key in the JSON export. This is the part that keeps the page useful once those three models are obsolete.
3. **The methodological warnings**, because they generalise: an n=4 spot check overstated the severity by more than double (3-in-4 became 5-in-12 at n=12 per arm), and automated prose classification was wrong sevenfold. Read the turns.

State the judge's own measured agreement rate from Task 10, and say plainly that the compliance record is one model's reading, not ground truth.

- [ ] **Step 2: Link it**

Add a row to the README docs table:

```markdown
| [`docs/model-selection.md`](./docs/model-selection.md) | Which models can actually hold an assigned opposing side, and how to measure it. |
```

- [ ] **Step 3: Verify the links resolve**

Run: `cd /Users/feaa/Code/cicero && grep -c "model-selection" README.md docs/superpowers/decisions/2026-08-06-debaters-do-not-hold-assigned-sides.md`
Expected: at least 1 in each.

- [ ] **Step 4: Commit**

```bash
git add docs/model-selection.md README.md docs/superpowers/decisions/2026-08-06-debaters-do-not-hold-assigned-sides.md
git commit -m "docs: which models can hold an assigned opposing side (F9)"
```

---

### Task 12: Documentation and traceability

**Files:**
- Modify: `docs/requirements.md:165` (after FR-33)
- Modify: `docs/backlog.md` (Epic F table, after F8)
- Modify: `docs/testing.md:68-83` (the gated-module table) and `:37-38` (the prose list of gated modules)
- Modify: `docs/architecture.md` (endpoint table)

Two of these fix known-stale rows rather than adding new ones: `docs/testing.md` is missing `core/outcome.py` and `docs/architecture.md`'s endpoint table is missing `GET /chambers/{id}/outcome`, both from F5 — that is issue #17. Fix them in this pass rather than leaving a reference table half-updated.

- [ ] **Step 1: Add FR-34**

In `docs/requirements.md`, after FR-33:

```markdown
- **FR-34 (S)** Measure, per turn, which side of the motion the turn actually
  argues, and report when a chamber's winning position was never argued against.
  Read from the turn's prose, never from the stance poll, and never used to
  decide an outcome.
```

- [ ] **Step 2: Add backlog story F9**

Add this row to the Epic F table in `docs/backlog.md`, after F8, filling in the bracketed figures from Task 10:

```markdown
| F9 | As a reader, I can tell whether the chamber's winning position was ever actually argued against, rather than assuming a consensus means debaters converged. | FR-34 | S | 5 | ✅ done — each turn is judged for the side its *prose* argues, by the moderator, which is never told who wrote the turn or what they were assigned; the comparison against the assignment is pure Python in `core/compliance.py`. Motivated by five of twelve con-assigned opening turns arguing the pro case, and by a five-debater run reporting "unanimous consensus" when nobody had argued the other side. Three prompt-level fixes were already refuted by measurement — holding an assigned side is a per-model capability (`llama3.1` 8/8, `command-r` 1/8), so this measures rather than rewords. Decides nothing: no decision rule, stop condition or outcome value reads it. Judge agreement with hand-reading measured at [N of M] on [model] |
```

- [ ] **Step 3: Fix the testing.md tables**

Add `core/compliance.py` — the compliance readers and the caveat rule — **and** the missing `core/outcome.py` row to the gated column. Add both module names to the prose list at lines 37–38.

- [ ] **Step 4: Fix the architecture.md endpoint table**

Add the missing `GET /chambers/{id}/outcome` row (F5), and note that its payload now carries the compliance fields.

- [ ] **Step 5: Verify the docs match the code**

Run: `cd /Users/feaa/Code/cicero && grep -n "compliance" docs/testing.md docs/requirements.md docs/backlog.md docs/architecture.md`
Expected: a hit in each of the four files.

- [ ] **Step 6: Commit**

```bash
git add docs/
git commit -m "docs: record compliance measurement (F9), fix two stale tables (#17)"
```

---

### Task 13: Whole-branch verification

- [ ] **Step 1: Full backend gate**

Run: `cd backend && make lint && make type && make test`
Expected: all pass. Record the test count.

- [ ] **Step 2: Full frontend gate**

Run: `cd frontend && npm run typecheck && npm run lint && npm test && npm run build`
Expected: all pass.

- [ ] **Step 3: Scoped mutation on the new module**

Run (Python 3.11 venv): `cd backend && mutmut run --paths-to-mutate cicero/core/compliance.py && mutmut results`
Expected: every mutant killed, or each survivor named and argued equivalent. Then `git status` — no `.bak`, no mutated source left behind.

- [ ] **Step 4: Offline acceptance**

Run: `cd backend && make demo`
Expected: 0 failed. Stop any local Ollama first (issue #15).

- [ ] **Step 5: Real-model smoke test**

Run one real debate with `measure_compliance` on, against a live provider. Confirm: turns carry `argued` in the JSON export, the outcome card renders the new fields when they apply, and a debate with a genuinely contested motion produces **no** caveat. A caveat on every debate means the rule is inverted or the judge is answering the same word every time.

- [ ] **Step 6: Confirm nothing became load-bearing**

Run: `cd backend && grep -rn "compliance" cicero/core/budget.py cicero/core/consensus.py cicero/core/repetition.py`
Expected: no matches. The spec forbids this signal reaching any decision; this is the check that it did not drift there during implementation.

---

## Self-Review

**Spec coverage:** §1 what is asked → Tasks 4, 5. §2 storage → Tasks 2, 6. §3 module → Tasks 2, 3, 5. §4 wiring → Task 6. §5 failure → Tasks 5, 6. §6 caveat rule → Task 3. §7 per-debater reporting → Task 3. §8 prompt and injection surface → Task 4. §9 setting → Tasks 1, 9. §10 surfacing → Tasks 8, 9. Testing §: Tasks 2, 3, 7, 10, 13. Part two (guidance page) → Task 11. Traceability → Task 12.

**Two spec requirements needed tasks of their own and got them:** the real-model validation gate (Task 10), which the spec explicitly says is not a unit test; and the "nothing became load-bearing" check (Task 13 step 6), which no single feature task would have caught.

**Naming consistency:** `ARGUED_KEY` (Task 2) is used in Tasks 6 and 8. `ComplianceRecord` fields `assigned/judged/held/argued_against` are used identically in Tasks 2, 3. `compliance_caveat` / `noncompliance` are the same names in the dataclass (Task 8), the JSON payload (Task 8), and TypeScript (Task 9). `ComplianceJudge(provider, model)` is constructed the same way in Tasks 5, 6.
