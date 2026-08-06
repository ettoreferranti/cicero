# Spec: the moderator becomes a configurable entity

> **Status:** approved design, ready for planning.
> **Traceability:** FR-1 (chamber creation), FR-22/23 (consensus), FR-12 (provider validation).
> Backlog story **F8**, Epic F.

## Problem

The moderator is the first debater:

```python
moderator_source = chamber.participants[0]
moderator = factory.get(moderator_source)  # impartial moderator = first participant
```

Three things are wrong with that, in ascending order of cost.

**The comment is false.** `participants[0]` is a debater with an assigned stance. Whatever
it is, it is not impartial, and the code says otherwise.

**It is silently order-dependent.** Whoever you add first becomes the arbiter — the model
that writes the headline (F5's primary artifact) and, on the default `judge` rule, breaks
ties by naming the winner. Nothing in the API or UI surfaces this.

**It silently inherits a token budget that does not work.** `_MODERATOR_MAX_TOKENS` is a
module constant set to `DEFAULT_MAX_TOKENS` (2048). Measured against the maintainer's own
setup, where `qwen3:30b` is the first participant in every chamber and therefore the
moderator:

| `max_tokens` | judge produced no readable `WINNER:` |
|---|---|
| **2048 (shipped)** | **5 of 9** |
| 4096 | 0 of 9 |
| 8192 | 0 of 9 |

Nine judge calls per budget across three real debates, through `OllamaProvider` with the
production `GenerateOptions` — including its retry-on-silence. The mechanism is
`done_reason: length`, `content: 0`, `thinking: ~10,000 characters`: the model exhausts
the budget reasoning and returns nothing. The provider retries with reasoning suppressed,
at which point it narrates instead of emitting the directive, so the retry converts
"empty" into "unparseable" rather than fixing it.

**So on the default decision rule, a tie currently fails to name a winner about half the
time**, and there is no way to fix it without editing source. F6's
`"judge-decided — the judge's ruling could not be read"` branch, added as a defensive
fallback, is in practice a coin flip.

### What this is *not* about

An earlier hypothesis was moderator bias — that a debater judging its own debate would
favour its own side. **Measured and not supported.** Running the judge task over three
debates with three moderator models, eight of nine parseable cells ruled `pro`, including
from the models assigned `con` and `neutral`. The verdict tracks the debate, not the
moderator's disposition. Impartiality is a reason to *name* the moderator honestly; it is
not the reason to build this.

## Goals

- The moderator is explicit, visible, and chosen rather than inherited from list order.
- Its token budget is configurable, with a default that works.
- A tie names a winner reliably.
- Moderator choice becomes a variable a comparison run can isolate.

## Non-goals

- Separate judge and synthesiser roles. The bias measurement gives no reason to split them.
- Blocking a moderator that is also a debater. With three local models there may be no
  alternative; a soft warning at most.
- Anything about the stance poll, the tally, or the `neutral` collapse. Unaffected.

## Design

### 1. The entity

```python
class Moderator(_Base):
    """Who writes the outcome. Not a debater: no stance, no turns, no vote."""

    provider: ProviderType
    model: str = Field(min_length=1, max_length=200)
    #: 2048 leaves a reasoning model no room to answer after thinking — measured at
    #: 5 failures in 9 judge calls. 4096 cleared all 9; 8192 bought nothing.
    max_tokens: int = Field(default=4096, gt=0, le=32768)
    #: Was an invisible module constant. A comparison run wanting reproducible
    #: verdicts can now set it to 0.
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
```

`Chamber` gains `moderator: Moderator | None = None`. `None` means "use the first
participant", which preserves today's behaviour for any caller that omits the field.

**Deliberately absent:** `stance` (it does not argue), `persona` and `instructions`
(`MODERATOR_SYSTEM` and the `MODERATOR_*_TASK` texts own its behaviour — an
operator-supplied persona on the thing writing the impartial synthesis is a footgun),
`muted` (it takes no turns). Reusing `Participant` would drag all four in and invite bugs
where something iterating the roster picks up the moderator.

### 2. Wiring

One production call site changes, in `_build_engine`:

```python
mod = chamber.moderator
if mod is None:
    # Legacy chambers, and any caller that omits the field: the pre-F8 behaviour.
    source = chamber.participants[0]
    mod = Moderator(provider=source.provider, model=source.model)
provider = factory.get_for_type(mod.provider)
options = GenerateOptions(
    model=mod.model, max_tokens=mod.max_tokens, temperature=mod.temperature
)
```

`get_for_type`, not `get` — the factory's `get` takes a `Participant` and the moderator is
not one. That is the small friction of not reusing `Participant`, and it is worth paying.

The engine is untouched: `ConsensusEngine` already takes a provider and options.

### 3. The judge retry

In `ConsensusEngine.finalize`, on the `VERDICT` path only:

```python
for _ in range(_JUDGE_ATTEMPTS):   # 3
    reply = parse_moderator_reply(result.content.strip())
    if reply.winner is not None:
        break
    result = await self._moderator.generate(messages, self._moderator_options)
```

This is **not** the provider's retry. That one fires on an *empty* reply and, as measured,
does not fix this — it re-asks with reasoning suppressed, and the model then narrates. This
one fires on a reply that arrived and carried no readable `WINNER:`.

Only `VERDICT` retries. The other three tasks have no `WINNER:` line to fail on, so
retrying them would cost calls for nothing. On exhaustion, `winning_stance` stays `None`
and F6's honest text renders unchanged.

At 4096 tokens the retry should rarely fire; it exists because the failure is stochastic,
not deterministic, and 0-of-9 is not 0-of-∞.

### 4. Default, validation, editability

**Default:** the creation form prefills the moderator from the first participant as it is
added, and shows it. Behaviour is unchanged for anyone who ignores the field. Visibility is
most of the fix — the real problem is that nobody knew a debater was the arbiter.

**Validation:** the moderator model gets the same availability check participants get
(C4/FR-12) on create and on edit — unreachable provider → `502`, model the provider cannot
serve → `422` naming what it can. Today nothing checks it, so a wrong moderator model fails
*after* the debate has run and been paid for.

**Editability:** editable while the chamber is a draft (D4), frozen once it has run, exactly
like the roster. That is what makes clone-and-swap-the-moderator work against the existing
compare endpoint (H4).

### 5. Surfacing

- **Markdown export:** named in the Participants section. A transcript that does not say who
  judged is missing something material.
- **JSON export:** free via the model dump.
- **`summarize_run` (compare):** carries the moderator, because "which moderator" is exactly
  the variable H4 exists to isolate.
- **UI:** a Moderator row above the roster on the creation form; on the detail page, beside
  the roster with the same `⚙` tuning-summary treatment, so a non-default budget or
  temperature is visible rather than buried.

## Testing

**Domain:** `max_tokens` and `temperature` bounds; a chamber with `moderator: None` still
resolves to the first participant.

**Wiring:** `_build_engine` uses the configured provider/model/options; falls back correctly
when absent; uses `get_for_type` so a moderator whose provider no debater uses still resolves.

**Judge retry** — the important ones:
- succeeds on a later attempt and records that winner;
- exhausts all attempts and leaves `winning_stance=None` with F6's text intact;
- **does not retry for CONSENSUS / MAJORITY / DISAGREEMENT.** Assert the provider call
  count, not just the outcome. Without this the retry silently costs three calls on every
  debate and no other test would notice.

**API:** accepted at creation; editable while draft; rejected once running; unreachable
provider → 502; unservable model → 422.

**Frontend:** the row renders, prefills from the first participant, and round-trips an edit.

## Risks

**Retrying widens non-determinism.** Two identical debates can resolve differently depending
on which attempt succeeded. This is already true at temperature 0.3; the retry widens it. The
mitigation is that `temperature` is now a field, so a comparison run can set it to 0.

**4096 is measured on one model and one machine.** `qwen3:30b` cleared 9 of 9, but a larger
reasoning model could still exhaust it. The retry is the backstop, and the field is
configurable — but the default is not a universal constant, and the spec should not pretend
otherwise.

**The default still inherits from `participants[0]`.** A user who never looks at the field
gets the old arrangement with a better token budget. That is the deliberate trade: no forced
migration, no broken API contract, and the visibility does the work. If chambers keep being
created with a weak first debater as judge, the next move is a server-level default
(`MODERATOR_MODEL`), not forcing the choice at creation.
