# Finding: debaters do not reliably hold an assigned opposing side

> **Status:** measured 2026-08-06. Open problem — no fix identified or shipped.
> **Why this matters:** the adversarial assignment is what makes Cicero a *debate*
> rather than several models agreeing. If it does not hold, a "unanimous consensus"
> may mean nobody argued the other side.

## What was observed

A five-debater run on *"should we teach basic programming to computer science
students at university, now that we can use GenAI to write code?"* concluded
`consensus`, `winning_stance: pro`, "unanimous — all 5 debaters on pro".

Reading the transcript, **every** debater argued for teaching programming — including
Bob, the only one assigned `con`. Bob had also inverted the motion, restating it in
his own words as:

> "The **motion to eliminate** teaching basic programming from university computer
> science curricula overlooks several key points"
>
> "Therefore, I maintain my position **against the motion**: teaching basic
> programming **should not be eliminated**"

The motion asks whether to *teach*; Bob opposed a proposal to *eliminate* teaching,
which is the pro position wearing a con label.

## First hypothesis: question phrasing — REFUTED

`STANCE_INSTRUCTION[CON]` says "Your starting position is AGAINST the motion." The
suspicion was that a question has no proposition to be "against", so the model
invents one and inverts it.

Measured across three models (`apertus:8b`, `llama3.1`, `command-r`), three samples
per cell, through `OllamaProvider` with production `GenerateOptions`. A con-assigned
debater argued one opening turn, then reported its own stance:

| topic | arm | held the assigned `con` side |
|---|---|---|
| question | shipped prompt | 3/9 |
| question | direction named explicitly, restatement forbidden | 5/9 |
| **proposition** | shipped prompt | **2/9** |

If phrasing were the cause, the proposition-phrased topic would hold con reliably. It
was **worse**. The hypothesis is not supported, and the candidate fix (3/9 -> 5/9 on
n=9) is within noise. Nothing was shipped.

## The finding, from prose rather than the poll

The metric above is confounded: it uses the stance poll, which is independently known
to be unreliable (see `2026-08-06-chamber-scoped-positions-not-built.md`). So the
opening turns were read directly, with no poll involved.

**Three of four con-assigned opening turns argue the pro case:**

- `apertus` — "teaching basic programming skills is **crucial**..."
- `command-r` — "it **cannot replace** the critical thinking skills developed through programming"
- `command-r` — "Learning basic programming is **essential** for computer science students"

Only one of four argued against. Note these did *not* invert the motion — no
"eliminate" restatement. They simply argued the other side. Bob's inversion is one
variant of the failure; plain non-compliance appears more common.

So this is not about phrasing, and not an artifact of the poll.

## Second hypothesis: the truth-seeking clause — REFUTED

`STANCE_INSTRUCTION` ends: "you are a truth-seeking debater, not a lawyer: if the case
against proves stronger, you are expected to say so and update your position." At round
0 there is no case against, so the suspicion was that models apply that licence to their
own priors.

Two arms differing only in that sentence, 12 con-assigned opening turns each, judged by
**reading the prose** (the stance poll is unreliable and confounded the previous attempt):

| model | shipped prompt | clause removed |
|---|---|---|
| `llama3.1:latest` | **4/4** held con | **4/4** held con |
| `apertus:8b` | 2/4 | 3/4 |
| `command-r:latest` | **1/4** | **0/4** |
| total | **7/12** | **7/12** |

No difference. Refuted.

## What it actually is: a per-model capability

The same table read by row rather than by column is the finding. `llama3.1` held the
assigned contrarian side 8 times out of 8; `command-r` held it 1 time out of 8. That gap
dwarfs any prompt effect measured here, and no prompt variant moved it.

**Whether a debate is genuinely adversarial therefore depends on which models are cast
as the opposition**, not on how the instruction is worded. In the five-debater run above,
the assigned `con` was `apertus` (5/8 here) and the roster also included `command-r`,
which almost never sustains a contrarian position. The "unanimous consensus" was
substantially a property of the roster.

Two corrections to earlier notes in this document:

- The severity was overstated. An initial spot check of 4 turns suggested 3 in 4 argued
  the wrong side; at n=12 per arm it is 5 in 12. The problem is real and smaller.
- A keyword classifier written for this experiment reported 1/12 where hand-reading gave
  7/12 — it matched words like "essential" from passages *rebutting* the pro case.
  Automated prose classification was wrong by a factor of seven here. Read the turns.

## Where to look next, if anywhere

Not `prompts.py`. Three prompt-level hypotheses have now been refuted by measurement
(poll transcript labels, question phrasing, the truth-seeking clause). The remaining
options are model-selection guidance, or **measuring compliance per debater** — "this
debater argued against its assigned side" is checkable against a known assignment for a
single turn, which is a far narrower judgement than the semantic classification tasks
these models have repeatedly failed.

## Judge agreement: 20 of 21 (measured 2026-08-10)

The second option was built (F9). Before shipping it, the judge was measured against
hand-reading, because this document already records a keyword classifier for this exact
task that was **wrong by a factor of seven**. A judge that reads prose badly produces a
confident, wrong compliance record — worse than none, because it looks authoritative.

Method: four real debates through `OllamaProvider`, 21 judged turns, judge
`llama3.1:latest`. `scripts/check_compliance_judge.py` writes the turns and the judge's
verdicts to **separate files**; the turns were hand-read first and the readings committed
to disk before the verdict file was opened. This split matters — the obvious single-table
design shows the reader the answer it is meant to be checking.

**Agreement: 20/21 (95%).** The one disagreement is instructive rather than alarming:

| turn | assigned | hand-read | judge |
|---|---|---|---|
| Eve, nuclear r1 | neutral | neutral | pro |

The turn is reproduced here so the disagreement can be re-examined rather than taken on
trust:

> While it's true that solar and wind projects can be deployed more quickly, I think
> we're underestimating the potential of hybrid approaches that integrate nuclear power
> with renewable energy sources. […] By exploring these innovative combinations, we might
> be able to reap the benefits of both nuclear and renewable energy technologies,
> creating a more resilient and adaptable grid.

Against the motion *"should nuclear power be **central** to decarbonisation?"* that is
genuinely contestable: it endorses nuclear's inclusion without endorsing its centrality.
The turn was flagged as ambiguous during hand-reading, before the verdict was seen.

So the residual disagreement sits exactly where this codebase's other measurement
problems sit — on the boundary of `neutral`, the label that means both "holds no
position" and "holds a position the vocabulary cannot name". The narrow task is
reliable; the overloaded word is not.

**This is one model on one machine, and it is not ground truth** — it is one reading
checked against another. Re-measure before trusting the compliance record from a
different judge model.

### An observation that is not a finding

Across those four debates `command-r:latest`, cast as `con`, held its assigned side in
**8 of 8** turns — against the 1-of-8 recorded above. The obvious difference is that here
it always spoke *after* a pro argument it could rebut, whereas the earlier measurement
used opening turns. That is a hypothesis, not a result: n=8, one model, four debates, and
this document's own history is of small samples overstating their case. Recorded so the
next person measures it rather than inherits either number as settled.

## Superseded: open hypothesis, untested

`STANCE_INSTRUCTION` ends: "you are a truth-seeking debater, not a lawyer: if the
case against proves stronger, you are expected to say so and update your position."

At round 0 there is no case against — the transcript is empty — yet models already
abandon the assigned side. They may be applying that licence to their own priors
rather than to anything argued in the debate. That would explain why phrasing makes
no difference.

**Untested.** Two hypotheses in this area have already been refuted by measurement
(the assigned-stance labels in the poll transcript, and question phrasing), so this
is recorded as a hypothesis and nothing more. The test to run: remove or condition
the truth-seeking clause for the opening round only, and judge by **reading the
prose**, not by the stance poll.

## Consequence for reading existing results

A `consensus` outcome cannot presently be taken as evidence that debaters converged.
It may equally mean the assigned opposition never materialised. The transcript is the
only reliable record of what was actually argued — which is what F6's caveat already
tells readers, for a different reason.

## See also

[`docs/model-selection.md`](../../model-selection.md) turns the measurements above into
guidance — the per-model table, how to rebuild it with the compliance record this
finding motivated, and the methodological warnings (small-sample overstatement, the
keyword classifier wrong by a factor of seven) restated for a reader who was not here
for the original measurement.
