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

## Open hypothesis, untested

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
