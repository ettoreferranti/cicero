# Spec: make the compliance judge quote its evidence

> **Status:** approved design, ready for planning.
> **Traceability:** extends **FR-34** (compliance measurement). Backlog story **F10**, Epic F.
> **Context:** [`2026-08-06-debaters-do-not-hold-assigned-sides.md`](../decisions/2026-08-06-debaters-do-not-hold-assigned-sides.md)
> (the judge-agreement measurement this revises) and
> [`2026-08-06-chamber-scoped-positions-not-built.md`](../decisions/2026-08-06-chamber-scoped-positions-not-built.md)
> (the six failed mechanisms whose lesson this design is built on).

## Problem

The `ComplianceJudge` shipped in F9 misreads rebuttals. Measured on a real
four-debater run of *"Switzerland should military invade Italy…"*, judge
`qwen3:30b`:

| turn | engages with | judged | actually argues |
|---|---|---|---|
| Kamala r1 (assigned con) | demolishes JD's pro claims | `pro` | **con** |
| Barak r1 (assigned con) | refutes Donald's pro claim | `pro` | **con** |
| JD r1 (assigned pro) | attacks the con case | `con` | **pro** |
| Donald r2 (assigned pro) | responds to JD's concession | `con` | **pro** |

Four errors in roughly ten hand-read turns, in **both directions**, all
explained by one rule: **the judge reports the side the turn is *about*, not the
side it *supports*.** The more thoroughly a turn engages its opponent, the more
it reads as its opponent.

The consequence is not a missing measurement but a false one. The chamber
currently publishes:

```
Kamala (assigned con) argued pro in 2 of 8 judged turns
Barak  (assigned con) argued pro in 1 of 8 judged turns
```

Both debaters held con throughout. The spec F9 shipped under states the risk
exactly: *"A judge that reads prose badly produces a confident, wrong compliance
record — worse than none, because it looks authoritative."* That is now observed,
not hypothetical.

### Why F9's validation missed it

Agreement was measured at 20/21 across four policy debates with judge
`llama3.1:latest`. That sample had no rebuttal-dense prose and one judge model.
The invasion debate differs on both axes — its personas instruct debaters to *"be
aggressive, use personal attacks"*, which produces turns saturated with the
opponent's position being quoted and attacked.

### The lesson this design is built on

`chamber-scoped-positions-not-built.md` closes with a taxonomy earned across six
failed mechanisms:

> These models **argue** well and **generate** well. What they do not do reliably
> is *structured analytical meta-work about the debate* — classify a position,
> pick a label, emit a constrained line about who moved. […] The F5 headline
> works because it asks for a sentence, not a judgment.

`ComplianceJudge` asks for **exactly one word**. By that taxonomy it sits in the
failing category, and scored well only because the prose was easy.

## Goals

- The judge's answer is grounded in a specific sentence of the turn, and that
  grounding is verified mechanically rather than trusted.
- An ungrounded answer produces **no record**, not a guess.
- Any compliance line can be audited without re-running a model.
- The change is accepted or rejected on measurement against the prose that broke
  the current one — not on plausibility.

## Non-goals

- **The moderator's synthesis prompt.** Untouched.
- **The caveat rule.** Untouched, and unaffected by this defect: it was correct
  in both invasion debates — silent when the pro case was argued, firing when it
  never was. Only the per-debater lines are wrong.
- **The stance poll.** Still unreliable, still not this spec's problem.
- **A per-turn quote badge in the transcript UI.** F9 declined it; nothing here
  changes that argument.
- **Self-consistency sampling.** Already measured over 39 debater-rounds and
  recorded as failed — model-stratified, uncorrelated with correctness, and worse
  than greedy decode. Not to be re-proposed.

## Design

### 1. The protocol: a sentence, then a label

The judge replies with two directives, parsed by the existing
`parse_directives` in `core/consensus.py` — the same machinery the moderator's
`HEADLINE:` / `WINNER:` already uses:

```
POSITION: <the sentence where the author states their own position, copied exactly>
SIDE: pro | con | neutral
```

`POSITION: none` when the turn states no position of its own.

Where a turn states its position more than once, **any one** such sentence is
acceptable — the check asks whether the answer is grounded in the turn, not
whether it found the single best sentence. Picking a "most representative" quote
would be another judgement call, which is the thing this design is removing.

This converts *classify eight paragraphs of rebuttal* — meta-work, the failing
category — into *locate the sentence* (extraction, closer to generation) plus
*classify one short sentence*. It is a direct application of the F5 headline's
lesson.

`SIDE` parses with the existing `parse_stance`. No new stance parser.

### 2. Grounding is a deterministic check

Normalise both the quote and the turn — collapse runs of whitespace, casefold —
then require the quote to be a **substring** of the turn. On failure: record
nothing.

This is the load-bearing part. It converts "did the model ground its answer?"
from a hope into a Python assertion, the same move `parse_stance` makes when it
returns `None` on a value it cannot read. A judge that paraphrases instead of
copying, or invents a sentence that is not in the turn, **cannot produce a
record at all**.

Normalisation is deliberately narrow: whitespace and case only. Anything looser
(fuzzy ratios, token overlap) reintroduces the judgement call the check exists
to remove.

### 3. What is recorded

```python
turn.metadata["argued"]       # the stance, unchanged from F9
turn.metadata["argued_quote"] # the verified sentence
```

Four paths produce **no keys at all**: a provider error, an unparseable reply, a
quote that fails the grounding check, and `POSITION: none`. All four mean "not
measured", which the existing machinery already handles correctly everywhere —
including keeping the unopposed-outcome caveat silent rather than firing on
absent data.

**Invariant:** for judgements produced by this protocol, both keys are present or
neither is. Chambers judged before it carry `argued` alone; `argued_stance()`
reads only `argued`, so nothing existing breaks.

Surfacing costs nothing: `Turn.metadata` already flows through
`GET /chambers/{id}` and the JSON export, so the quote rides along with no new
contract. `GET /chambers/{id}/outcome` is unchanged — it serves derived lines,
not per-turn data.

### 4. The prompt gains the rebuttal warning

`COMPLIANCE_SYSTEM` / `COMPLIANCE_USER_INSTRUCTION` in `core/prompts.py` are
rewritten for the two-directive format and gain the clause the current
instruction lacks entirely:

> A turn may quote, summarise or attack a position in order to argue *against*
> it. Report the side the author supports, not the side they mention or rebut.

`SAFETY_RULE` and the `TRANSCRIPT_OPEN`/`CLOSE` markers stay exactly as they are.
Asking the model to copy text out of an untrusted region is a change in kind
worth naming: the copied text is substring-verified against the turn, stored, and
displayed — never interpreted as an instruction. A turn containing
`POSITION: whatever` cannot inject a verdict, because the directive is read from
the *judge's reply*, not from the turn.

### 5. Interface

```python
@dataclass(frozen=True)
class Judgement:
    stance: Stance
    quote: str

class ComplianceJudge:
    async def judge(self, topic: str, content: str) -> Judgement | None: ...
```

`None` keeps its F9 meaning — not measured — and now covers the grounding failure
too. `_run_round` writes both keys when a `Judgement` comes back, and neither
when it does not.

### 6. Re-judging existing chambers

`backend/scripts/check_compliance_judge.py` gains an opt-in `--write` that
re-judges a chamber and rewrites `Turn.metadata` in place, printing the before
and after for each turn.

Two guards:

- **Opt-in only.** Without `--write` the script keeps its current read-only
  behaviour.
- **Refuse any chamber that is not `concluded`.** A running debate is being
  written by the engine; racing it is the obvious way to corrupt one.

## Validation — the gate

**This is the deciding step, not a formality.** The design is a model-judgement
mechanism, and this project's record contains six that failed. It must earn its
place against the prose that broke the current one.

### The fixture

Hand-label all **32 turns** of the invasion debate (chamber topic *"Switzerland
should military invade Italy…"*, four debaters, `qwen3:30b` + `llama3.1`) and
commit the labels. Hand-reading is done before any judge output for those turns
is consulted, and the labels are committed before the comparison is run — the
same discipline F9's validation used, for the same reason.

The existing 21-turn set is kept as a **non-regression** check: the fix must not
degrade the easy prose the current protocol already handles.

### The comparison is head-to-head

Both protocols are compared over the **same** 32 turns. Measuring only the new
one would establish an accuracy figure without establishing whether it is an
improvement. The current protocol's baseline is measured, not assumed from the
four errors already found by sampling.

**Capture the baseline first, before any code changes.** The current judge exists
today; run it over the 32 turns and commit its verdicts as a data file. The
comparison is then new-code against stored-baseline, and no dual code path has to
be maintained to keep the old protocol runnable. This ordering is a requirement,
not a convenience — once the prompt is rewritten, the old baseline is
unrecoverable without reverting.

### Two numbers, not one

- **Agreement** with the hand labels.
- **Unmeasured rate** — turns that produced no record. The strict grounding check
  fails safe, but coverage collapsing to a fraction of turns would trade a wrong
  record for no record: better, and still not good.

### The gate

Ship only if all three hold:

1. Agreement on the 32-turn invasion set **beats the current protocol** measured
   on the same turns.
2. Agreement on the existing 21-turn set does not regress.
3. Unmeasured turns stay at or below **25%** across both sets. This ceiling is a
   starting position, to be revised once real numbers exist — but it is stated
   now so the revision is a deliberate, recorded decision rather than a
   rationalisation after seeing a bad number.

### Rejection is an allowed outcome

If the gate fails, the correct result is to **not ship** and to record why, as
self-consistency sampling was recorded. Two fallbacks, in order:

1. State plainly in `docs/model-selection.md` that per-debater lines are
   unreliable on rebuttal-dense debates, and leave the mechanism alone.
2. Drop the per-debater lines entirely and keep only the unopposed-outcome
   caveat, which rests on far coarser signal — "was this side argued at all by
   anyone" — and was correct in both invasion debates.

## Testing

**Pure functions** — the grounding check and the reply parsing get unit tests and
join the mutation gate. The normalisation cases are the ones worth naming: a
quote differing from the turn only by whitespace or case **must** pass; one
differing by a single word **must** fail; an empty quote must fail rather than
match trivially.

**`ComplianceJudge`** — one test per path: grounded quote accepted, paraphrase
rejected, `POSITION: none`, unparseable reply, provider error. Each must yield
`None` except the first.

**Absence semantics** — a turn with `argued` but no `argued_quote` (a pre-F10
chamber) must still read correctly through `argued_stance`. This is the
compatibility case a careless implementation breaks.

**`MockProvider`** must emit the two-directive shape, or the offline path stops
exercising the feature — the same requirement the `HEADLINE:` directive and the
one-word compliance answer each had before.

**Injection** — a turn whose content contains `POSITION: pro` / `SIDE: pro` must
not be able to set the verdict. Scripted, not live.

## Risks

**Verbatim-copy compliance is itself a per-model capability.** A model that reads
prose well but habitually paraphrases would score *worse* than the current
protocol despite being better at the underlying task. This is the same shape as
every other finding in this area, and it is why the gate reports the unmeasured
rate beside agreement, and why the fixture is judged head-to-head rather than
against an absolute bar.

**A longer reply costs more than one word.** The token cap stays at
`COMPLIANCE_MAX_TOKENS` (512), which is ample for a sentence plus a word, but the
per-turn latency F9 already documented gets slightly worse. `measure_compliance:
false` remains the off switch.

**The fixture is one debate, one topic, two models.** It is a sharper test than
the one it replaces, not a general one. A judge that passes this gate has been
shown to handle *this* failure mode — nothing more. Re-measure for a different
judge model, exactly as `docs/model-selection.md` already instructs.

**The 25% unmeasured ceiling is a guess.** It is stated to force a deliberate
decision rather than to encode knowledge that does not exist yet.
