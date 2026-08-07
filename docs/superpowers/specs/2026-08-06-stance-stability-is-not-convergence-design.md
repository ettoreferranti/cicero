# Spec: stance stability alone must not end a debate

> **Status:** approved design, ready for planning.
> **Traceability:** FR-16 (stop conditions), FR-22 (convergence). Backlog story **F7**, Epic F.
> **Context:** [`2026-08-06-chamber-scoped-positions-not-built.md`](../decisions/2026-08-06-chamber-scoped-positions-not-built.md)
> and the poll-reliability experiment recorded below.

## Problem

`orchestrator.py` ends a debate with `StopReason.STANCES_STABLE` when two consecutive
stance polls are identical during the convergence phase. That treats an unchanged poll
as evidence the chamber has finished arguing.

It is not. A real debate ended at **round 3 of a possible 8** with all three polls
byte-identical, while the transcript for that final round contained:

- Alice: *"I've reconsidered and now support Bob's core insight…"* — an announced reversal
- Bob: *"I agree with Alice's updated position… I support teaching basic programming"* —
  moving from opposition to explicit support
- Eve: endorsing the two-tiered proposal for the first time

The poll recorded `pro / neutral / neutral` in every round and saw none of it.

### The poll is an unreliable instrument, and that is the premise here

Measured across three real debates × three models, one sample each, production decoding
settings. Comparing each debater's poll answer with the position their own final turn
argues:

- The shipped wording answers `neutral` for debaters who take a clear side — including
  one whose turn reads *"I support teaching basic programming… it remains essential."*
  The models emit a bare `"neutral"`; `unparsed` is empty, so this is the model's answer,
  not a parse failure. They use `neutral` to mean *"my position is nuanced"*.
- Rewording does not fix it, it relocates the errors. A variant adding *"reply neutral
  ONLY if you genuinely hold no position"* scored better overall but turned that same
  debater into a confident `con` — the opposite of what he argued.
- Stripping the assigned-stance labels from the poll's transcript (a plausible anchoring
  cause) **made it worse**: a debater who is unambiguously pro across three debates
  flipped to `con` in two of them.

No tested wording is reliably better. This spec therefore does **not** try to fix the
poll. It stops a known-unreliable signal from being load-bearing for a decision it
cannot support.

## Goals

- A debate does not end early while debaters are still producing new arguments.
- The change's failure mode is "debates run longer", never "the record is wrong".

## Non-goals

- Changing the poll's wording, vocabulary, or parsing. See the evidence above; two
  prompt changes shipped on small measurements have already had to be reverted.
- Changing the decision rule, the outcome artifact, or anything about display.

## Design

### The fix

`STANCES_STABLE` gains a second condition: the round must also have produced **no new
argument**. Concretely, it may fire only when `round_all_repeated(chamber, round_index)`
is true — the same textual, deterministic check that already backs `StopReason.REPETITION`.

The two signals answer different questions, and only their conjunction means "finished":

| signal | question | reliable? |
|---|---|---|
| stance stability | has anyone's one-word answer changed? | **no** — see above |
| `round_all_repeated` | is anyone still saying anything new? | yes — pure text comparison, no model judgement |

In the failing debate, round 2 was substantively new for all three debaters, so
`round_all_repeated` is false and the debate would have continued.

Note this is a *conjunction*, not a reordering: `REPETITION` still fires on its own when
debaters repeat themselves without the poll settling, and that path is unchanged.

### Why not simply require more identical polls

The obvious fix — demand three consecutive matching polls instead of two — **would not
have helped**. That debate had three identical polls; the stop fired on the third. The
problem is not how many samples of the signal we take, it is that the signal does not
measure what the stop condition needs.

### `round_all_repeated` must be computed regardless of `stop_on_repetition`

Today that check is guarded by the `stop_on_repetition` setting. The setting exists so an
operator can choose to keep paying for rounds that add nothing; it is not a statement that
the *measurement* is unwanted. Compute the signal unconditionally (it is pure text, no
model call) and let `stop_on_repetition` gate only the `REPETITION` stop reason. Otherwise
turning that setting off would silently restore the bug this spec fixes.

### Considered and rejected

**Gating `StopReason.CONSENSUS` the same way.** Unanimity rests on the same unreliable
poll, so a chamber whose debaters all answer `neutral` for unrelated reasons would still
stop immediately. Rejected for now because it changes behaviour for every debate that
converges normally — the common, working path — to defend against a case not yet observed.
Worth revisiting if a debate is seen stopping on false unanimity.

**Making the conjunction configurable.** A setting whose "off" position restores a known
defect is not a useful knob. If the stricter rule proves too slow in practice, the honest
response is more evidence, not an escape hatch.

## Testing

**Mutation-gated** (`core/orchestrator.py` is in the gate):

- Identical polls **with** a new argument in the round → the debate **continues**. This is
  the regression test for the observed failure; it must fail against today's code.
- Identical polls **and** an all-repeated round → stops with `STANCES_STABLE`, as before.
- An all-repeated round with **changing** polls → stops with `REPETITION`, unchanged.
- `stop_on_repetition = False` with identical polls and a new argument → still continues,
  proving the measurement is not gated by the setting.
- `stop_on_repetition = False` with an all-repeated round → does **not** stop with
  `REPETITION` (the setting still does its job).
- The adversarial-phase branch (identical polls before convergence pulls `converge_start`
  forward) is unchanged.

Existing orchestrator tests that rely on `STANCES_STABLE` will need a repeating provider
to keep reaching it; `RepeatingProvider` in `tests/conftest.py` already exists for this.

## Risks

**Debates get longer and cost more.** A chamber whose debaters have genuinely settled but
keep producing fresh prose now runs to `max_rounds` (or a budget cap) instead of stopping
early. That is the intended trade — the budgets are the backstop, and they are enforced.
Worth measuring token cost on a few real debates after this ships.

**`round_all_repeated` becomes load-bearing for two decisions.** Its threshold
(`repetition_threshold`, default 0.95) now influences when a debate ends via a second
path. It is deterministic and already tested, but a chamber that lowers the threshold
aggressively will stop earlier in both paths, not just one.
