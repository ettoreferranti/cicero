# Spec: stop the outcome fields contradicting the statement

> **Status:** approved design, ready for planning.
> **Traceability:** extends FR-23 / FR-25. Backlog story **F6**, Epic F.
> **Context:** [`2026-08-06-chamber-scoped-positions-not-built.md`](../decisions/2026-08-06-chamber-scoped-positions-not-built.md)
> records the five experiments that rejected the larger position-registry design and
> identified this smaller defect as the real one.

## Problem

A concluded debate currently renders like this:

```markdown
**The chamber concluded:** …fundamentals must be taught before integrating GenAI.
- **Support:** contested — 2 of 3 debaters settled on pro (1 neutral)
- **How decided:** majority of final positions
- **Positions moved:** Bob (con→neutral), Eve (neutral→pro)

The debate resolved in favor of teaching foundational programming… Bob's revised
position — advocating for core concepts through hands-on practice before using
GenAI for optimization — aligned with this view…
```

Read it in order. The fields say Bob became undecided and stands outside the winning
position. The statement, three lines later, says Bob authored it. Both come from the
same debate; only one is a description of Bob.

Neither is a bug in isolation. `con→neutral` is a **correct record of the stance
poll** — Bob's one-word answers really did go from `con` to `neutral`. It is a
**wrong description of the debater**, because the poll has no word for the compromise
he actually moved to, so `neutral` is where every compromise lands (this is the same
collapse that motivated F5's headline).

The defect is that nothing on screen distinguishes those two readings, and the fields
are presented as a compact, authoritative list *above* the prose that corrects them.

## Goals

- A reader can tell that stance labels are a coarse poll record, not a characterisation.
- The statement reliably names who moved and what they moved *to*, in its own words.
- No new stored fields, no new parsing, no schema change.

## Non-goals

- Chamber-scoped position labels, minting, merging — rejected, see the decision record.
- Changing the stance vocabulary, the poll, or any decision rule.
- Question mode.

## Design

### 1. The movement field says what it is

`movements` is a record of the **poll**, so it should be labelled as one. The display
label changes from `Positions moved` to `Recorded stance changes` in the Markdown
export and the UI.

The JSON key stays `movements`. `to_export_dict`'s shape already changed once in F5;
renaming the key would break the API contract a second time for a display concern.

### 2. A caveat, shown only when it is needed

When the outcome block contains the label `neutral` anywhere — in the Support
breakdown or in a stance change — a single muted line appears beneath the fields:

> *Stance labels are the poll's three-word record. A debater who moved to a compromise
> is recorded as `neutral`; the statement below describes what actually changed.*

The `neutral` trigger is deliberate rather than always-on. `neutral` is the one label
that is overloaded — it means both "holds no position" and "holds a position the poll
cannot name". `pro` and `con` are unambiguous, so a debate that never touches `neutral`
gets no caveat and no noise.

Derived from the already-computed `support` and `movements` strings, so no new state.

### 3. The statement names the movement

Each of the four `MODERATOR_*_TASK` prompts gains one sentence:

> Where a debater's position changed during the debate, name them and say what they
> moved to in your own words — describe the position they arrived at, not the one-word
> label it was recorded under, since a debater who moved to a specific compromise is
> recorded only as `neutral`.

No new field, no directive, no parsing — this only enriches prose that is already
produced and already displayed.

**Measured effect, not assumed.** Tested against two moderators on the real debate:

| moderator | shipped prompt | with the clause |
|---|---|---|
| `qwen3:30b` | already narrates Bob's move well | comparable; no clear gain |
| `command-r:latest` | describes the dissent without naming who | **names Bob and his position** |

So: a real improvement on the weaker model, no regression on the stronger one. This is
a modest, honest gain — it is included because it costs one sentence, not because it
transforms the output.

### Considered and rejected

**Moving the fields below the statement**, so prose leads and the tally supports it.
It would fix the "authoritative list above the correction" ordering directly. Rejected
because the fields exist to be read at a glance, and burying them under a paragraph
removes the reason they were added in F5. The caveat addresses the same problem
without giving that up.

## Testing

**Pure, mutation-gated** (`core/outcome.py`, `core/export.py`):

- The caveat appears when `neutral` is in the Support breakdown; when it is in a stance
  change; when it is in both; and **not** when the debate only involved `pro`/`con`.
  That last case is the one that keeps the trigger honest — a test that only asserts
  presence would pass with an always-on caveat.
- The `Recorded stance changes` label is used, and the line is still omitted entirely
  when nothing moved (existing behaviour must not regress).
- JSON `outcome_summary.movements` keeps its key and value shape.

**Moderator prompts:** the clause is present in all four task texts, and the
`{winner}` placeholder in `MODERATOR_MAJORITY_TASK` still formats — that string mixes
f-string interpolation with a runtime `.format()` placeholder and has broken this way
before.

**Frontend:** caveat renders when present, absent when not; label text updated;
existing outcome-card tests still pass.

## Risks

**The caveat becomes wallpaper.** Most debates involve `neutral`, so most outcome
blocks will carry it, and a caveat that always appears stops being read. The trigger
rule limits this but does not eliminate it. If it proves noisy in practice, the next
move is to show it only when a stance *change ends on* `neutral` — the specific shape
that misleads — rather than on any occurrence.

**The clause could make statements worse.** It lengthens a prompt that currently works,
and F5 has precedent for prompt edits degrading output: rewriting `POLL_USER_INSTRUCTION`
during the experiments flipped two debaters' stances. The measurement above shows no
regression on either model tested, but it is two models on one debate. Worth re-reading
a real statement after this ships.
