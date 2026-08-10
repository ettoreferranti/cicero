# Model selection: which debaters can hold an assigned side

**Whether a Cicero debate is genuinely adversarial depends on which models sit in
which seats, not on how the stance instruction is worded.** Three prompt-level
fixes were tried and each was refuted by measurement. The variable that actually
moves the number is the model.

## 1. The measured table

Con-assigned debaters, opening turns, judged by reading the prose (not the stance
poll — see [why below](#why-not-the-poll)):

| model | held the assigned `con` side |
|---|---|
| `llama3.1:latest` | 8/8 |
| `apertus:8b` | 5/8 |
| `command-r:latest` | 1/8 |

Read by column this looks like noise between two prompt arms (shipped prompt vs.
the truth-seeking clause removed — 7/12 either way). Read by row it is the
finding: `llama3.1` never dropped the assigned side; `command-r` almost never
kept it. That gap dwarfs every prompt effect measured.

### Three refuted hypotheses — `prompts.py` is not where to look

All three were tested by changing a prompt and re-measuring, not by inspecting
the prose for plausibility:

1. **The stance poll's assigned-stance labels.** Stripping the labels from the
   poll transcript flipped a debater across debates instead of fixing anything —
   an artifact of the poll, not evidence about the debate.
2. **Question vs. proposition phrasing.** The suspicion was that a question
   ("should we teach X?") has no proposition to be "against," so a con-assigned
   debater invents one and inverts it. Measured across `apertus:8b`, `llama3.1`,
   and `command-r`, three samples per cell: question-phrased topics held con
   3/9 and 5/9 across two prompt variants; the **proposition**-phrased topic
   held con **2/9** — worse. If phrasing were the cause, the proposition arm
   should have won. It did not.
3. **The truth-seeking clause** ("you are a truth-seeking debater, not a
   lawyer... you are expected to say so and update your position"). The
   suspicion was that at round 0, with no case against yet argued, models apply
   that licence to their own priors. Two arms differing only in that sentence,
   12 con-assigned opening turns each, hand-read: **7/12 held con with the
   clause, 7/12 without it.** No difference.

Full detail, including the con-assigned excerpts that argue the pro case, is in
the decision record:
[`docs/superpowers/decisions/2026-08-06-debaters-do-not-hold-assigned-sides.md`](./superpowers/decisions/2026-08-06-debaters-do-not-hold-assigned-sides.md).

## 2. Build your own table

The three models above will be obsolete; the method to re-measure with today's
models is what keeps this page useful.

- **`measure_compliance` defaults to `True`** on `DebateSettings`. Leave it on
  and every judged turn gets read for the side its prose argues.
- Each turn's judged side lands in **`Turn.metadata["argued"]`** — `"pro"`,
  `"con"`, or `"neutral"`. Absence of the key means the turn was not measured
  (empty content, a failed judge call, an unparseable reply). It never means
  compliant — do not read a missing key as a held side.
- Run a debate to conclusion and read the outcome. A debater that argued
  against its assignment produces a line:

  ```
  Bob (assigned con) argued pro in 3 of 3 judged turns
  ```

  A debater that held its side produces no line, so a fully compliant debate
  shows nothing at all. Separately, if the chamber's winning position was
  `pro` or `con` and the assigned opposition was never judged to argue it, the
  outcome carries a caveat that the chamber never heard the other side.

- To build a table like §1 from your own roster: run debates with your
  candidate models seated on opposing sides, then run
  `backend/scripts/check_compliance_judge.py <chamber-id> [<chamber-id> ...]`
  against the concluded chambers. It re-judges every turn with the real
  `ComplianceJudge` and writes two **separate** files —
  `--turns-out` (Markdown: id, round, speaker, assigned stance, full turn text,
  no verdict) and `--verdicts-out` (JSON: turn id → verdict). Read the turns
  file and record your own answers before opening the verdicts file — see
  [§3](#judge-agreement) for why that split exists.

## 3. Methodological warnings

Two mistakes were made while producing the table in §1, and both generalise
past this experiment:

- **Small samples overstated the severity.** An initial spot check of 4
  con-assigned turns found 3 arguing the wrong side — a 75% failure rate. At
  n=12 per arm (the measurement behind the truth-seeking-clause test above),
  it was **5 in 12** — real, but less than half of what n=4 suggested. Treat
  any table built from a handful of turns as a rough bound, not a rate.
- **A keyword classifier was wrong by a factor of seven.** Written for this
  exact task — was the turn con? — it reported 1 of 12 con-assigned turns
  arguing the wrong side; hand-reading the same 12 turns found 7. It matched
  words like "essential" inside passages that were *rebutting* the pro case,
  not endorsing it. Automated prose classification failed the task the
  `ComplianceJudge` above exists to do properly. **Read the turns.**

## 4. Limits of the compliance record

<a id="judge-agreement"></a>

**This is one model's reading, not ground truth.** The `ComplianceJudge` is
itself a model call, and this whole page is about models being unreliable at
reading prose. Before the feature shipped, its verdicts were checked against
hand-reading: four real debates through `OllamaProvider`, 21 judged turns,
judge `llama3.1:latest`, hand-read first and committed to disk before the
verdict file was opened. **Agreement: 20 of 21 (95%).** The one disagreement
sat on the boundary of `neutral` — a turn endorsing nuclear power's inclusion
in a grid without endorsing its *centrality*, against a motion asking whether
it should be central — flagged as ambiguous during hand-reading before the
verdict was seen. Re-measure before trusting a compliance record produced by a
different judge model; the method is `check_compliance_judge.py`, described
above.

**Compliance is not quality.** `argued` records whether a debater's prose
supports pro, con, or neither — nothing about whether the argument was any
good. A debater that holds its assigned side with a weak argument reports as
compliant. A compliance table tells you who showed up on the correct side, not
who won.

**`neutral` is overloaded.** A turn that genuinely holds no position and a
turn that goes vague instead of switching sides both read `neutral`, and the
per-debater line in §2 only counts turns arguing the *polar opposite* of the
assignment — so a debater that drifts to `neutral` rather than crossing to the
other side produces no line at all, undercounting real non-compliance. This is
observed, not hypothetical: in a real two-round debate with `command-r` cast
as `con` (the 1/8 model above), it argued con in round 0 and drifted to
`neutral` in round 1 — never crossing to `pro`. `argued_against` stayed 0 and
no per-debater line appeared, even though the debater had visibly abandoned
its assigned side.

**One extra model call, and it happens before the turn is visible.** The judge
call runs after the turn is generated but before the `Turn` is appended and
streamed, so it adds latency to every judged turn — not just cost. Set
`measure_compliance: false` on `DebateSettings` to turn it off entirely: no
calls, no `argued` key, and any compliance caveat goes silent rather than
firing on stale or absent data.
