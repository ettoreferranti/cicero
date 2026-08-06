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

> *Stance labels record how each debater answered a three-word poll, not what they
> argued. A neutral answer covers both holding no position and holding a compromise
> the poll has no word for — read the transcript for what a debater actually held.*

It points at the **transcript**, deliberately, not at the moderator's statement. The
first wording ended "the statement below describes what actually changed"; §3 explains
why vouching for the statement turned out to be unsafe. The transcript is the primary
record — the statement is another model's reading of it.

The `neutral` trigger is deliberate rather than always-on. `neutral` is the one label
that is overloaded — it means both "holds no position" and "holds a position the poll
cannot name". `pro` and `con` are unambiguous, so a debate that never touches `neutral`
gets no caveat and no noise.

Derived from the already-computed `support` and `movements` strings, so no new state.

### 3. The statement names the movement — BUILT, THEN REVERTED

Each of the four `MODERATOR_*_TASK` prompts gained one sentence asking the moderator to
name any debater whose position changed and say what they moved to.

**This was shipped and then reverted. Do not reinstate it without new evidence.**

The justification was a measured gain: on the original test debate `command-r:latest`
named the mover it had otherwise left anonymous, and `qwen3:30b` was unaffected. That
gain was real but marginal, and it was traded against a much worse failure.

On the next real debate the shipped statement claimed a debater *"ultimately endorsed
this compromise"*, when his final turn opens *"I respectfully disagree with Alice's
updated position"* and proposes an alternative to the very course the headline
advocates. A controlled re-run on that transcript — same model, temperature 0, the
clause as the only variable — reproduced it:

| prompt | what it said about the dissenting debater |
|---|---|
| without the clause | *"Bob's dissent centers on whether this requires a standalone course…"* — accurate |
| with the clause | *"he ultimately accepted it as the optimal compromise… rendering his earlier dissent moot"* — **false** |

Asking a model to narrate movement induces it to manufacture a conversion arc when
there is none. This spec's own Risks section flagged the possibility and it was shipped
anyway on the strength of one weak positive; that was the wrong trade. Fabricating a
debater's position is strictly worse than the field/statement contradiction this spec
set out to fix — especially since §2's caveat then pointed readers at the statement.

The `{winner}`-placeholder regression test added alongside the clause was kept: it
guards a real trap in `MODERATOR_MAJORITY_TASK` independently of this.

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

**Moderator prompts:** the `{winner}` placeholder in `MODERATOR_MAJORITY_TASK` still
formats — that string mixes f-string interpolation with a runtime `.format()`
placeholder and has broken this way before. (The clause-presence test went with the
clause; this one stays.)

**Frontend:** caveat renders when present, absent when not; label text updated;
existing outcome-card tests still pass.

## Risks

**The caveat becomes wallpaper.** Most debates involve `neutral`, so most outcome
blocks will carry it, and a caveat that always appears stops being read. The trigger
rule limits this but does not eliminate it. If it proves noisy in practice, the next
move is to show it only when a stance *change ends on* `neutral` — the specific shape
that misleads — rather than on any occurrence.

**The clause made statements worse — this risk materialised.** It was flagged here
before shipping, shipped anyway on one weak positive, and produced a fabricated
endorsement on the next real debate. See §3. The general lesson: a two-model, one-debate
measurement is not enough to justify editing a prompt that already works, and the
failure mode to watch for is not a worse-written statement but a *confidently wrong* one.

**Stance movement is invisible when a debater changes sides of an argument without
changing label.** In the debate that exposed §3, all three debaters moved substantively
and `movements` was empty: one reversed her position entirely while both readings scored
`pro`, one never registered his assigned `con` at all, and one moved and moved back.
The caveat covers the `neutral` case; it does not cover this one, and first-vs-last
comparison over three words cannot. Not fixed here — recorded so the next attempt starts
from it.
