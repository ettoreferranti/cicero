# Decision: do not build chamber-scoped position labels (Spec B1)

> **Status:** decided, 2026-08-06. Revisit only if the conditions in *When to revisit* change.
> **Supersedes:** the "Spec B — Question mode" sketch in
> [`2026-08-05-outcome-headline-design.md`](../specs/2026-08-05-outcome-headline-design.md).

## What was proposed

Replace the fixed `pro | con | neutral` vocabulary with **chamber-scoped position
labels** that accrete during a debate. A motion chamber would seed the three
familiar labels; debaters could mint a new one via a `NEW: <sentence>` reply in the
stance poll; a merge pass before the final tally would collapse duplicates; and
every decision rule would run over opaque labels instead of stances.

The motivating defect is real and reproduced. In a live three-model debate on
*"should we teach basic programming to CS students, now that we can use GenAI to
write code?"*, the debater **Bob** moved from opposing the motion to proposing a
specific compromise — teach fundamentals by hand first, introduce GenAI later for
optimisation — which the chamber then adopted as its conclusion. The stance poll has
no word for that, so Bob was recorded as `neutral`, and the outcome reported him as
the lone hold-out against the position he had authored.

## Why it is not being built

Five experiments against the maintainer's own Ollama models
(`qwen3:30b`, `MichelRosselli/apertus:8b-instruct-2509-bf16`, `command-r:latest`),
replaying that real debate. All used production decoding settings
(`temperature 0.0`, `num_predict 512`, `think: False`).

### 1. Debaters do not use an escape hatch — 0 of 3

Three arms: the shipped poll; the poll rewritten to present a position table plus a
`NEW:` option; and the shipped poll with only the `NEW:` sentence appended.

| debater | recorded | control | rewritten + `NEW:` | append-only + `NEW:` |
|---|---|---|---|---|
| Alice | pro | pro | pro | pro |
| Bob | neutral | neutral | **con** | neutral |
| Eve | pro | pro | **con** | pro |

**Nobody minted, in either treatment** — including Bob, whose position was precisely
the one the three words cannot express.

Two secondary results worth keeping:

- **Appending the escape hatch is harmless.** The append-only arm is identical to the
  control, so the mechanism costs nothing in accuracy. It is inert, not harmful.
- **Rewriting the poll's wording degrades accuracy.** Bob and Eve both flipped to
  `con` under the rewrite. The first uncontrolled run of this experiment looked like
  "the escape hatch makes stances worse"; the controlled run shows the *rewrite* did
  that, not the option. Anyone editing `POLL_USER_INSTRUCTION` should expect this.

### 2. Observer-style classification — inconclusive, test was broken

Showing a model a debater's own final turn and asking whether `pro`/`con`/`neutral`
covered it. `qwen3:30b` narrated instead of emitting the required line, and the test's
classifier counted any non-`NEW:` reply as a match — so all three "matches" were
format failures scored as successes. **This experiment proves nothing** and is
recorded only so it is not mistaken for evidence.

### 3. A structured movement narrative — format failure or vacuous compliance

Asking the moderator for `MOVED: <name> — <from, to>` lines:

- `qwen3:30b` never emitted the format; it narrated for 900 tokens and was truncated.
  Notably, its narration *reasoned correctly* — it observed that Bob "is actually in
  favor of teaching basic programming but with a caveat" and that the recorded label
  was `neutral`. The understanding was there; the constrained output was not.
- `command-r:latest` emitted perfect format and empty content:
  `MOVED: Bob — against the motion to in favour of a specific new proposal`.
  Which specific proposal, it does not say.

### 3b. Self-consistency sampling — tested later, also fails

Proposed after the above: leave the prompt alone, sample the poll N times at
temperature 0.7, and where no clear plurality emerges record the position as
*unmeasured* rather than inventing one — reusing the `unparsed` path, on the existing
principle that only real evidence should vote. It needs no prompt change, which is its
main appeal given the failures above.

Measured over 39 debater-rounds (three real debates, every round, every debater,
N=5) plus an earlier N=7 pass. It fails three ways:

**Agreement is stratified by model, not by answer.**

| model | n | mean agreement | range |
|---|---|---|---|
| `qwen3:30b` | 13 | 4.92 / 5 | 4-5 |
| `apertus:8b` | 13 | 3.85 / 5 | 3-5 |
| `command-r:latest` | 13 | 3.69 / 5 | 2-5 |

A "require 5/5" rule keeps 12 of 13 `qwen3` answers and 3 of 13 for each of the
others. Since `deciding_stances` excludes unmeasured debaters from the vote, the
outcome would be decided by whoever runs the strongest model rather than by whoever
argued best.

**Agreement does not predict correctness.** Scored against what each debater's final
turn argues: two wrong answers at 4/5 agreement (`command-r` in two debates, answering
`neutral` for a debater who proposed a mandatory course), and a right answer at 3/5.
The signal does not separate the cases it would need to separate.

**Sampling is worse than the shipped greedy decode.** For `command-r`, temperature-0
greedy returns the correct `pro` in both those cases; majority-of-5 at 0.7 returns
`neutral` at 4/5 confidence. The mechanism would add cost and noise to degrade answers
the current code already gets right.

### 4. The pattern

| mechanism | outcome |
|---|---|
| debaters self-label with `NEW:` | 0/3 used it |
| observer classifies a turn | test broken; no result |
| moderator emits structured movement lines | format failure, or compliant and vacuous |
| reworded poll (direction / disambiguation) | errors relocate, not removed; one confident inversion |
| stance labels removed from the poll transcript | worse — an unambiguously pro debater flipped to `con` |
| self-consistency sampling (§3b) | model-stratified, uncorrelated with correctness, worse than greedy |
| **F5 headline (shipped)** | **works on real runs** |

These models **argue** well and **generate** well. What they do not do reliably is
*structured analytical meta-work about the debate* — classify a position, pick a
label, emit a constrained line about who moved. Every mechanism the registry design
depends on sits in that second category. The F5 headline works because it asks for a
sentence, not a judgment.

### 5. The information was never missing

The decisive result. The **shipped** moderator statement, in the maintainer's real
debate, already reads:

> "Bob's revised position — advocating for core concepts through hands-on practice
> before using GenAI for optimization — aligned with this view, as it directly
> addressed Eve's warning about over-reliance on incomplete AI explanations and
> Alice's emphasis on cognitive friction as the bedrock of computational intuition."

That is Bob's compromise, named precisely, in production, today. Four experiments were
spent trying to add machinery to capture something the system was already capturing.

**Correction, added after further runs.** This conclusion was drawn too broadly. The
statement is *not* an independent read of the transcript: `build_moderator_messages`
feeds it `_stance_tally`, and `MODERATOR_MAJORITY_TASK` is formatted with the winning
stance, so the moderator is **told** what the tally says. When the tally is right the
statement inherits truth; when it is wrong it inherits the error. A later debate whose
tally resolved to `neutral` produced a statement opening *"The debate resolved in favor
of a neutral position…"* — parroting the label for what the transcript shows was a
substantive compromise everyone had converged on.

So "the statement already carries it" is a property of that run, not of the system. The
finding that survives is narrower: the transcript is the only artifact on the page that
is not downstream of the poll. That is why F6's caveat points readers there.

## What the defect actually is

Not a missing vocabulary — a **contradiction between the derived fields and the
statement**, with the fields printed above it in a compact, authoritative list:

```
**The chamber concluded:** …fundamentals must be taught before GenAI.   ← true
- **Support:** contested — 2 of 3 settled on pro (1 neutral)            ← Bob is the odd one out
- **Positions moved:** Bob (con→neutral)                                ← Bob became undecided
<statement> "Bob's revised position … aligned with this view"           ← Bob authored the winner
```

`con→neutral` is a correct record of the *poll*. It is a wrong description of *Bob*,
and nothing on screen says which of those two things it is. That is a much smaller
problem, and it is what the original complaint was actually about. It is specified in
[`2026-08-06-outcome-fields-vs-statement-design.md`](../specs/2026-08-06-outcome-fields-vs-statement-design.md).

## When to revisit

This decision rests on the debater tier, not on the design being wrong. Revisit if:

- **Debaters are frontier models.** The failure is instruction-following on
  constrained analytical output. A model that reliably emits `NEW: <sentence>` when
  asked would make self-labelling work as designed, and the rest of the registry
  follows.
- **A cheap deterministic signal for "this is a distinct position" appears** that does
  not require a model to classify anything.
- **Question mode is wanted for its own sake.** Open-ended questions ("PhD or
  finance?") genuinely have no pro/con axis, so they need *some* position vocabulary
  regardless of whether motions do. Note this decision does not evaluate question
  mode — it only rejects generalising motions onto a label registry. Question mode
  would face the same instruction-following problem and should be tested the same
  way before being built.

The experiment scripts are not committed (they live in a scratch directory), but they
are ~60 lines each and reproducible from the descriptions above against any chamber's
`GET /chambers/{id}/export?format=json`.
