# Outcome Fields vs Statement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the derived outcome fields asserting things the moderator's statement contradicts — mark stance labels as the poll record they are, and have the statement name who moved and to what.

**Architecture:** One new derived (not stored) field on `OutcomeSummary`, computed from data already recorded; a display-label rename; and one sentence added to four prompt strings. No schema change, no new parsing, no stored state.

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI, pytest (`asyncio_mode = "auto"`), mutmut; React 18 + TypeScript + Vitest.

**Spec:** [`docs/superpowers/specs/2026-08-06-outcome-fields-vs-statement-design.md`](../specs/2026-08-06-outcome-fields-vs-statement-design.md)
**Context:** [`docs/superpowers/decisions/2026-08-06-chamber-scoped-positions-not-built.md`](../decisions/2026-08-06-chamber-scoped-positions-not-built.md)

## Global Constraints

- Backend commands run from `backend/`, frontend from `frontend/`.
- `ruff` line length **100**; lint selects `["E","F","I","B","C4","UP","S"]` (bare `assert` flagged outside tests).
- `mypy` **strict**, `disallow_untyped_defs = true` — full annotations, tests included (`-> None`).
- Every module starts with `from __future__ import annotations`.
- Docstrings and comments explain *why*, not *what*.
- `cicero/core/outcome.py` and `cicero/core/export.py` are in the mutation gate. Run it **scoped and in the foreground**, never bare `make mutation`:
  `source /private/tmp/claude-503/-Users-feaa-Code-cicero/b73d9d80-9498-4433-a10e-59d1baa61e05/scratchpad/venv311/bin/activate && mutmut run --paths-to-mutate <path> && mutmut results`
  (the repo `.venv` is Python 3.14, where mutmut crashes). Afterwards check `git status` for a stray `.bak`.
- Branch `spec/outcome-headline`, which already tracks `origin`. Do not create a branch or open a PR.

---

### Task 1: Derive the stance caveat

**Files:**
- Modify: `backend/cicero/core/outcome.py`
- Test: `backend/tests/test_outcome.py`

**Interfaces:**
- Produces: `outcome.STANCE_CAVEAT: str`; `OutcomeSummary.caveat: str` (empty when not needed)

The caveat is computed from the underlying `Stance` values, **not** by substring-matching the rendered `support`/`movements` strings — a participant named "Neutral Ned" would otherwise trigger it.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_outcome.py`:

```python
def test_caveat_appears_when_a_deciding_stance_is_neutral() -> None:
    chamber = _concluded(
        ConsensusOutcome.MAJORITY,
        {"a": Stance.PRO, "b": Stance.PRO, "c": Stance.NEUTRAL},
        winner=Stance.PRO,
        unparsed=[],
    )
    summary = summarize_outcome(chamber)
    assert summary is not None
    assert summary.caveat == STANCE_CAVEAT


def test_caveat_appears_when_a_movement_ends_on_neutral() -> None:
    chamber = _concluded(
        ConsensusOutcome.MAJORITY,
        {"a": Stance.PRO, "b": Stance.PRO, "c": Stance.PRO},
        winner=Stance.PRO,
        unparsed=[],
    )
    ada = chamber.participants[0]
    chamber.stance_history = [
        StancePoll(round_index=0, stances={str(ada.id): Stance.CON}),
        StancePoll(round_index=1, stances={str(ada.id): Stance.NEUTRAL}),
    ]
    summary = summarize_outcome(chamber)
    assert summary is not None
    assert summary.caveat == STANCE_CAVEAT


def test_caveat_is_absent_when_no_neutral_is_involved() -> None:
    # The case that keeps the trigger honest: an always-on caveat would pass every
    # other test in this group.
    chamber = _concluded(
        ConsensusOutcome.MAJORITY,
        {"a": Stance.PRO, "b": Stance.PRO, "c": Stance.CON},
        winner=Stance.PRO,
        unparsed=[],
    )
    ada = chamber.participants[0]
    chamber.stance_history = [
        StancePoll(round_index=0, stances={str(ada.id): Stance.CON}),
        StancePoll(round_index=1, stances={str(ada.id): Stance.PRO}),
    ]
    summary = summarize_outcome(chamber)
    assert summary is not None
    assert summary.caveat == ""


def test_caveat_ignores_a_neutral_only_in_an_unparsed_poll() -> None:
    # A carried-forward value is not a measurement, so it must not trigger a
    # caveat about what the poll recorded.
    chamber = _concluded(
        ConsensusOutcome.MAJORITY,
        {"a": Stance.PRO, "b": Stance.PRO, "c": Stance.CON},
        winner=Stance.PRO,
        unparsed=[],
    )
    ada = chamber.participants[0]
    chamber.stance_history = [
        StancePoll(round_index=0, stances={str(ada.id): Stance.CON}),
        StancePoll(
            round_index=1,
            stances={str(ada.id): Stance.NEUTRAL},
            unparsed=[str(ada.id)],
        ),
    ]
    summary = summarize_outcome(chamber)
    assert summary is not None
    assert summary.caveat == ""


def test_caveat_needs_an_actual_movement_not_just_a_neutral_reading() -> None:
    chamber = _concluded(
        ConsensusOutcome.MAJORITY,
        {"a": Stance.PRO, "b": Stance.PRO, "c": Stance.CON},
        winner=Stance.PRO,
        unparsed=[],
    )
    ada = chamber.participants[0]
    # Neutral in the middle, but first and last measurements match, so `movements`
    # reports nothing and there is no label for the caveat to qualify.
    chamber.stance_history = [
        StancePoll(round_index=0, stances={str(ada.id): Stance.PRO}),
        StancePoll(round_index=1, stances={str(ada.id): Stance.NEUTRAL}),
        StancePoll(round_index=2, stances={str(ada.id): Stance.PRO}),
    ]
    summary = summarize_outcome(chamber)
    assert summary is not None
    assert summary.caveat == ""
```

Add `STANCE_CAVEAT` to the module's import block in the test file.

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && python -m pytest tests/test_outcome.py -v -k caveat`
Expected: FAIL — `ImportError: cannot import name 'STANCE_CAVEAT'`

- [ ] **Step 3: Implement**

In `backend/cicero/core/outcome.py`, add beside the other module constants:

```python
#: Shown only when a stance label could be read as a characterisation rather than a
#: poll record. ``neutral`` is the one overloaded label — it means both "holds no
#: position" and "holds a position the poll cannot name" — so a debate that never
#: touches it gets no caveat and no noise.
STANCE_CAVEAT = (
    "Stance labels are the poll's three-word record. A debater who moved to a "
    "compromise is recorded as neutral; the statement below describes what "
    "actually changed."
)
```

Extract the trajectory computation so `movements` and the caveat cannot drift apart:

```python
def _measured_trajectory(chamber: Chamber, participant: Participant) -> list[Stance]:
    """A participant's stances across the polls that actually read them.

    Polls where the reply could not be read are skipped: the value there was carried
    forward, not measured, and treating it as evidence is the confusion
    ``StancePoll.unparsed`` exists to prevent.
    """
    key = str(participant.id)
    return [
        poll.stances[key]
        for poll in chamber.stance_history
        if key in poll.stances and key not in poll.unparsed
    ]
```

Import `Participant` from `cicero.domain.models`. Rewrite `movements` to use it, keeping its behaviour byte-identical:

```python
    for participant in chamber.participants:
        measured = _measured_trajectory(chamber, participant)
        if len(measured) < 2 or measured[0] is measured[-1]:
            continue
```

Then add:

```python
def _stance_caveat(chamber: Chamber) -> str:
    """Whether the stance labels on display need qualifying (see ``STANCE_CAVEAT``).

    Computed from the ``Stance`` values rather than by matching the rendered strings:
    a debater whose display name contains "neutral" must not trigger it.
    """
    consensus = chamber.consensus
    if consensus is None:
        return ""
    deciding = deciding_stances(
        chamber, consensus.final_stances, _effective_unparsed(chamber)
    )
    if any(stance is Stance.NEUTRAL for stance in deciding.values()):
        return STANCE_CAVEAT
    for participant in chamber.participants:
        measured = _measured_trajectory(chamber, participant)
        if len(measured) < 2 or measured[0] is measured[-1]:
            continue
        if Stance.NEUTRAL in (measured[0], measured[-1]):
            return STANCE_CAVEAT
    return ""
```

Add `caveat: str` to `OutcomeSummary` (after `decided_by`) and set it in `summarize_outcome`:

```python
        caveat=_stance_caveat(chamber),
```

- [ ] **Step 4: Verify and gate**

Run: `cd backend && python -m pytest -q && make lint && make type`
Expected: all clean. Existing `OutcomeSummary(...)` constructions in tests may need the new field — update them rather than giving it a default.

Run the scoped mutation on `cicero/core/outcome.py` per the Global Constraints. Kill any survivor on a line you added, or justify it as equivalent.

- [ ] **Step 5: Commit**

```bash
git add backend/cicero/core/outcome.py backend/tests/test_outcome.py
git commit -m "feat: derive a caveat when stance labels could mislead (F6)"
```

---

### Task 2: Render it in the Markdown export

**Files:**
- Modify: `backend/cicero/core/export.py` (outcome block)
- Test: `backend/tests/test_export.py`

**Interfaces:**
- Consumes: `OutcomeSummary.caveat`, `STANCE_CAVEAT` (Task 1)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_export.py`:

```python
def test_markdown_labels_movement_as_the_poll_record() -> None:
    chamber = _concluded_with_headline()
    ada = chamber.participants[0]
    chamber.stance_history = [
        StancePoll(round_index=0, stances={str(ada.id): Stance.PRO}),
        StancePoll(round_index=1, stances={str(ada.id): Stance.CON}),
    ]
    md = to_markdown(chamber)
    # "Positions moved" reads as a claim about the debater; this is a poll record.
    assert "- **Recorded stance changes:**" in md
    assert "**Positions moved:**" not in md


def test_markdown_carries_the_stance_caveat_when_neutral_is_involved() -> None:
    md = to_markdown(_concluded_with_headline())
    assert STANCE_CAVEAT in md


def test_markdown_omits_the_caveat_when_no_neutral_is_involved() -> None:
    chamber = _concluded_with_headline()
    consensus = chamber.consensus
    ada, zeno, kant = chamber.participants
    consensus.final_stances = {
        str(ada.id): Stance.PRO, str(zeno.id): Stance.PRO, str(kant.id): Stance.CON
    }
    consensus.winning_stance = Stance.PRO
    assert STANCE_CAVEAT not in to_markdown(chamber)
```

Import `STANCE_CAVEAT` in the test module. Note `_concluded_with_headline()` is the three-debater majority fixture with two `neutral` and one `con`, so the second test needs no extra setup.

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && python -m pytest tests/test_export.py -v -k "poll_record or caveat"`
Expected: FAIL — `assert '- **Recorded stance changes:**' in md`

- [ ] **Step 3: Implement**

In `backend/cicero/core/export.py`, replace the movement line and add the caveat:

```python
        if summary.movements:
            # "Recorded stance changes", not "Positions moved": this is what the
            # one-word poll captured, which is not the same claim as a description
            # of where the debater actually ended up.
            lines.append(f"- **Recorded stance changes:** {', '.join(summary.movements)}")
        if summary.caveat:
            lines.append("")
            lines.append(f"*{summary.caveat}*")
        lines.append("")
        lines.append(chamber.consensus.statement)
```

- [ ] **Step 4: Verify and gate**

Run: `cd backend && python -m pytest -q && make lint && make type`
Any golden-layout test in `test_export.py` asserting the old `Positions moved` label or the exact block shape will fail — update the expectation, do not weaken the assertion.

Run the scoped mutation on `cicero/core/export.py` per the Global Constraints.

- [ ] **Step 5: Commit**

```bash
git add backend/cicero/core/export.py backend/tests/test_export.py
git commit -m "feat: mark exported stance changes as the poll record (F6)"
```

---

### Task 3: Serve and render the caveat

**Files:**
- Modify: `backend/cicero/api/routers/chambers.py` (`get_outcome`)
- Modify: `frontend/src/types.ts`, `frontend/src/ChamberDetail.tsx`
- Test: `backend/tests/test_api.py`, `frontend/src/ChamberDetail.outcome.test.tsx`

**Interfaces:**
- Consumes: `OutcomeSummary.caveat` (Task 1)
- Produces: `GET /chambers/{id}/outcome` gains a `caveat` key; `OutcomeSummary.caveat: string` in TS

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_api.py`, extend the existing outcome-endpoint success test to assert the new key is present and is a string (the mock debate's stances decide whether it is populated — assert the key exists rather than guessing its value, then tighten once you have run it).

In `frontend/src/ChamberDetail.outcome.test.tsx`, add to the existing `outcome` fixtures a `caveat` field, and add:

```tsx
it("shows the stance caveat when the backend sends one", async () => {
  renderChamber({
    consensus: { /* the existing headline fixture */ },
    outcome: {
      headline: "Mars should wait.",
      support: "contested — 2 of 3 debaters settled on pro (1 neutral)",
      decided_by: "majority of final positions",
      movements: ["Ada (con→neutral)"],
      caveat: "Stance labels are the poll's three-word record.",
    },
  });
  expect(await screen.findByText(/three-word record/)).toBeInTheDocument();
  expect(screen.getByText(/Recorded stance changes/)).toBeInTheDocument();
});

it("omits the caveat when the backend sends none", async () => {
  renderChamber({
    consensus: { /* the existing headline fixture */ },
    outcome: {
      headline: "Mars should wait.",
      support: "contested — 2 of 3 debaters settled on pro (1 con)",
      decided_by: "majority of final positions",
      movements: [],
      caveat: "",
    },
  });
  await screen.findByText("Mars should wait.");
  expect(screen.queryByText(/three-word record/)).not.toBeInTheDocument();
});
```

Follow the file's existing pattern: assertions on second-effect text must be `await screen.findByText(...)`, never synchronous `getByText` — a render race from exactly that mistake cost three fix rounds in F5.

- [ ] **Step 2: Run to verify they fail**

Run: `cd frontend && npx vitest run src/ChamberDetail.outcome.test.tsx`
Expected: FAIL — the caveat text is not rendered.

- [ ] **Step 3: Implement**

`chambers.py`, in `get_outcome`'s returned dict, after `decided_by`:

```python
        "caveat": summary.caveat,
```

`types.ts`:

```ts
export interface OutcomeSummary {
  headline: string;
  support: string;
  decided_by: string;
  movements: string[];
  // Qualifies the stance labels above when they could be read as a
  // characterisation rather than a poll record. Empty when not needed.
  caveat: string;
}
```

`ChamberDetail.tsx`, in the outcome card — rename the label and add the caveat after the `</dl>`:

```tsx
              {outcome.movements.length > 0 && (
                <>
                  <dt>Recorded stance changes</dt>
                  <dd>{outcome.movements.join(", ")}</dd>
                </>
              )}
            </dl>
          )}
          {outcome?.caveat && (
            <p className="muted" style={{ fontSize: "0.85rem" }}>
              {outcome.caveat}
            </p>
          )}
```

- [ ] **Step 4: Verify**

Run: `cd frontend && npm test && npm run lint && npm run typecheck`
Run: `cd backend && python -m pytest -q && make lint && make type`
Expected: all clean. `api/*` is excluded from the mutation gate — no mutation run for this task.

- [ ] **Step 5: Commit**

```bash
git add backend/cicero/api/routers/chambers.py backend/tests/test_api.py \
        frontend/src/types.ts frontend/src/ChamberDetail.tsx \
        frontend/src/ChamberDetail.outcome.test.tsx
git commit -m "feat: surface the stance caveat in the API and outcome card (F6)"
```

---

### Task 4: Ask the statement to name the movement

**Files:**
- Modify: `backend/cicero/core/prompts.py` (the four `MODERATOR_*_TASK` texts)
- Test: `backend/tests/test_consensus.py`

**Interfaces:** none — prompt text only.

Measured effect: on the real debate this named the mover on `command-r:latest` where the shipped prompt did not, and was neutral on `qwen3:30b`. A modest gain for one sentence.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_consensus.py`:

```python
def test_every_moderator_task_asks_the_statement_to_name_movement() -> None:
    for task in (
        prompts.MODERATOR_CONSENSUS_TASK,
        prompts.MODERATOR_DISAGREEMENT_TASK,
        prompts.MODERATOR_MAJORITY_TASK,
        prompts.MODERATOR_JUDGE_TASK,
    ):
        assert prompts.MODERATOR_MOVEMENT_CLAUSE in task


def test_majority_task_still_formats_its_winner_placeholder() -> None:
    # This string mixes f-string interpolation with a runtime .format() placeholder
    # and has broken that way before.
    assert "pro" in prompts.MODERATOR_MAJORITY_TASK.format(winner="pro")
    assert "{winner}" not in prompts.MODERATOR_MAJORITY_TASK.format(winner="pro")
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_consensus.py -v -k "movement or placeholder"`
Expected: FAIL — `AttributeError: module ... has no attribute 'MODERATOR_MOVEMENT_CLAUSE'`

- [ ] **Step 3: Implement**

In `backend/cicero/core/prompts.py`, add after `MODERATOR_HEADLINE_DIRECTIVE`:

```python
#: Appended to every moderator task. The one-word poll records a debater who moved to
#: a compromise as "neutral", so the derived fields cannot say what actually changed —
#: the statement can, and measurably does once asked.
MODERATOR_MOVEMENT_CLAUSE = (
    " Where a debater's position changed during the debate, name them and say what "
    "they moved to in your own words — describe the position they arrived at, not the "
    "one-word label it was recorded under, since a debater who moved to a specific "
    "compromise is recorded only as 'neutral'."
)
```

Append `f"{MODERATOR_MOVEMENT_CLAUSE}"` to each of the four task strings, at the end of the final segment. **Keep `{winner}` in `MODERATOR_MAJORITY_TASK` inside a non-f-string segment** — putting it in an f-string raises `NameError` at import.

- [ ] **Step 4: Verify**

Run: `cd backend && python -c "from cicero.core.prompts import MODERATOR_MAJORITY_TASK as m; print(m.format(winner='pro')[:80])"`
Expected: prints text with `pro` substituted, no error.

Run: `cd backend && python -m pytest -q && make lint && make type`
Expected: all clean. `prompts.py` is excluded from the mutation gate (prompt prose).

- [ ] **Step 5: Commit**

```bash
git add backend/cicero/core/prompts.py backend/tests/test_consensus.py
git commit -m "feat: ask the moderator statement to name who moved (F6)"
```

---

### Task 5: Documentation and verification

**Files:**
- Modify: `docs/backlog.md` (Epic F), `docs/requirements.md` (FR-25)

- [ ] **Step 1: Add the backlog row**

Append to the Epic F table, after F5:

```markdown
| F6 | As a reader, the derived outcome fields no longer contradict the moderator's statement: stance changes are labelled as the poll record they are, a caveat appears when `neutral` could be read as a characterisation, and the statement names who moved and to what. | FR-23/25 | S | 2 | ✅ done — `neutral` is the one overloaded stance label (it means both "holds no position" and "holds a position the poll cannot name"), so it is the trigger. See `docs/superpowers/decisions/2026-08-06-chamber-scoped-positions-not-built.md` for the five experiments that rejected the larger position-registry design and identified this as the real defect |
```

- [ ] **Step 2: Extend FR-25**

Extend the FR-25 bullet in `docs/requirements.md` so recorded stance changes are explicitly a record of the poll rather than a characterisation of the debater. Read the current text first and preserve everything the edit does not deliberately change.

- [ ] **Step 3: Full verification**

Run: `cd backend && make lint && make type && make cov`
Run: `cd frontend && npm test && npm run lint && npm run typecheck && npm run build`
Run: `cd backend && make demo` — confirm it still passes, and read the `## Outcome` block in `demo-output/*.md` to see the new label and caveat rendered.

Note `make demo` prefers a reachable live Ollama over the mock, so an unrelated check can fail intermittently; re-run once before investigating.

- [ ] **Step 4: Commit and push**

```bash
git add docs/backlog.md docs/requirements.md
git commit -m "docs: record the outcome-fields fix (F6)"
git push
```

---

## Self-review notes

- **Spec coverage:** §1 (label rename) → Task 2 + 3; §2 (caveat and its trigger) → Task 1 + 2 + 3; §3 (movement clause, with its measured effect) → Task 4. Testing section → the tests in each task, including the spec's named must-have: the absent-caveat case that would pass under an always-on implementation.
- **The trigger is computed from `Stance` values, not rendered strings** — the spec's wording ("when the block contains the label `neutral`") would permit a substring match, which a participant named "Neutral Ned" would trip. Tightened deliberately.
- **Rejected in the spec, not revisited here:** moving the fields below the statement.
