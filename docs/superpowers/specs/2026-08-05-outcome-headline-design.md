# Spec A — Meaningful outcome (F5)

> **Status:** approved design, ready for planning.
> **Traceability:** extends FR-23 (Consensus Statement). Backlog story **F5**, Epic F.
> **Restore point:** tag `v0.3.0` (`6a88197`) marks the state before this work.

## Problem

A concluded chamber currently reports its result as:

```markdown
## Outcome: majority
**Winning position:** neutral
```

Most debates end this way, and the line carries almost no information.

The cause is structural, not cosmetic. `CONVERGE_GUIDANCE` (`core/prompts.py:87`)
tells debaters that in the final phase they should concede, pick the strongest
position, and *"propose a concrete compromise if it would genuinely satisfy all
sides."* The stance poll (`POLL_USER_INSTRUCTION`, `core/prompts.py:98`) then
asks them to express the outcome of that as **one word from `{pro, con,
neutral}`**. A compromise is not expressible in that vocabulary, so it collapses
to `neutral`.

`neutral` therefore does not mean "the chamber is undecided". It means "the
chamber settled somewhere the poll cannot name". Reporting it as the winning
position presents a measurement limit as a result.

This spec does not widen the position vocabulary — that is Spec B (question
mode). It makes the outcome legible within the existing vocabulary, by adding
the one thing the stance word can never carry: a sentence saying what the
chamber actually concluded.

## Goals

- Every concluded debate carries a one-sentence, human-readable conclusion.
- The reader can see, at a glance, how firmly it was held and how it was decided.
- Already-concluded chambers benefit without re-running.
- The stance word remains visible as evidence, demoted from headline to detail.

## Non-goals

- Changing the position vocabulary, the decision rules, or the debate loop.
- Open-ended questions (Spec B).
- Any additional model call.

## Design

### 1. Data model

**Two stored fields.** `ConsensusResult` (`domain/models.py:159`) gains:

```python
#: One declarative sentence stating what the chamber concluded. Empty when the
#: moderator did not produce a usable one — never fabricated from the statement.
headline: str = Field(default="", max_length=500)

#: Ids whose final position could not be read, as passed to ``finalize``.
#: ``None`` means "not recorded" (chambers concluded before this field existed),
#: which is not the same as "none were unparsed" — see below.
unparsed: list[str] | None = None
```

Both defaults preserve backward compatibility: chambers persist as JSON
documents and `_Base` sets `extra="forbid"`, so an absent key must be tolerated
by a default rather than by a validator. Same contract the `style` →
`instructions` alias honours for pre-D7 chambers.

**Why `unparsed` must be stored rather than read back from history.** The
support line has to be computed over the same set `finalize()` decided on, and
`stance_history[-1].unparsed` is not reliably that set. `_record_poll`
(`orchestrator.py:520`) records a round once, so when a debate ends on
repetition or max-rounds the fresh final poll at `orchestrator.py:291` measures
a round already present in the history and is skipped. The last recorded poll is
then an *earlier* one, with a different `unparsed` set. `finalize()` already
receives `unparsed` as an argument — it simply needs to put it on the result.

**Three derived values**, in a new pure module `core/outcome.py`:

| Function | Reads | Produces |
|---|---|---|
| `support_summary` | `final_stances`, `outcome`, `unparsed`, roster | `contested — 2 of 3 debaters settled on neutral, 1 dissent` |
| `decision_basis` | `outcome`, `settings.decision_rule` | `majority of final positions` |
| `movements` | `stance_history` | `[Claude (pro→neutral), llama3 (con→neutral)]` |

Deriving rather than storing means **every already-concluded chamber gains
support, basis and movement retroactively.** Historical runs lack only the
headline (absent, handled in §2) and an exact `unparsed` set (approximated,
handled below).

`outcome.py` is a new file rather than more of `consensus.py`, which is already
261 lines and cohesive around polling and finalizing. It is pure, so it joins
the mutation-testing gate (see `docs/testing.md` §3.1).

#### `support_summary`

Computed over `deciding_stances(chamber, final_stances, consensus.unparsed)` —
the same filter the decision rule uses, so the reported support is exactly the
support that decided the outcome. Muted debaters and never-measured positions are
excluded by that function and therefore excluded here.

`N` is the number of deciding stances; `M` is how many of them hold the winning
stance; `K = N - M` is the dissent.

| Outcome | Text |
|---|---|
| `CONSENSUS` | `unanimous — all N debaters` |
| `MAJORITY` | `contested — M of N debaters settled on <stance>, K dissent` |
| `VERDICT` | `judge-decided — no majority among N debaters` |
| `DISAGREEMENT` | `unresolved — no position prevailed` |

When `deciding_stances` returns nothing, the text is `unmeasured — no debater's
final position could be read`. Never `0 of 3`, which would read as a measured
result of zero support.

Note this is narrower than it first appears: `deciding_stances` deliberately
falls back to the wider set rather than returning empty (`roster.py:75`), so
neither "every reply unparsed" nor "every debater muted" reaches this branch —
both resolve against the full roster, by design, so a fully-muted chamber still
resolves to *something*. The branch fires only when `final_stances` is itself
empty.

**Historical chambers**, where `consensus.unparsed is None`, fall back to
`stance_history[-1].unparsed` when a history exists, and to `()` otherwise. The
fallback is an approximation: for a chamber that ended on repetition or
max-rounds it may name a different poll's failures than the ones `finalize()`
saw, so a historical support line can differ slightly from the tally that
actually produced the stored outcome. New chambers are exact. This is worth
accepting rather than engineering around — the alternative is showing nothing
for every debate already run.

#### `decision_basis`

A pure mapping over `(outcome, decision_rule)`:

| Outcome | Text |
|---|---|
| `CONSENSUS` | `all debaters converged` |
| `MAJORITY` | `majority of final positions` |
| `VERDICT` | `moderator's verdict on argument strength` |
| `DISAGREEMENT` | `unresolved under the <rule> rule` |

#### `movements`

Compares each participant's **first and last measured** stance across
`stance_history`, and reports only those that differ.

A poll in which a participant's id appears in `StancePoll.unparsed` is skipped
for that participant — its value there was carried forward, not measured.
Reporting it would present a parse failure as someone changing their mind, which
is the exact confusion `unparsed` exists to prevent.

- A participant with fewer than two measured polls is omitted (no movement can
  be established).
- Muted participants are still reported, flagged `(muted)`. They are still
  polled by design (FR-13), so the record is continuous even though they do not
  vote — and where a muted debater ended up is interesting precisely because it
  did not count.

### 2. Producing the headline

Each of the four `MODERATOR_*_TASK` prompts gains a required opening line:

```
HEADLINE: <one declarative sentence stating what the chamber concluded>
```

No extra call — it rides along in the existing `ConsensusEngine.finalize()`
moderator call.

Per-outcome content, so the headline is a real summary in all four cases:

| Outcome | The headline states |
|---|---|
| `CONSENSUS` | the shared position, as a claim |
| `MAJORITY` | the prevailing position, as a claim |
| `VERDICT` | the position the judge ruled for |
| `DISAGREEMENT` | the crux that stayed unresolved |

A `DISAGREEMENT` headline is still a useful TL;DR — *"The chamber did not
converge: whether the onboarding cost is decisive was never settled."*

#### Parsing

`MODERATOR_JUDGE_TASK` already requires `WINNER:` as its first line, and
`parse_verdict` (`consensus.py:136`) reads it with a single
`partition("\n")`. A `VERDICT` reply now carries two directive lines, which that
parser cannot handle, and nesting two ordered parsers would make the order of
the two prompts load-bearing.

Instead, generalise:

```python
def parse_directives(text: str, keys: frozenset[str]) -> tuple[dict[str, str], str]:
    """Peel leading ``KEY: value`` lines off a moderator reply, in any order.

    Returns the directives found and the remaining body. Stops at the first line
    that is not a recognised directive, so ordinary prose is never consumed.
    """
```

`parse_verdict` and headline extraction both become thin callers. When no
directive line is present the whole text is returned as the body — `parse_verdict`'s
current contract, so its existing tests hold unchanged. `parse_directives` is
pure and joins the mutation gate.

#### Failure handling

- **No `HEADLINE:` directive** → `headline` stays `""`. Never fabricated, and
  never back-filled from the statement's first sentence: a summary the moderator
  did not write must not be presented as one it did.
- **Only the first line is taken.** A value longer than 500 characters is not a
  sentence, so it is **dropped entirely** rather than truncated — a half-sentence
  presented as the chamber's conclusion is worse than no headline. Same reasoning
  as `parse_stance` returning `None` instead of guessing.
- **Empty value** (`HEADLINE:` with nothing after it) → treated as absent.

### 3. Presentation

#### Markdown export

`export.py:82-88` becomes:

```markdown
## Outcome

**The chamber concluded:** Remote work should be the default for individual
contributors, but not during onboarding.

- **Support:** contested — 2 of 3 debaters settled on *neutral*, 1 dissent
- **How decided:** majority of final positions
- **Positions moved:** Claude (pro→neutral), llama3 (con→neutral)

<statement paragraph>
```

The stance word is not hidden — it moves from headline to evidence, inside the
support line where it is honest rather than uninformative. The standalone
`**Winning position:**` heading is removed.

The `Positions moved` bullet is omitted entirely when no movement can be
established (no history, or nothing changed).

#### JSON export

`headline` and `unparsed` arrive free inside `chamber.model_dump()`. The three derived values
are added as a **sibling** `outcome_summary` key, not merged into the model dump,
so `to_export_dict`'s chamber snapshot remains an exact representation of the
model. Safe because nothing consumes the JSON export as input — `chambers.py:581`
is write-only.

#### UI

`ChamberDetail.tsx:729-742`: the headline becomes the card's `<h2>`, the three
derived values a metadata row beneath it, the statement below that.

#### Compare

`compare.py:28` includes the headline per run. Two runs of one topic, each
summarised in a sentence, is what H4 is for.

#### Fallback

When `headline` is empty — a historical chamber, or a non-compliant moderator —
the display reverts to today's exact shape (`## Outcome: majority` plus
`**Winning position:** neutral`) with the three derived bullets still shown.
The result degrades to strictly-better-than-current, never to broken. This
applies identically to Markdown, JSON and the UI.

### 4. Testing

Per `docs/testing.md`, the pure additions are in the mutation gate.

**`parse_directives`** — no directives (whole text as body); `WINNER:` then
`HEADLINE:`; `HEADLINE:` then `WINNER:`; unrecognised key left in the body;
empty value; over-length value dropped; directive-only reply with no body;
leading whitespace.

**`support_summary`** — each of the four outcomes; muted debaters absent from
the denominator; an empty `final_stances` yielding `unmeasured`; all-muted and
all-unparsed resolving against the full roster rather than `unmeasured`, per
`deciding_stances`' fallback;
`consensus.unparsed is None` falling back to `stance_history[-1].unparsed`, and
to `()` when there is no history at all. One test must cover the divergent case
specifically: a chamber whose final poll was not recorded (ended on repetition
after an earlier poll of the same round) has `consensus.unparsed` used, *not*
the stale history entry.

**`decision_basis`** — four outcomes × three decision rules.

**`movements`** — empty history; a single poll; a participant unparsed in the
first poll, in the last, and in both; a muted participant (reported, flagged); a
stance that moved and moved back (reported only if first ≠ last); a participant
with no entry in an early poll.

**Engine** (`ConsensusEngine.finalize`, mock moderator) — emits `HEADLINE`;
omits it; emits both directives in either order; emits `HEADLINE` with an empty
statement body (existing `EMPTY_MODERATOR_STATEMENT` path must still apply);
and `unparsed` is stored on the result exactly as passed in, including the empty
case (`[]`, never `None`, for a freshly concluded chamber).

**Export** — golden Markdown with a headline and without (fallback);
`outcome_summary` present and correct in JSON.

**Persistence** — a pre-headline chamber JSON fixture loads with `headline == ""`
and `unparsed is None`. This is the regression that matters most given
`extra="forbid"`.

**Frontend** — outcome card renders the headline; falls back cleanly when absent.

**`make demo`** — the FR-23 acceptance check asserts a headline is present. This
requires the mock provider's moderator reply to emit `HEADLINE:`, so the
deterministic acceptance path exercises the real parser.

## Risks

**Moderator compliance.** A one-sentence constrained output is a bigger ask of a
small local model than the one-word `WINNER:` line the codebase already relies
on. The failure is contained — an absent headline falls back to today's display
— but if compliance is poor on Ollama models in practice, the mitigation is
prompt tuning, not a second call. Worth measuring on real runs before Spec B
builds on it.

**Headline quality.** A compliant but vacuous headline ("The debate covered
several perspectives") is worse than none, because it looks like a result. Not
detectable programmatically; the mitigation is explicit prompt wording demanding
a *claim*, and review on real debates.

## Out of scope

Widening the position vocabulary, open-ended questions, the answer registry and
the normalizer — all Spec B (Epic K), which builds on the headline this spec
introduces.
