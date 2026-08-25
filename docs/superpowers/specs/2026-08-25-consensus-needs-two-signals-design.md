# Spec: a consensus stop needs two signals, not a better one

> **Status:** approved design, ready for planning.
> **Traceability:** FR-16 (stop conditions), FR-22 (convergence), FR-34 (compliance
> measurement). Backlog story **F11**, Epic F.
> **Context:** [`2026-08-06-chamber-scoped-positions-not-built.md`](../decisions/2026-08-06-chamber-scoped-positions-not-built.md)
> (§3 and §3b — the moderator meta-analysis mechanisms that failed),
> [`2026-08-06-stance-stability-is-not-convergence-design.md`](2026-08-06-stance-stability-is-not-convergence-design.md)
> (F7 — the same shape of fix, applied to `STANCES_STABLE`), and the
> `min_rounds` / poll-budget bug entries in the backlog dated 2026-08-25.

## Problem

`StopReason.CONSENSUS` fires when `is_consensus(deciding_stances(...))` holds over
one stance poll. That single signal is the debater's own answer about itself, and
it ended two real debates after four turns.

Both Colombia chambers (`1d419178`, `5e94e519`, 2026-08-25) ran one round and
stopped. The poll that ended them:

| debater | model | assigned | self-poll | judged from its prose |
|---|---|---|---|---|
| Donald | `qwen3:30b` | pro | con | **pro** (debate 1) / con (debate 2) |
| JD | `deepseek-r1:8b` | pro | *unreadable* | **pro** (both) |
| Barak | `muse-glimmer:30b-mlx` | con | con | con |
| Kamala | `command-r:latest` | con | con | con |

In debate 1 the poll reported three `con` votes and could not read the fourth, so
`deciding_stances` dropped JD as a phantom and `is_consensus` saw unanimity. The
compliance judge, reading the same four turns, scored the round **2–2**. The judge
is right: Donald's turn argues the motion ("*we're building Colombia's economic
engine*") and JD's rebuts him from the pro side.

The sharpest detail sits in debate 1's roster. It had no moderator configured, so
`_compliance_judge_for` fell back to the first participant's model — `qwen3:30b`,
Donald's own model. **The same model called that turn `pro` as an anonymous reader
and `con` as Donald.** The variable is not the model. It is whether the question is
*what does this text argue* or *what do you now believe*.

Two contributing defects were fixed separately on 2026-08-25 (`min_rounds`
defaulting to 1, and a 512-token poll budget that left `deepseek-r1:8b` unreadable
in both runs). Neither is the subject here. With both fixed the debates would have
run three rounds instead of one — but the poll would still have been the only thing
consulted, and it was wrong about half the roster.

## What was proposed, and what is being built instead

The proposal was to **replace** the self-report: let the moderator decide each
debater's stance, removing the bias of different models grading themselves.

The bias is real and already recorded. §3b of the position-registry decision
measured self-report agreement stratified by model — `qwen3:30b` 4.92/5,
`apertus:8b` 3.85/5, `command-r:latest` 3.69/5 — and concluded the outcome "would
be decided by whoever runs the strongest model rather than by whoever argued best."

Replacement still fails, for a reason the accuracy numbers hide. **The judge is the
better reader and the worse stop signal**, because the two signals answer different
questions: the poll asks about a standing position, the judge asks about one
speech. A round in which everyone happens to attack the same thing is a normal
event inside a live debate, not the end of one.

## The measurement

Every concluded chamber already stores both signals, so the candidate gates were
replayed over the real corpus — no model calls, no new runs.
`backend/scripts/replay_consensus_gate.py` reproduces all of it offline in under a
second:

```
python scripts/replay_consensus_gate.py --database-url sqlite:///./cicero.db
```

**16 concluded chambers carry both signals** (the rest ran before F9 or with
`measure_compliance` off). Judged sides are filtered exactly as `deciding_stances`
filters polled ones: muted debaters and unmeasured turns do not vote.

| gate | fires in | would cut short a debate that actually ran longer |
|---|---|---|
| `poll` (ships today) | 6 of 16 | 0 |
| `judge` (the replacement) | 10 of 16 | **8** |
| `poll` **and** `judge` | 2 of 16 | 0 |

**A judge-only gate is much worse than what ships.** It would have ended 8 of 16
debates early — the `Should the canton of Zürich secede` chamber at round 1 of the
8 it actually ran, the US-presidency chamber at round 2 of 8, two more at round 3
of 8, and the deadliest-pathogen chamber at round 4 of the 8 whose *eighth* round
is the one both signals actually agree on. Its per-turn accuracy is not in question (F9: 20/21 against hand-reading; F10:
25/32 on the harder fixture set). Its *stability* is: `argued` describes one
speech, and one round of agreement is not convergence. This is F7's finding
arriving a second time by a different route.

**The conjunction blocks exactly the bad stops and nothing else.** Of the 6
chambers where the poll fired:

| chamber | poll fired | judge agreed | verdict |
|---|---|---|---|
| Colombia (×2) | r1 | **no** — 2–2, 3–1 | blocked ✅ |
| Zürich secession | r1 | **no** — 3–1 | blocked ✅ |
| US presidency | r1 | **no** — 2–2 | blocked ✅ |
| Switzerland invades Italy | r1 | yes — 4–0 con | kept |
| Deadliest pathogen | r8 | yes — 5–0 pro | kept |

All four blocked stops are round-1 stops. Both kept stops are ones where an
independent reader confirms a single position held: the pathogen chamber ran its
full eight rounds, and the invasion chamber is the single-model roster written up
in [`model-selection.md`](../../model-selection.md) where *nobody argued the
motion*. **Zero legitimate stops are lost, and no `max_rounds` debate is affected
at all** — the poll never fired in those, so nothing changes.

### What this does not fix

The invasion chamber is the honest limit. Four instances of one model unanimously
declined the pro brief, and the gate keeps that stop because the judge correctly
reports one position in the room. A conjunction detects a **contradiction between
two readings**; it cannot detect a chamber that never contained a disagreement.
That failure is a roster property, and F9's caveat is what surfaces it.

## Design

Add the judge's read as a second, independent condition on the existing
`StopReason.CONSENSUS` branch in `orchestrator.py`. Nothing else changes: no new
stop reason, no new setting, no new model call — `measure_compliance` already
produces the `argued` values on every judged turn.

```
if is_consensus(deciding_stances(chamber, poll, report.unparsed)):
    if judged_consensus_available and not judged_consensus_agrees:
        # two readings of the same round contradict each other; keep debating
        pass
    else:
        stop_reason, final_stances = StopReason.CONSENSUS, poll
```

Four rules the implementation has to hold to:

1. **The judge is a veto, never a trigger.** A round the poll does not call
   consensus can never stop on the judge alone. This is what the 8 early stops in
   the table above buy.
2. **Absent evidence does not veto.** When `measure_compliance` is off, or no turn
   in the round was judged, the gate degrades to poll-only — otherwise turning off
   a *measurement* would silently disable a *stop condition*, and a chamber would
   run to `max_rounds` for a reason nothing on the page explains.
3. **Unmeasured turns do not vote**, mirroring `deciding_stances`. An unjudged turn
   is not agreement, and it is not disagreement either.
4. **Muted debaters do not vote**, on both signals, as FR-13 already requires.

`judged_stances` in the replay script is the reference implementation of rules 3
and 4; it belongs in `core/roster.py` beside `deciding_stances`, which is where the
identical rule for polls already lives, and it is pure enough for the mutation
gate.

### One requirement has to move

FR-34 currently ends *"and never used to decide an outcome."* That line was
written to keep a measurement from quietly becoming a judge, and it is the right
instinct. This design does not breach its purpose — the judge still picks no
winner, breaks no tie, and touches no decision rule; it can only *withhold* a stop
the poll proposed, and the poll alone still decides what the outcome then says.
But "decides nothing" (F9's status line) stops being literally true, and a
requirement that overstates a guarantee is worse than one that states a narrower
guarantee accurately.

FR-34 should be amended in the same change, to something like: *never used to
decide an outcome or to end a debate on its own; it may only withhold a stop
another signal proposed.* Do not build this without that edit — the traceability
is the point of the requirement.

### Recording it

A blocked stop must be visible or it is indistinguishable from a debate that simply
kept going. When the judge vetoes, record it on the round's `StancePoll` so the
stance history can render it and both exports can carry it. A reader who sees a
debate run past an apparent consensus is owed the reason.

## Alternatives rejected

| mechanism | why not |
|---|---|
| Replace the poll with the judge | 8 of 16 chambers cut short, one by 7 rounds (measured above) |
| Ask the moderator for structured stance/movement lines | Measured and failed: `qwen3:30b` narrated 900 tokens without emitting the format, `command-r` emitted perfect format and vacuous content (position-registry decision §3) |
| Self-consistency sampling of the poll | Measured and failed: model-stratified, uncorrelated with correctness, worse than greedy decode (§3b) |
| Reword `POLL_USER_INSTRUCTION` | Refuted three times; rewriting the poll degrades accuracy rather than improving it (§1, and `model-selection.md` §1) |
| Require N consecutive matching polls | F7 already found this insufficient for `STANCES_STABLE`: the debate it was meant to catch had three identical polls |
| Raise `min_rounds` alone | Shipped 2026-08-25 and necessary, but not sufficient — it delays the poll rather than checking it |

## Acceptance

- A chamber whose poll reports unanimity while the judged sides of that round
  disagree does **not** stop; it keeps debating and the veto is recorded.
- A chamber whose poll and judged sides agree stops exactly as it does today.
- A chamber with `measure_compliance: false` behaves exactly as it does today.
- A round with no judged turns behaves exactly as it does today.
- Replaying the corpus reproduces the table above: `both` fires in 2 of 16 and cuts
  no debate short.

## When to revisit

- **If the judge model changes.** All of this rests on `argued` being a decent read
  of one turn. F10 measured 25/32 on the harder fixtures with all 7 errors being
  pro↔con inversions **on rebuttals** — and a debate after round 0 is mostly
  rebuttals. Re-measure with `scripts/score_compliance_judge.py` before trusting a
  gate built on a different judge.
- **If debaters become frontier models.** The position-registry decision already
  names this condition: mechanisms that failed on constrained analytical output may
  simply work, at which point a richer stance signal beats a conjunction of two
  poor ones.
- **If the corpus grows.** 16 chambers, 6 poll firings and 2 surviving stops is a
  small sample. It is enough to reject judge-only replacement (8 failures is not
  noise) and enough to justify building the conjunction, but the "zero legitimate
  stops lost" claim rests on two chambers. Re-run the replay as runs accumulate.
