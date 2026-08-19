# Decision: do not ship quote-first compliance judging (F10)

> **Status:** decided, 2026-08-14. The protocol was built, measured, and reverted.
> **Superseded in part, 2026-08-18:** the baseline arm this record reports was
> measured through a broken parser. 6 of its 7 "baseline errors" were
> `parse_stance` reading the judge's reasoning instead of its answer, not judge
> failures — see *What the baseline's errors look like* below and the backlog's
> 2026-08-18 bug entry. The decision not to ship quote-first judging is
> unaffected: that arm produced nothing to parse at all.
> **Concerns:** [`2026-08-12-judge-grounding-design.md`](../specs/2026-08-12-judge-grounding-design.md)
> and its plan [`2026-08-12-judge-grounding.md`](../plans/2026-08-12-judge-grounding.md).
> **Related:** [`2026-08-06-chamber-scoped-positions-not-built.md`](2026-08-06-chamber-scoped-positions-not-built.md)
> — this is the seventh mechanism to land in the same column of that record's table.

## What was proposed

F9's compliance judge answers in one word (`pro` / `con` / `neutral`), and nothing
ties that word to the turn it describes. F10 proposed to make the verdict
*checkable*: ask the judge for two directives,

```
POSITION: <one sentence, copied word for word from the argument, in which the
           author states their own position>
SIDE: pro, con, or neutral
```

verify in Python that the `POSITION` sentence really appears in the turn
(whitespace- and case-insensitive substring, nothing looser), and discard the
verdict when it does not. A turn would then carry `argued` **and**
`argued_quote`, or neither — a stance without its evidence being exactly the
ungrounded record the feature existed to remove.

The reasoning was the pattern in the decision record above: these models do
structured analytical meta-work badly and extraction well, so "find the sentence,
then read it" should beat "classify this argument".

## The measurement

Ground truth: the 32 turns of the Switzerland-invades-Italy chamber
(`832b06b5-d488-488e-bdf5-5211eb53ea40`), hand-read **before** any verdict was
looked at, and committed first — `f10_invasion_turns.md` (the prose, with no
verdicts in it) and `f10_hand_labels.json` (the labels). Both protocols were then
run over those same 32 turns with the same judge, `qwen3:30b`, the model that
produced the errors F10 set out to fix.

| protocol | agreement with hand labels | unmeasured |
|---|---|---|
| one-word (F9, shipped) | **25 / 32** | 0 / 32 |
| quote-first (F10) | **0 / 32** | **32 / 32 (100%)** |

The quote-first protocol did not produce a single usable judgement on a single
turn. `f10_new_verdicts.json` is that run, kept verbatim: every id maps to `null`
under both `verdicts` and `quotes`.

The plan's gate required all three of: agreement beats the baseline, the easy set
does not regress, unmeasured ≤ 25%. It fails the first and third outright, and
the second was never reached — there is nothing to regress from a protocol that
measures nothing. The plan explicitly forbids tuning the prompt until the number
improves, that being fitting to the test set, so the fallback it names was taken:
revert, and keep the record.

### What is *not* known

The harness collapses every failure to `null` — provider error, unreadable
directives, `POSITION: none`, and a quote that is not in the turn are one value
in `f10_new_verdicts.json`. So the run proves the protocol produced nothing
usable; it does not say which of those four happened, or in what mix. Anyone
revisiting this should record the raw replies, not just the parsed verdict. A
100% failure rate is more consistent with a systematic format failure than with
scattered paraphrasing, but that is a hypothesis, not a result.

### What the baseline's errors look like

Worth keeping, because it is the defect F10 aimed at and did not fix. All 7
baseline errors are *inversions*, not abstentions — 6 straight pro↔con flips and
one `con` read as `neutral`. That is the rebuttal failure the spec predicted: a
turn that spends four paragraphs dismantling the pro case reads as pro.

> **Corrected 2026-08-18 — this paragraph was wrong, and the follow-up it
> motivated was aimed at almost nothing.**
>
> Six of those seven errors were not the judge. `parse_stance` strips reasoning
> with a regex requiring an opening `<think>` tag; Ollama with `think: false`
> emits the narration closed by a bare `</think>` and no opening tag, so the
> strip never fired and the narration — which quotes the instruction's own word
> list — was parsed instead of the answer. On these exact 32 replies the judge
> had reasoned to the correct answer and said so.
>
> | parsed from | agreement | unmeasured |
> |---|---|---|
> | the whole reply (as measured here) | 25/32 | 0 |
> | after the final `</think>` (fixed) | **31/32** | 0 |
>
> The real residue is **one** turn, `614d9e5b` — a genuine pro rebuttal read as
> con. That much of this record stands: the rebuttal inversion is real. Its size
> does not.
>
> This is exactly the failure the *What is not known* section above warned about,
> one level up: that section worried the harness could not explain the treatment
> arm's nulls, and the same blindness was hiding a defect in the **baseline**.
> Recording only parsed verdicts cost two measurements, not one. The replies are
> now committed (`tests/fixtures/f10_judge_replies.json`) and re-scoreable
> offline.

## What was kept

- **The fixtures.** `f10_invasion_turns.md`, `f10_hand_labels.json`,
  `f10_baseline_verdicts.json`, `f10_new_verdicts.json`. The first three are a
  reusable, hand-read benchmark for any future change to this judge — the
  expensive part of this work and the part that outlives it. Note the shapes
  differ: the baseline is flat `{turn_id: side}`, written by the shipped harness;
  `f10_new_verdicts.json` is `{verdicts: {...}, quotes: {...}}`, written by the
  reverted harness, and is a frozen record rather than something the current
  script can regenerate.
- **`POST /chambers/{id}/compliance/rejudge`.** Built for F10's migration, but it
  is not specific to it: it re-runs the current judge over a concluded chamber's
  turns and replaces `argued`, which is how any future protocol reaches chambers
  that are already finished. Concluded chambers only, and it clears before it
  writes — a turn the judge can no longer read loses its old verdict rather than
  keeping one the run did not reproduce.
- **`_compliance_judge_for`**, which is what lets the endpoint build the
  chamber's judge without standing up a whole `DebateEngine`.

Everything else is reverted: `prompts.py` is back to the one-word instruction,
`compliance.py` back to `parse_stance` with no `Judgement`, `quote_is_grounded`
or `ARGUED_QUOTE_KEY`, the orchestrator back to recording a stance alone, the
mock provider back to a bare one-word answer, and `check_compliance_judge.py`
back to writing flat verdicts.

## When to revisit

- **A stronger judge.** The judge is a free choice — nothing requires it to be
  the same tier as the debaters. Re-run the two protocols over the committed 32
  turns with a frontier model before concluding anything about the design; this
  result is about `qwen3:30b`.
- **With the raw replies captured**, per *What is not known* above. If the failure
  is format, the design is untested rather than refuted.
- **The rebuttal clause was never measured on its own.** The reverted prompt
  bundled two changes: the quote-first protocol *and* a sentence warning that an
  argument may quote or attack a position in order to argue against it. Adding
  that clause to the shipped one-word prompt is a separable experiment, and this
  decision says nothing about it.

  **Revised 2026-08-18.** This was written as "cheap, against 7 errors of exactly
  that kind". After the parser fix the target is **1** turn out of 32, which no
  run over this fixture can resolve — a one-turn move is indistinguishable from
  noise. The clause is still untested, but it now needs a *larger hand-read
  benchmark* first, and that is the expensive part. Do not re-run it on the
  invasion fixture and read anything into the result.

Reproduce either arm with:

```bash
cd backend && python scripts/check_compliance_judge.py 832b06b5-d488-488e-bdf5-5211eb53ea40 \
  --model qwen3:30b --turns-out /tmp/turns.md --verdicts-out /tmp/verdicts.json
```
