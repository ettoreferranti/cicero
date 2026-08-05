# Meaningful Outcome (F5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the uninformative `**Winning position:** neutral` with a moderator-written one-sentence conclusion, plus support/how-decided/who-moved values derived from data the engine already records.

**Architecture:** One extra prompt directive (`HEADLINE:`) on the existing moderator call — no new model call. Two new stored fields on `ConsensusResult` (`headline`, `unparsed`). A new pure module `core/outcome.py` derives three display strings from the chamber, so already-concluded debates benefit retroactively. A new `GET /chambers/{id}/outcome` endpoint serves those strings to the UI, keeping one implementation of the wording (mirroring the existing `/metrics` endpoint).

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI, pytest (`asyncio_mode = "auto"`), mutmut for backend mutation testing; React 18 + TypeScript + Vitest + Testing Library, Stryker for frontend mutation testing.

**Spec:** [`docs/superpowers/specs/2026-08-05-outcome-headline-design.md`](../specs/2026-08-05-outcome-headline-design.md)

## Global Constraints

- All backend commands run from `backend/`. All frontend commands run from `frontend/`.
- `ruff` line length is **100**; `select = ["E", "F", "I", "B", "C4", "UP", "S"]`.
- `mypy` runs in **strict** mode with `disallow_untyped_defs = true`. Every function needs full annotations, including tests (`-> None`).
- Every module starts with `from __future__ import annotations`.
- Domain models inherit `_Base`, which sets `extra="forbid"`, `str_strip_whitespace=True`, `validate_assignment=True`. **A new field must have a default**, or every chamber already in the database fails to load.
- New pure `core/` modules must be added to `paths_to_mutate` in `backend/setup.cfg`. The gate threshold is **80** (`make mutation`).
- Docstrings in this codebase explain *why*, not *what*. Match that density and tone.
- Never fabricate a headline. An absent or unusable one is stored as `""` and the display falls back.
- Full local gate: `make check` (lint, type, coverage, mutation) from `backend/`.

---

### Task 1: Directive parsing for moderator replies

Today `parse_verdict` reads a single leading `WINNER:` line with `partition("\n")`. Verdict replies will now carry two directive lines, so parsing must handle several in any order.

**Files:**
- Modify: `backend/cicero/core/consensus.py:136-150` (replace `parse_verdict`), `:29` (imports), `:1-15` (module docstring)
- Modify: `backend/cicero/core/prompts.py:132` (replace `VERDICT_WINNER_PREFIX`)
- Modify: `backend/cicero/domain/models.py` (new `MAX_HEADLINE_LENGTH` constant)
- Modify: `backend/tests/test_consensus.py:13` (import), `:224-239` (rewrite verdict tests)

**Interfaces:**
- Consumes: `parse_stance(text) -> Stance | None` (existing, `consensus.py:98`)
- Produces:
  - `prompts.DIRECTIVE_HEADLINE: str = "HEADLINE"`, `prompts.DIRECTIVE_WINNER: str = "WINNER"`
  - `domain.models.MAX_HEADLINE_LENGTH: int = 500` — lives in the domain, not in
    `prompts`, because Task 2 makes it a field constraint. `core` may depend on
    `domain`; the reverse would invert the layering.
  - `consensus.parse_directives(text: str, keys: frozenset[str]) -> tuple[dict[str, str], str]`
  - `consensus.ModeratorReply` — frozen dataclass with `headline: str`, `winner: Stance | None`, `body: str`
  - `consensus.parse_moderator_reply(text: str) -> ModeratorReply`
- `parse_verdict` is **removed**. It has no callers outside `finalize` and its own tests (verified: `grep -rn parse_verdict backend/cicero backend/tests backend/scripts`), and keeping it as a wrapper would leave dead code.

**Behaviour change to be explicit about:** today `parse_verdict("WINNER: maybe\ntext")` returns the *whole* text as the body, because an unparseable winner leaves the line unconsumed. Under the new single rule — a recognised leading `KEY:` line is always consumed, and usability only decides whether a value is extracted — the body becomes `"text"`. This is deliberate: `WINNER: maybe` is a failed directive, not content, and rendering it at the top of a verdict statement is worse than dropping it. The existing test is updated accordingly.

The `body = rest or whole text` fallback **is** preserved: `"WINNER: pro"` with no body still yields the full text as body, so a winner-only reply does not collapse to `EMPTY_MODERATOR_STATEMENT`.

- [ ] **Step 1: Write the failing tests**

Replace `backend/tests/test_consensus.py:224-239` with:

```python
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", {}),
        ("Just prose.", {}),
        ("WINNER: pro\nBody.", {"WINNER": "pro"}),
        ("winner: CON\nBody.", {"WINNER": "CON"}),
        ("HEADLINE: We should go.\nWINNER: pro\nBody.",
         {"HEADLINE": "We should go.", "WINNER": "pro"}),
        # Order must not matter: the two prompts are written independently.
        ("WINNER: pro\nHEADLINE: We should go.\nBody.",
         {"WINNER": "pro", "HEADLINE": "We should go."}),
        ("  HEADLINE: Indented.\nBody.", {"HEADLINE": "Indented."}),
        ("HEADLINE:\nBody.", {"HEADLINE": ""}),
        # An unrecognised key is prose, and stops the peel.
        ("VERDICT: pro\nHEADLINE: never reached.", {}),
        # A directive after prose is prose.
        ("Body.\nHEADLINE: too late.", {}),
    ],
)
def test_parse_directives_peels_leading_keys(text: str, expected: dict[str, str]) -> None:
    directives, _ = parse_directives(text, frozenset({"HEADLINE", "WINNER"}))
    assert directives == expected


@pytest.mark.parametrize(
    ("text", "body"),
    [
        ("WINNER: pro\nBody.", "Body."),
        ("HEADLINE: One.\nWINNER: pro\nBody one.\nBody two.", "Body one.\nBody two."),
        ("Just prose.", "Just prose."),
        # Nothing left after the directives: the caller needs *something*, so the
        # original text is returned rather than an empty statement.
        ("WINNER: pro", "WINNER: pro"),
    ],
)
def test_parse_directives_returns_remaining_body(text: str, body: str) -> None:
    _, remaining = parse_directives(text, frozenset({"HEADLINE", "WINNER"}))
    assert remaining == body


@pytest.mark.parametrize(
    ("text", "winner", "body"),
    [
        ("WINNER: pro\nStrong case.", Stance.PRO, "Strong case."),
        ("winner: CON\nThe cons had it.", Stance.CON, "The cons had it."),
        ("WINNER: neutral\nNobody moved.", Stance.NEUTRAL, "Nobody moved."),
        ("No verdict line here.", None, "No verdict line here."),
        # A failed directive is not content: it is consumed, and no winner read.
        ("WINNER: maybe\ntext", None, "text"),
    ],
)
def test_parse_moderator_reply_reads_the_winner(
    text: str, winner: Stance | None, body: str
) -> None:
    reply = parse_moderator_reply(text)
    assert reply.winner is winner
    assert reply.body == body


def test_parse_moderator_reply_without_body_keeps_full_text() -> None:
    reply = parse_moderator_reply("WINNER: pro")
    assert reply.winner is Stance.PRO
    assert reply.body == "WINNER: pro"


def test_parse_moderator_reply_reads_the_headline() -> None:
    reply = parse_moderator_reply("HEADLINE: Remote work should be the default.\nBecause X.")
    assert reply.headline == "Remote work should be the default."
    assert reply.body == "Because X."


def test_parse_moderator_reply_without_a_headline_reports_none() -> None:
    assert parse_moderator_reply("Just the statement.").headline == ""


def test_parse_moderator_reply_ignores_an_empty_headline() -> None:
    assert parse_moderator_reply("HEADLINE:\nStatement.").headline == ""


def test_parse_moderator_reply_drops_an_overlong_headline() -> None:
    # A moderator that put its whole statement on the HEADLINE line has not
    # written a headline. Dropping the value (rather than truncating it) is what
    # makes the display fall back instead of showing half a sentence as the
    # chamber's conclusion.
    reply = parse_moderator_reply("HEADLINE: " + "x" * 501 + "\nStatement.")
    assert reply.headline == ""
    assert reply.body == "Statement."


def test_parse_moderator_reply_takes_only_the_first_line_as_headline() -> None:
    reply = parse_moderator_reply("HEADLINE: One sentence.\nThe longer statement.\nMore.")
    assert reply.headline == "One sentence."
    assert reply.body == "The longer statement.\nMore."
```

Update the import block at `backend/tests/test_consensus.py:13` — remove `parse_verdict`, add `parse_directives` and `parse_moderator_reply`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_consensus.py -v -k "directive or moderator_reply"`
Expected: FAIL — `ImportError: cannot import name 'parse_directives'`

- [ ] **Step 3: Replace the prompt constant**

In `backend/cicero/core/prompts.py`, replace line 132 (`VERDICT_WINNER_PREFIX = "WINNER:"`) with:

```python
#: Directive keys the moderator may open its reply with, peeled off by
#: ``consensus.parse_moderator_reply``. Bare keys — the ``:`` is the parser's.
DIRECTIVE_WINNER = "WINNER"
DIRECTIVE_HEADLINE = "HEADLINE"
```

Then add the cap to `backend/cicero/domain/models.py`, next to the existing
module-level `DEFAULT_MAX_TOKENS` constant (line 48-54, same style):

```python
#: A headline is one sentence. A longer value means the moderator wrote its
#: statement on the wrong line, so the value is dropped rather than truncated —
#: half a sentence presented as the chamber's conclusion is worse than none.
MAX_HEADLINE_LENGTH = 500
```

It belongs in the domain because Task 2 makes it a field constraint on
`ConsensusResult`. Putting it in `core/prompts.py` would force `domain` to import
from `core`, inverting the layering.

- [ ] **Step 4: Implement the parser**

In `backend/cicero/core/consensus.py`, change the import at line 29 to:

```python
from cicero.core.prompts import (
    DIRECTIVE_HEADLINE,
    DIRECTIVE_WINNER,
    EMPTY_MODERATOR_STATEMENT,
)
from cicero.domain.models import Chamber, ConsensusResult, MAX_HEADLINE_LENGTH
```

(`consensus.py:32` already imports `Chamber, ConsensusResult` from that module —
extend the existing line rather than adding a second import.)

Replace `parse_verdict` (lines 136-150) with:

```python
#: Every directive key the moderator may open a reply with.
MODERATOR_DIRECTIVES = frozenset({DIRECTIVE_HEADLINE, DIRECTIVE_WINNER})


@dataclass(frozen=True)
class ModeratorReply:
    """A moderator reply split into its directives and its prose body."""

    #: One declarative sentence, or ``""`` when none was given or it was unusable.
    headline: str
    #: The judge's winner, or ``None`` outside a verdict (or when unreadable).
    winner: Stance | None
    #: The statement itself, with the directive lines removed.
    body: str


def parse_directives(text: str, keys: frozenset[str]) -> tuple[dict[str, str], str]:
    """Peel leading ``KEY: value`` lines off a reply, in any order.

    The moderator prompts are written independently, so the order the directives
    arrive in must not be load-bearing. Peeling stops at the first line that is
    not a recognised directive, which is what keeps ordinary prose — including a
    sentence that happens to contain a colon — out of the result.

    Returns the directives found (keys upper-cased) and the remaining body. When
    nothing remains, the original text is returned as the body: a reply that was
    *only* a directive still has to yield a statement rather than an empty one.
    """
    stripped = text.strip()
    remaining = stripped.split("\n")
    found: dict[str, str] = {}
    while remaining:
        key, separator, value = remaining[0].strip().partition(":")
        candidate = key.strip().upper()
        if not separator or candidate not in keys or candidate in found:
            break
        found[candidate] = value.strip()
        remaining = remaining[1:]
    body = "\n".join(remaining).strip()
    return found, body or stripped


def parse_moderator_reply(text: str) -> ModeratorReply:
    """Split a moderator reply into its headline, winner and statement body.

    A recognised leading directive line is always consumed; whether its *value*
    is usable only decides whether a value is extracted. An unusable directive is
    a failed instruction, not content, and rendering ``WINNER: maybe`` at the top
    of a statement would be worse than dropping it.
    """
    directives, body = parse_directives(text, MODERATOR_DIRECTIVES)
    headline = directives.get(DIRECTIVE_HEADLINE, "")
    if len(headline) > MAX_HEADLINE_LENGTH:
        headline = ""
    winner_word = directives.get(DIRECTIVE_WINNER)
    winner = parse_stance(winner_word) if winner_word else None
    return ModeratorReply(headline=headline, winner=winner, body=body)
```

Update the module docstring at `consensus.py:12-14`: replace `parse_verdict` with `parse_directives`, `parse_moderator_reply` in the list of mutation-gated pure parts.

- [ ] **Step 5: Update the one caller**

In `finalize` (`consensus.py:253-255`), replace:

```python
        if outcome is ConsensusOutcome.VERDICT:
            winner, body = parse_verdict(statement)
            statement = body or EMPTY_MODERATOR_STATEMENT
```

with:

```python
        reply = parse_moderator_reply(statement)
        statement = reply.body or EMPTY_MODERATOR_STATEMENT
        if outcome is ConsensusOutcome.VERDICT:
            winner = reply.winner
```

(`reply.headline` is wired into the result in Task 3 — this step only keeps the existing behaviour green.)

- [ ] **Step 6: Run the full backend suite**

Run: `cd backend && pytest -q`
Expected: PASS. If `test_consensus.py` or `test_orchestrator.py` fail on statement text, check that the `body or EMPTY_MODERATOR_STATEMENT` fallback survived.

- [ ] **Step 7: Lint and type-check**

Run: `cd backend && make lint && make type`
Expected: both clean.

- [ ] **Step 8: Commit**

```bash
cd backend && git add cicero/core/consensus.py cicero/core/prompts.py tests/test_consensus.py
git commit -m "refactor: parse moderator directives in any order (F5)"
```

---

### Task 2: Store the headline and the unparsed set

**Files:**
- Modify: `backend/cicero/domain/models.py:159-170` (`ConsensusResult`)
- Test: `backend/tests/test_domain_models.py`

**Interfaces:**
- Produces: `ConsensusResult.headline: str` (default `""`, max 500), `ConsensusResult.unparsed: list[str] | None` (default `None`)

`unparsed` must be stored rather than read back from `stance_history`: `_record_poll` (`orchestrator.py:520`) records each round once, so a debate ending on repetition or max-rounds takes a fresh final poll (`orchestrator.py:291`) that is *not* persisted. The last history entry is then a different poll with a different unparsed set. `None` means "not recorded" (chambers concluded before this field existed) and is not the same as `[]`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_domain_models.py`:

```python
def test_consensus_result_defaults_to_no_headline_and_unrecorded_unparsed() -> None:
    result = ConsensusResult(outcome=ConsensusOutcome.CONSENSUS, statement="Agreed.")
    assert result.headline == ""
    # None is "never recorded", which is not the same claim as "none failed".
    assert result.unparsed is None


def test_consensus_result_accepts_a_headline_and_an_unparsed_list() -> None:
    result = ConsensusResult(
        outcome=ConsensusOutcome.CONSENSUS,
        statement="Agreed.",
        headline="The office should be kept at 21 degrees.",
        unparsed=["abc"],
    )
    assert result.headline == "The office should be kept at 21 degrees."
    assert result.unparsed == ["abc"]


def test_consensus_result_rejects_an_overlong_headline() -> None:
    with pytest.raises(ValidationError):
        ConsensusResult(
            outcome=ConsensusOutcome.CONSENSUS, statement="Agreed.", headline="x" * 501
        )


def test_consensus_result_loads_a_chamber_persisted_before_the_headline_existed() -> None:
    # Chambers persist as JSON and _Base forbids extra keys, so backward
    # compatibility rests entirely on these defaults. This is the regression that
    # would take out every debate already in the database.
    legacy = {
        "outcome": "majority",
        "statement": "The majority prevailed.",
        "winning_stance": "neutral",
        "final_stances": {},
    }
    result = ConsensusResult.model_validate(legacy)
    assert result.headline == ""
    assert result.unparsed is None
```

Ensure the file imports `pytest`, `ValidationError` from `pydantic`, and `ConsensusOutcome` / `ConsensusResult`; add whichever are missing.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_domain_models.py -v -k "consensus_result"`
Expected: FAIL — `ValidationError: Extra inputs are not permitted [type=extra_forbidden]` on `headline`.

- [ ] **Step 3: Add the fields**

In `backend/cicero/domain/models.py`, inside `ConsensusResult`, after `winning_stance` (line 167):

```python
    #: One declarative sentence stating what the chamber concluded — the TL;DR a
    #: stance word cannot carry. Empty when the moderator produced none that was
    #: usable; never fabricated from the statement.
    headline: str = Field(default="", max_length=MAX_HEADLINE_LENGTH)
    #: Ids whose final position could not be read, exactly as passed to
    #: ``ConsensusEngine.finalize``. ``None`` means *not recorded* — a chamber
    #: concluded before this field existed — which is a different claim from
    #: ``[]`` ("every position was read"). Kept here rather than derived from
    #: ``stance_history`` because the final poll is not always recorded there.
    unparsed: list[str] | None = None
```

`MAX_HEADLINE_LENGTH` is already defined at module level in `models.py` by Task 1 — no import needed.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_domain_models.py -v -k "consensus_result"`
Expected: PASS

- [ ] **Step 5: Run the full suite, lint and types**

Run: `cd backend && pytest -q && make lint && make type`
Expected: all clean.

- [ ] **Step 6: Commit**

```bash
cd backend && git add cicero/domain/models.py cicero/core/prompts.py tests/test_domain_models.py
git commit -m "feat: store the outcome headline and the unparsed set (F5)"
```

---

### Task 3: Ask the moderator for a headline and record it

**Files:**
- Modify: `backend/cicero/core/prompts.py:110-131` (the four `MODERATOR_*_TASK` texts)
- Modify: `backend/cicero/core/consensus.py` (`finalize`, ~line 246-261)
- Test: `backend/tests/test_consensus.py`

**Interfaces:**
- Consumes: `parse_moderator_reply(text) -> ModeratorReply` (Task 1); `ConsensusResult.headline`, `ConsensusResult.unparsed` (Task 2)
- Produces: `finalize` returns a `ConsensusResult` carrying `headline` and `unparsed`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_consensus.py`. Use the existing `ScriptedProvider` from `tests/conftest.py`, whose `moderator_reply` argument sets the moderator's text:

```python
async def test_finalize_records_the_moderator_headline() -> None:
    ada = make_participant("Ada", Stance.PRO)
    zeno = make_participant("Zeno", Stance.PRO)
    chamber = make_chamber(ada, zeno)
    provider = ScriptedProvider(
        moderator_reply="HEADLINE: Mars should wait.\nThe cost case was decisive."
    )
    engine = ConsensusEngine(ConstantFactory(provider), provider, GenerateOptions(model="m"))

    result = await engine.finalize(
        chamber, {str(ada.id): Stance.PRO, str(zeno.id): Stance.PRO}
    )

    assert result.headline == "Mars should wait."
    assert result.statement == "The cost case was decisive."


async def test_finalize_leaves_the_headline_empty_when_the_moderator_omits_it() -> None:
    ada = make_participant("Ada", Stance.PRO)
    zeno = make_participant("Zeno", Stance.PRO)
    chamber = make_chamber(ada, zeno)
    provider = ScriptedProvider(moderator_reply="They agreed on the cost case.")
    engine = ConsensusEngine(ConstantFactory(provider), provider, GenerateOptions(model="m"))

    result = await engine.finalize(
        chamber, {str(ada.id): Stance.PRO, str(zeno.id): Stance.PRO}
    )

    # Never back-filled from the statement: a summary the moderator did not write
    # must not be presented as one it did.
    assert result.headline == ""
    assert result.statement == "They agreed on the cost case."


async def test_finalize_records_the_unparsed_set_it_decided_on() -> None:
    ada = make_participant("Ada", Stance.PRO)
    zeno = make_participant("Zeno", Stance.CON)
    chamber = make_chamber(ada, zeno)
    provider = ScriptedProvider(moderator_reply="HEADLINE: Unclear.\nNo agreement.")
    engine = ConsensusEngine(ConstantFactory(provider), provider, GenerateOptions(model="m"))

    result = await engine.finalize(
        chamber, {str(ada.id): Stance.PRO, str(zeno.id): Stance.CON}, unparsed=[str(zeno.id)]
    )

    assert result.unparsed == [str(zeno.id)]


async def test_finalize_records_an_empty_unparsed_set_rather_than_none() -> None:
    ada = make_participant("Ada", Stance.PRO)
    zeno = make_participant("Zeno", Stance.PRO)
    chamber = make_chamber(ada, zeno)
    provider = ScriptedProvider(moderator_reply="HEADLINE: Agreed.\nBoth sides aligned.")
    engine = ConsensusEngine(ConstantFactory(provider), provider, GenerateOptions(model="m"))

    result = await engine.finalize(
        chamber, {str(ada.id): Stance.PRO, str(zeno.id): Stance.PRO}
    )

    # A freshly concluded chamber always knows; only historical ones say None.
    assert result.unparsed == []


async def test_finalize_reads_a_verdict_that_also_carries_a_headline() -> None:
    ada = make_participant("Ada", Stance.PRO)
    zeno = make_participant("Zeno", Stance.CON)
    chamber = make_chamber(ada, zeno)
    chamber.settings.decision_rule = DecisionRule.JUDGE
    provider = ScriptedProvider(
        moderator_reply="HEADLINE: The pro case won.\nWINNER: pro\nBetter evidence."
    )
    engine = ConsensusEngine(ConstantFactory(provider), provider, GenerateOptions(model="m"))

    result = await engine.finalize(
        chamber, {str(ada.id): Stance.PRO, str(zeno.id): Stance.CON}
    )

    assert result.outcome is ConsensusOutcome.VERDICT
    assert result.winning_stance is Stance.PRO
    assert result.headline == "The pro case won."
    assert result.statement == "Better evidence."
```

Add any missing imports to the test module: `ConsensusEngine`, `GenerateOptions`, `DecisionRule`, `ConsensusOutcome`, and `ConstantFactory` / `ScriptedProvider` / `make_chamber` / `make_participant` from `tests.conftest`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_consensus.py -v -k "finalize_records or finalize_leaves or finalize_reads"`
Expected: FAIL — `assert '' == 'Mars should wait.'` (the headline is parsed but never stored).

- [ ] **Step 3: Add the directive to the four moderator prompts**

In `backend/cicero/core/prompts.py`, add above `MODERATOR_CONSENSUS_TASK` (line 110):

```python
#: Prepended to every moderator task. The headline is the one thing a stance word
#: cannot carry — "neutral" is what a compromise collapses to, not what it says.
#: Demanding a *claim* is deliberate: a compliant but vacuous headline ("the
#: debate covered several perspectives") looks like a result and is worse than none.
MODERATOR_HEADLINE_DIRECTIVE = (
    "Your reply MUST begin with a line of exactly 'HEADLINE: <one sentence>'. That "
    "sentence must be a concrete claim a reader could agree or disagree with — the "
    "single thing this chamber concluded — not a description of what was discussed. "
    "Then continue with the rest of your reply on the following lines."
)
```

Append the per-outcome guidance to each of the four task texts. Replace `MODERATOR_CONSENSUS_TASK`, `MODERATOR_DISAGREEMENT_TASK`, `MODERATOR_MAJORITY_TASK` and `MODERATOR_JUDGE_TASK` (lines 110-131) with:

```python
MODERATOR_CONSENSUS_TASK = (
    "The participants have converged. Write a single CONSENSUS STATEMENT (one short "
    "paragraph) that captures the shared position they can all endorse.\n\n"
    f"{MODERATOR_HEADLINE_DIRECTIVE} Here the headline states the shared position."
)
MODERATOR_DISAGREEMENT_TASK = (
    "The participants did NOT reach consensus. Write a concise SUMMARY OF "
    "DISAGREEMENT: the main positions, the key points of contention (cruxes), and "
    "what remains unresolved.\n\n"
    f"{MODERATOR_HEADLINE_DIRECTIVE} Here the headline names the crux that stayed "
    "unresolved — for example 'The chamber did not converge: whether the onboarding "
    "cost is decisive was never settled.'"
)
MODERATOR_MAJORITY_TASK = (
    "A majority of participants — though not all — settled on the position "
    "'{winner}'. Write a single RESOLUTION (one short paragraph): state the "
    "winning position and the strongest reasons it prevailed, then briefly note "
    "the remaining dissent.\n\n"
    f"{MODERATOR_HEADLINE_DIRECTIVE} Here the headline states the prevailing position."
)
MODERATOR_JUDGE_TASK = (
    "The participants did not settle on a single position. Acting as the JUDGE, "
    "decide which position won on the strength of the arguments alone. After the "
    "headline line, your reply MUST carry a line of exactly 'WINNER: pro', "
    "'WINNER: con', or 'WINNER: neutral', followed by a short VERDICT paragraph "
    "justifying the decision and noting the strongest losing argument.\n\n"
    f"{MODERATOR_HEADLINE_DIRECTIVE} Here the headline states the position you ruled for."
)
```

`MODERATOR_MAJORITY_TASK` keeps its `{winner}` placeholder — it is `.format(winner=...)`-ed at `consensus.py:179`. The `f` prefix on that string interpolates `MODERATOR_HEADLINE_DIRECTIVE` at import time and leaves `{winner}` alone **only because it is in a non-f segment**; keep the `{winner}` segment in the plain (non-`f`) part exactly as shown.

- [ ] **Step 4: Store the headline and unparsed set**

In `backend/cicero/core/consensus.py`, `finalize`, replace the block from `task = _moderator_task(...)` through the `return` with:

```python
        task = _moderator_task(outcome, winner)
        messages = build_moderator_messages(chamber, stances, task)
        result = await self._moderator.generate(messages, self._moderator_options)
        reply = parse_moderator_reply(result.content.strip())
        statement = reply.body.strip() or EMPTY_MODERATOR_STATEMENT
        if outcome is ConsensusOutcome.VERDICT:
            winner = reply.winner
        return ConsensusResult(
            outcome=outcome,
            statement=statement,
            headline=reply.headline,
            winning_stance=winner,
            final_stances=dict(stances),
            unparsed=list(unparsed),
        )
```

Note `unparsed: Iterable[str] = ()` is already a parameter of `finalize` (line 236) — `list(unparsed)` is why a fresh chamber records `[]` rather than `None`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_consensus.py -v`
Expected: PASS

- [ ] **Step 6: Run the full suite**

Run: `cd backend && pytest -q && make lint && make type`
Expected: all clean. `test_orchestrator.py` exercises `finalize` end-to-end; if a test asserts an exact statement string, the moderator fixture's reply now passes through the directive parser unchanged (it has no directive lines), so it should be unaffected.

- [ ] **Step 7: Commit**

```bash
cd backend && git add cicero/core/prompts.py cicero/core/consensus.py tests/test_consensus.py
git commit -m "feat: moderator writes a one-sentence headline for every outcome (F5)"
```

---

### Task 4: Derive support, basis and movement

**Files:**
- Create: `backend/cicero/core/outcome.py`
- Create: `backend/tests/test_outcome.py`
- Modify: `backend/cicero/core/__init__.py` (re-exports)
- Modify: `backend/setup.cfg` (`paths_to_mutate`)

**Interfaces:**
- Consumes: `deciding_stances(chamber, stances, unparsed) -> dict[str, Stance]` (`core/roster.py:51`); `ConsensusResult.unparsed` (Task 2)
- Produces:
  - `OutcomeSummary` — frozen dataclass: `headline: str`, `support: str`, `decided_by: str`, `movements: tuple[str, ...]`
  - `summarize_outcome(chamber: Chamber) -> OutcomeSummary | None` (`None` when the chamber has no consensus)
  - `support_summary(chamber: Chamber) -> str`
  - `decision_basis(chamber: Chamber) -> str`
  - `movements(chamber: Chamber) -> tuple[str, ...]`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_outcome.py`:

```python
"""Tests for the derived outcome summary (F5)."""

from __future__ import annotations

from cicero.core.outcome import (
    decision_basis,
    movements,
    summarize_outcome,
    support_summary,
)
from cicero.domain.enums import ConsensusOutcome, DecisionRule, Stance
from cicero.domain.models import ConsensusResult, StancePoll
from tests.conftest import make_chamber, make_participant


def _concluded(
    outcome: ConsensusOutcome,
    stances: dict[str, Stance],
    winner: Stance | None = None,
    unparsed: list[str] | None = None,
    headline: str = "",
):  # type: ignore[no-untyped-def]
    """A chamber with three debaters and a recorded consensus."""
    ada = make_participant("Ada", Stance.PRO)
    zeno = make_participant("Zeno", Stance.CON)
    kant = make_participant("Kant", Stance.NEUTRAL)
    chamber = make_chamber(ada, zeno, kant)
    keyed = dict(zip([str(p.id) for p in chamber.participants], stances.values()))
    chamber.consensus = ConsensusResult(
        outcome=outcome,
        statement="Statement.",
        headline=headline,
        winning_stance=winner,
        final_stances=keyed,
        unparsed=unparsed,
    )
    return chamber


def test_no_consensus_yields_no_summary() -> None:
    chamber = make_chamber(make_participant("Ada", Stance.PRO))
    assert summarize_outcome(chamber) is None


def test_support_reports_unanimity() -> None:
    chamber = _concluded(
        ConsensusOutcome.CONSENSUS,
        {"a": Stance.PRO, "b": Stance.PRO, "c": Stance.PRO},
        winner=Stance.PRO,
        unparsed=[],
    )
    assert support_summary(chamber) == "unanimous — all 3 debaters"


def test_support_reports_a_contested_majority_with_its_dissent() -> None:
    chamber = _concluded(
        ConsensusOutcome.MAJORITY,
        {"a": Stance.NEUTRAL, "b": Stance.NEUTRAL, "c": Stance.CON},
        winner=Stance.NEUTRAL,
        unparsed=[],
    )
    assert support_summary(chamber) == (
        "contested — 2 of 3 debaters settled on neutral, 1 dissent"
    )


def test_support_reports_a_judge_verdict() -> None:
    chamber = _concluded(
        ConsensusOutcome.VERDICT,
        {"a": Stance.PRO, "b": Stance.CON, "c": Stance.NEUTRAL},
        winner=Stance.PRO,
        unparsed=[],
    )
    assert support_summary(chamber) == "judge-decided — no majority among 3 debaters"


def test_support_reports_disagreement_as_unresolved() -> None:
    chamber = _concluded(
        ConsensusOutcome.DISAGREEMENT,
        {"a": Stance.PRO, "b": Stance.CON, "c": Stance.NEUTRAL},
        unparsed=[],
    )
    assert support_summary(chamber) == "unresolved — no position prevailed"


def test_support_excludes_a_muted_debater_from_the_denominator() -> None:
    chamber = _concluded(
        ConsensusOutcome.CONSENSUS,
        {"a": Stance.PRO, "b": Stance.PRO, "c": Stance.PRO},
        winner=Stance.PRO,
        unparsed=[],
    )
    chamber.participants[2].muted = True
    # A muted debater does not vote (FR-13), so it cannot pad the support count.
    assert support_summary(chamber) == "unanimous — all 2 debaters"


def test_support_says_unmeasured_when_nothing_was_recorded() -> None:
    chamber = _concluded(ConsensusOutcome.DISAGREEMENT, {}, unparsed=[])
    chamber.consensus.final_stances = {}
    # Never "0 of 3", which reads as a measured result of zero support.
    assert support_summary(chamber) == (
        "unmeasured — no debater's final position could be read"
    )


def test_support_prefers_the_recorded_unparsed_set_over_a_stale_poll() -> None:
    # The final poll is not always written to stance_history (orchestrator.py:520
    # records a round once), so the last poll can name different failures than the
    # ones finalize() actually decided on. The recorded set wins.
    chamber = _concluded(
        ConsensusOutcome.CONSENSUS,
        {"a": Stance.PRO, "b": Stance.PRO, "c": Stance.PRO},
        winner=Stance.PRO,
        unparsed=[],
    )
    ada = chamber.participants[0]
    chamber.stance_history = [
        StancePoll(round_index=0, stances={}, unparsed=[str(ada.id)]),
    ]
    assert support_summary(chamber) == "unanimous — all 3 debaters"


def test_support_falls_back_to_the_last_poll_for_a_historical_chamber() -> None:
    chamber = _concluded(
        ConsensusOutcome.CONSENSUS,
        {"a": Stance.PRO, "b": Stance.PRO, "c": Stance.PRO},
        winner=Stance.PRO,
        unparsed=None,
    )
    ada = chamber.participants[0]
    # Never measured anywhere, so deciding_stances drops it.
    chamber.stance_history = [StancePoll(round_index=0, stances={}, unparsed=[str(ada.id)])]
    assert support_summary(chamber) == "unanimous — all 2 debaters"


def test_support_for_a_historical_chamber_without_history_counts_everyone() -> None:
    chamber = _concluded(
        ConsensusOutcome.CONSENSUS,
        {"a": Stance.PRO, "b": Stance.PRO, "c": Stance.PRO},
        winner=Stance.PRO,
        unparsed=None,
    )
    assert support_summary(chamber) == "unanimous — all 3 debaters"


def test_decision_basis_per_outcome() -> None:
    stances = {"a": Stance.PRO, "b": Stance.PRO, "c": Stance.PRO}
    assert (
        decision_basis(_concluded(ConsensusOutcome.CONSENSUS, stances, winner=Stance.PRO))
        == "all debaters converged"
    )
    assert (
        decision_basis(_concluded(ConsensusOutcome.MAJORITY, stances, winner=Stance.PRO))
        == "majority of final positions"
    )
    assert (
        decision_basis(_concluded(ConsensusOutcome.VERDICT, stances, winner=Stance.PRO))
        == "moderator's verdict on argument strength"
    )


def test_decision_basis_names_the_rule_that_failed_to_resolve() -> None:
    for rule, expected in [
        (DecisionRule.UNANIMOUS, "unresolved under the unanimous rule"),
        (DecisionRule.MAJORITY, "unresolved under the majority rule"),
        (DecisionRule.JUDGE, "unresolved under the judge rule"),
    ]:
        chamber = _concluded(ConsensusOutcome.DISAGREEMENT, {"a": Stance.PRO})
        chamber.settings.decision_rule = rule
        assert decision_basis(chamber) == expected


def test_movements_reports_who_changed_between_first_and_last_measurement() -> None:
    chamber = _concluded(
        ConsensusOutcome.MAJORITY,
        {"a": Stance.NEUTRAL, "b": Stance.NEUTRAL, "c": Stance.NEUTRAL},
        winner=Stance.NEUTRAL,
        unparsed=[],
    )
    ada, zeno, kant = chamber.participants
    chamber.stance_history = [
        StancePoll(
            round_index=0,
            stances={str(ada.id): Stance.PRO, str(zeno.id): Stance.CON,
                     str(kant.id): Stance.NEUTRAL},
        ),
        StancePoll(
            round_index=1,
            stances={str(ada.id): Stance.NEUTRAL, str(zeno.id): Stance.CON,
                     str(kant.id): Stance.NEUTRAL},
        ),
    ]
    assert movements(chamber) == ("Ada (pro→neutral)",)


def test_movements_is_empty_without_history() -> None:
    chamber = _concluded(ConsensusOutcome.DISAGREEMENT, {"a": Stance.PRO})
    assert movements(chamber) == ()


def test_movements_needs_two_measurements() -> None:
    chamber = _concluded(ConsensusOutcome.DISAGREEMENT, {"a": Stance.PRO})
    ada = chamber.participants[0]
    chamber.stance_history = [StancePoll(round_index=0, stances={str(ada.id): Stance.PRO})]
    assert movements(chamber) == ()


def test_movements_ignores_polls_that_could_not_be_read() -> None:
    chamber = _concluded(ConsensusOutcome.DISAGREEMENT, {"a": Stance.PRO})
    ada = chamber.participants[0]
    # The second value was carried forward, not measured. Reporting it would
    # present a parse failure as someone changing their mind.
    chamber.stance_history = [
        StancePoll(round_index=0, stances={str(ada.id): Stance.PRO}),
        StancePoll(round_index=1, stances={str(ada.id): Stance.CON}, unparsed=[str(ada.id)]),
    ]
    assert movements(chamber) == ()


def test_movements_ignores_a_stance_that_moved_and_moved_back() -> None:
    chamber = _concluded(ConsensusOutcome.DISAGREEMENT, {"a": Stance.PRO})
    ada = chamber.participants[0]
    chamber.stance_history = [
        StancePoll(round_index=0, stances={str(ada.id): Stance.PRO}),
        StancePoll(round_index=1, stances={str(ada.id): Stance.CON}),
        StancePoll(round_index=2, stances={str(ada.id): Stance.PRO}),
    ]
    assert movements(chamber) == ()


def test_movements_ignores_polls_a_debater_was_absent_from() -> None:
    chamber = _concluded(ConsensusOutcome.DISAGREEMENT, {"a": Stance.PRO})
    ada, zeno = chamber.participants[0], chamber.participants[1]
    # Zeno joined late: an early poll has no entry for it at all, which is not a
    # measurement and must not become the start of a trajectory.
    chamber.stance_history = [
        StancePoll(round_index=0, stances={str(ada.id): Stance.PRO}),
        StancePoll(round_index=1, stances={str(ada.id): Stance.PRO,
                                           str(zeno.id): Stance.CON}),
    ]
    assert movements(chamber) == ()


def test_movements_flags_a_muted_debater() -> None:
    chamber = _concluded(ConsensusOutcome.DISAGREEMENT, {"a": Stance.PRO})
    ada = chamber.participants[0]
    ada.muted = True
    chamber.stance_history = [
        StancePoll(round_index=0, stances={str(ada.id): Stance.PRO}),
        StancePoll(round_index=1, stances={str(ada.id): Stance.NEUTRAL}),
    ]
    # Still polled (FR-13), so still reported — and where it ended up is
    # interesting precisely because it did not count.
    assert movements(chamber) == ("Ada (pro→neutral, muted)",)


def test_summarize_outcome_bundles_the_headline_with_the_derived_values() -> None:
    chamber = _concluded(
        ConsensusOutcome.CONSENSUS,
        {"a": Stance.PRO, "b": Stance.PRO, "c": Stance.PRO},
        winner=Stance.PRO,
        unparsed=[],
        headline="Mars should wait.",
    )
    summary = summarize_outcome(chamber)
    assert summary is not None
    assert summary.headline == "Mars should wait."
    assert summary.support == "unanimous — all 3 debaters"
    assert summary.decided_by == "all debaters converged"
    assert summary.movements == ()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_outcome.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cicero.core.outcome'`

- [ ] **Step 3: Implement the module**

Create `backend/cicero/core/outcome.py`:

```python
"""Derived, human-readable outcome facts (F5, FR-23).

The stance word alone is not a result: ``CONVERGE_GUIDANCE`` invites debaters to
propose a compromise, and the one-word poll has no way to name one, so a
compromise collapses to ``neutral``. The headline (written by the moderator, on
``ConsensusResult``) says what was concluded; this module says how firmly it was
held, how it was decided, and who moved to get there.

Everything here is **derived**, not stored, so a chamber concluded before F5
existed gains all three values without re-running. Pure and deterministic — part
of the mutation-testing gate.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from cicero.core.roster import deciding_stances
from cicero.domain.enums import ConsensusOutcome
from cicero.domain.models import Chamber

UNMEASURED = "unmeasured — no debater's final position could be read"
UNRESOLVED = "unresolved — no position prevailed"

_BASIS: dict[ConsensusOutcome, str] = {
    ConsensusOutcome.CONSENSUS: "all debaters converged",
    ConsensusOutcome.MAJORITY: "majority of final positions",
    ConsensusOutcome.VERDICT: "moderator's verdict on argument strength",
}


@dataclass(frozen=True)
class OutcomeSummary:
    """Everything a reader needs at a glance, above the full statement."""

    headline: str
    support: str
    decided_by: str
    movements: tuple[str, ...]


def _effective_unparsed(chamber: Chamber) -> tuple[str, ...]:
    """The unparsed set the outcome was decided on.

    Prefers the set recorded on the result, which is exact. Chambers concluded
    before that field existed fall back to the last recorded poll — an
    approximation, because a debate that ended on repetition or max-rounds took a
    final poll that ``_record_poll`` did not persist. Approximating beats showing
    nothing for every debate already run.
    """
    consensus = chamber.consensus
    if consensus is not None and consensus.unparsed is not None:
        return tuple(consensus.unparsed)
    if chamber.stance_history:
        return tuple(chamber.stance_history[-1].unparsed)
    return ()


def support_summary(chamber: Chamber) -> str:
    """How firmly the winning position was held, in the reader's words."""
    consensus = chamber.consensus
    if consensus is None:
        return ""
    deciding = deciding_stances(
        chamber, consensus.final_stances, _effective_unparsed(chamber)
    )
    if not deciding:
        return UNMEASURED
    total = len(deciding)
    if consensus.outcome is ConsensusOutcome.CONSENSUS:
        return f"unanimous — all {total} debaters"
    winner = consensus.winning_stance
    if winner is None:
        return UNRESOLVED
    if consensus.outcome is ConsensusOutcome.VERDICT:
        return f"judge-decided — no majority among {total} debaters"
    held = Counter(deciding.values())[winner]
    return (
        f"contested — {held} of {total} debaters settled on {winner.value}, "
        f"{total - held} dissent"
    )


def decision_basis(chamber: Chamber) -> str:
    """Which rule produced the outcome."""
    consensus = chamber.consensus
    if consensus is None:
        return ""
    basis = _BASIS.get(consensus.outcome)
    if basis is not None:
        return basis
    return f"unresolved under the {chamber.settings.decision_rule.value} rule"


def movements(chamber: Chamber) -> tuple[str, ...]:
    """Who changed position between their first and last *measured* poll.

    Polls where a debater's reply could not be read are skipped for that debater:
    the value there was carried forward, and reporting it would present a parse
    failure as someone changing their mind — the exact confusion
    ``StancePoll.unparsed`` exists to prevent.
    """
    moved: list[str] = []
    for participant in chamber.participants:
        key = str(participant.id)
        measured = [
            poll.stances[key]
            for poll in chamber.stance_history
            if key in poll.stances and key not in poll.unparsed
        ]
        if len(measured) < 2 or measured[0] is measured[-1]:
            continue
        # Muted debaters are still polled (FR-13), so they still have a
        # trajectory — flagged, because it did not count toward the outcome.
        suffix = ", muted" if participant.muted else ""
        moved.append(
            f"{participant.display_name} "
            f"({measured[0].value}→{measured[-1].value}{suffix})"
        )
    return tuple(moved)


def summarize_outcome(chamber: Chamber) -> OutcomeSummary | None:
    """The full derived summary, or ``None`` when the debate has no outcome yet."""
    consensus = chamber.consensus
    if consensus is None:
        return None
    return OutcomeSummary(
        headline=consensus.headline,
        support=support_summary(chamber),
        decided_by=decision_basis(chamber),
        movements=movements(chamber),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_outcome.py -v`
Expected: PASS (all 18)

- [ ] **Step 5: Re-export and register in the mutation gate**

In `backend/cicero/core/__init__.py`, add alongside the existing export re-exports:

```python
from cicero.core.outcome import (
    OutcomeSummary,
    decision_basis,
    movements,
    summarize_outcome,
    support_summary,
)
```

and add `"OutcomeSummary"`, `"decision_basis"`, `"movements"`, `"summarize_outcome"`, `"support_summary"` to `__all__`, keeping it alphabetically sorted as it already is.

In `backend/setup.cfg`, append `,cicero/core/outcome.py` to the end of the `paths_to_mutate=` line.

- [ ] **Step 6: Verify the mutation gate covers the new module**

Run: `cd backend && make lint && make type && pytest -q`
Expected: all clean.

Run: `cd backend && make mutation`
Expected: score ≥ 80. If mutants survive in `outcome.py`, they will be in the string literals or the `len(measured) < 2` boundary — add the specific assertion that kills each rather than loosening the threshold.

- [ ] **Step 7: Commit**

```bash
cd backend && git add cicero/core/outcome.py cicero/core/__init__.py setup.cfg tests/test_outcome.py
git commit -m "feat: derive outcome support, basis and stance movement (F5)"
```

---

### Task 5: Render the outcome in both exports

**Files:**
- Modify: `backend/cicero/core/export.py:1-16` (imports/docstring), `:82-88` (outcome block), `:14-16` (`to_export_dict`)
- Test: `backend/tests/test_export.py`

**Interfaces:**
- Consumes: `summarize_outcome(chamber) -> OutcomeSummary | None` (Task 4); `ConsensusResult.headline` (Task 2)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_export.py`:

```python
def _concluded_with_headline():  # type: ignore[no-untyped-def]
    chamber = _chamber_with_debate()
    ada, zeno = chamber.participants
    chamber.consensus = ConsensusResult(
        outcome=ConsensusOutcome.MAJORITY,
        statement="The majority prevailed.",
        headline="Mars should wait for cheaper launch costs.",
        winning_stance=Stance.NEUTRAL,
        final_stances={str(ada.id): Stance.NEUTRAL, str(zeno.id): Stance.NEUTRAL},
        unparsed=[],
    )
    return chamber


def test_markdown_leads_with_the_headline() -> None:
    md = to_markdown(_concluded_with_headline())
    assert "## Outcome" in md
    assert "**The chamber concluded:** Mars should wait for cheaper launch costs." in md
    # The stance word is demoted to evidence, not promoted to the headline.
    assert "**Winning position:**" not in md


def test_markdown_shows_the_derived_outcome_facts() -> None:
    md = to_markdown(_concluded_with_headline())
    assert "- **Support:** unanimous — all 2 debaters" in md
    assert "- **How decided:** majority of final positions" in md


def test_markdown_lists_who_moved() -> None:
    chamber = _concluded_with_headline()
    ada, zeno = chamber.participants
    chamber.stance_history = [
        StancePoll(round_index=0, stances={str(ada.id): Stance.PRO,
                                           str(zeno.id): Stance.CON}),
        StancePoll(round_index=1, stances={str(ada.id): Stance.NEUTRAL,
                                           str(zeno.id): Stance.NEUTRAL}),
    ]
    md = to_markdown(chamber)
    assert "- **Positions moved:** Ada (pro→neutral), Zeno (con→neutral)" in md


def test_markdown_omits_the_movement_line_when_nobody_moved() -> None:
    assert "**Positions moved:**" not in to_markdown(_concluded_with_headline())


def test_markdown_falls_back_to_the_old_shape_without_a_headline() -> None:
    chamber = _concluded_with_headline()
    chamber.consensus.headline = ""
    md = to_markdown(chamber)
    # Strictly better than before, never broken: the old heading returns, and the
    # derived facts are shown alongside it.
    assert "## Outcome: majority" in md
    assert "**Winning position:** neutral" in md
    assert "- **Support:** unanimous — all 2 debaters" in md


def test_json_export_carries_the_derived_summary_beside_the_chamber() -> None:
    data = to_export_dict(_concluded_with_headline())
    # A sibling key, so the chamber snapshot stays an exact dump of the model.
    assert data["chamber"]["consensus"]["headline"] == (
        "Mars should wait for cheaper launch costs."
    )
    assert data["outcome_summary"] == {
        "headline": "Mars should wait for cheaper launch costs.",
        "support": "unanimous — all 2 debaters",
        "decided_by": "majority of final positions",
        "movements": [],
    }


def test_json_export_has_no_summary_before_a_debate_concludes() -> None:
    chamber = make_chamber(make_participant("Ada", Stance.PRO))
    assert to_export_dict(chamber)["outcome_summary"] is None
```

**Note the shape change:** `to_export_dict` previously returned the chamber dump at the top level. It now returns `{"chamber": <dump>, "outcome_summary": <dict|null>}`. Three existing assertions read the old shape and must be updated to go through `["chamber"]`:

- `backend/tests/test_export.py:93` and `:184`
- `backend/tests/test_api.py:419` — `assert as_json.json()["topic"] == "Should we colonise Mars?"` becomes `assert as_json.json()["chamber"]["topic"] == ...`

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_export.py -v`
Expected: FAIL — `assert '**The chamber concluded:**' in md`

- [ ] **Step 3: Implement the Markdown block**

In `backend/cicero/core/export.py`, add to the imports:

```python
from cicero.core.outcome import summarize_outcome
```

Replace lines 82-88 (`if chamber.consensus is not None:` through the trailing `lines.append("")`) with:

```python
    if chamber.consensus is not None:
        summary = summarize_outcome(chamber)
        assert summary is not None  # noqa: S101 — guarded by the branch above
        if chamber.consensus.headline:
            lines.append("## Outcome")
            lines.append("")
            lines.append(f"**The chamber concluded:** {chamber.consensus.headline}")
        else:
            # No usable headline: fall back to the pre-F5 shape rather than
            # showing a heading with nothing under it.
            lines.append(f"## Outcome: {chamber.consensus.outcome.value}")
            if chamber.consensus.winning_stance is not None:
                lines.append(
                    f"**Winning position:** {chamber.consensus.winning_stance.value}"
                )
        lines.append("")
        lines.append(f"- **Support:** {summary.support}")
        lines.append(f"- **How decided:** {summary.decided_by}")
        if summary.movements:
            lines.append(f"- **Positions moved:** {', '.join(summary.movements)}")
        lines.append("")
        lines.append(chamber.consensus.statement)
        lines.append("")
```

`ruff` selects `S` (bandit), which flags bare `assert` — hence the `noqa`. If you prefer, replace the assert with an early `summary = summarize_outcome(chamber) or OutcomeSummary("", "", "", ())`; the assert is clearer about the invariant.

- [ ] **Step 4: Implement the JSON block**

Replace `to_export_dict` (lines 14-16) with:

```python
def to_export_dict(chamber: Chamber) -> dict[str, Any]:
    """A JSON-serialisable snapshot of the whole chamber, plus its outcome summary.

    The summary is a **sibling** of the chamber rather than a key inside it, so
    the chamber remains an exact dump of the model. Nothing consumes this export
    as input, so the added key breaks no round-trip.
    """
    summary = summarize_outcome(chamber)
    return {
        "chamber": chamber.model_dump(mode="json"),
        "outcome_summary": asdict(summary) | {"movements": list(summary.movements)}
        if summary is not None
        else None,
    }
```

Add `from dataclasses import asdict` to the imports. (`asdict` renders `movements` as a tuple, which `json` serialises as a list anyway — the explicit `list(...)` keeps the returned dict equal to the test's expectation without relying on that.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_export.py -v`
Expected: PASS

- [ ] **Step 6: Fix the export endpoint's consumers**

Run: `cd backend && pytest -q`
`test_api.py` asserts on the export endpoint's JSON body (`chambers.py:581` returns `to_export_dict(chamber)`). Update any assertion that read a top-level chamber key to read through `["chamber"]`. Then:

Run: `cd backend && make lint && make type && make mutation`
Expected: all clean, mutation score ≥ 80.

- [ ] **Step 7: Commit**

```bash
cd backend && git add cicero/core/export.py tests/test_export.py tests/test_api.py
git commit -m "feat: lead the exported outcome with the headline (F5)"
```

---

### Task 6: Carry the headline into run comparison

**Files:**
- Modify: `backend/cicero/core/compare.py:20-48` (`summarize_run`)
- Test: `backend/tests/test_compare.py`

**Interfaces:**
- Consumes: `ConsensusResult.headline` (Task 2)

Comparing two runs of one topic is where a one-sentence summary earns the most (H4/FR-32) — it is the difference between reading two full statements and reading two sentences.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_compare.py`:

```python
def test_run_summary_carries_the_headline() -> None:
    ada = make_participant("Ada", Stance.PRO)
    chamber = make_chamber(ada)
    chamber.consensus = ConsensusResult(
        outcome=ConsensusOutcome.CONSENSUS,
        statement="A long statement nobody wants to read twice.",
        headline="Mars should wait.",
        winning_stance=Stance.PRO,
        final_stances={str(ada.id): Stance.PRO},
        unparsed=[],
    )
    assert summarize_run(chamber)["headline"] == "Mars should wait."


def test_run_summary_headline_is_none_before_an_outcome() -> None:
    chamber = make_chamber(make_participant("Ada", Stance.PRO))
    assert summarize_run(chamber)["headline"] is None
```

Add any missing imports (`ConsensusResult`, `ConsensusOutcome`, `Stance`, `make_chamber`, `make_participant`).

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/test_compare.py -v -k headline`
Expected: FAIL — `KeyError: 'headline'`

- [ ] **Step 3: Add the field**

In `backend/cicero/core/compare.py`, inside the dict returned by `summarize_run`, directly after `"outcome"`:

```python
        # The one line that makes two runs comparable at a glance (F5).
        "headline": consensus.headline or None if consensus is not None else None,
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_compare.py -v`
Expected: PASS

- [ ] **Step 5: Run the gate**

Run: `cd backend && pytest -q && make lint && make type`
Expected: all clean.

- [ ] **Step 6: Commit**

```bash
cd backend && git add cicero/core/compare.py tests/test_compare.py
git commit -m "feat: include the outcome headline in run comparison (F5)"
```

---

### Task 7: Serve the outcome summary over the API

The derived strings must have exactly one implementation, so the UI reads them rather than re-deriving them in TypeScript. This mirrors the existing `GET /chambers/{id}/metrics` endpoint (`chambers.py:585`), which serves derived data the same way.

**Files:**
- Modify: `backend/cicero/api/routers/chambers.py` (imports; new route after `get_metrics`)
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: `summarize_outcome(chamber) -> OutcomeSummary | None` (Task 4)
- Produces: `GET /chambers/{chamber_id}/outcome` → `{"headline": str, "support": str, "decided_by": str, "movements": list[str]}`; `404` when the chamber does not exist **or** has no outcome yet.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_api.py`, using the file's existing `client` fixture (line 23) and its `_create_chamber` / `_add_participant` helpers (lines 37 and 43), and driving a real mock-provider debate exactly as `test_metrics_endpoint` (line 431) does:

```python
def test_outcome_endpoint_returns_the_derived_summary(client: TestClient) -> None:
    cid = _create_chamber(client)
    _add_participant(client, cid, "Pro-A", "pro")
    _add_participant(client, cid, "Pro-B", "pro")
    client.post(f"/chambers/{cid}/run", params={"wait": "true"})

    resp = client.get(f"/chambers/{cid}/outcome")

    assert resp.status_code == 200
    body = resp.json()
    # The mock moderator's fixed reply (Task 9) is what makes this deterministic.
    assert body["headline"] == "The chamber reached a deterministic mock outcome."
    assert body["support"] == "unanimous — all 2 debaters"
    assert body["decided_by"] == "all debaters converged"
    assert body["movements"] == []


def test_outcome_endpoint_404s_before_the_debate_concludes(client: TestClient) -> None:
    cid = _create_chamber(client)
    _add_participant(client, cid, "Pro-A", "pro")
    _add_participant(client, cid, "Pro-B", "pro")
    resp = client.get(f"/chambers/{cid}/outcome")
    assert resp.status_code == 404


def test_outcome_endpoint_404s_for_an_unknown_chamber(client: TestClient) -> None:
    resp = client.get(f"/chambers/{uuid4()}/outcome")
    assert resp.status_code == 404
```

Add `from uuid import uuid4` to the imports if it is not already there.

**Ordering note:** the first test's `headline` assertion depends on the mock provider emitting a `HEADLINE:` line, which Task 9 adds. If you run tasks strictly in order, write that assertion as `assert body["headline"] == ""` here and tighten it to the exact sentence in Task 9. If you have already done Task 9, use the assertion as written above.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_api.py -v -k outcome_endpoint`
Expected: FAIL — 404 on all three (the route does not exist).

- [ ] **Step 3: Add the route**

In `backend/cicero/api/routers/chambers.py`, add to the imports:

```python
from cicero.core.outcome import summarize_outcome
```

Add after the `get_metrics` route (around line 585-600, matching its style):

```python
@router.get("/{chamber_id}/outcome")
def get_outcome(chamber_id: UUID, repo: RepoDep) -> dict[str, object]:
    """The concluded debate's headline and derived facts (F5, FR-23).

    Served rather than re-derived in the UI so the wording has exactly one
    implementation — the same reason ``/metrics`` is an endpoint.
    """
    chamber = _require_chamber(repo, chamber_id)
    summary = summarize_outcome(chamber)
    if summary is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="chamber has no outcome yet"
        )
    return {
        "headline": summary.headline,
        "support": summary.support,
        "decided_by": summary.decided_by,
        "movements": list(summary.movements),
    }
```

Use whatever the file's existing "load chamber or 404" helper is called — check how `get_metrics` and `get_chamber` (line 216) resolve a chamber and raise, and match it exactly rather than introducing `_require_chamber` if a differently-named helper already exists.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_api.py -v -k outcome_endpoint`
Expected: PASS

- [ ] **Step 5: Run the gate**

Run: `cd backend && pytest -q && make lint && make type`
Expected: all clean. (`api/*` is excluded from the mutation gate by design — see `setup.cfg` — so no mutation run is needed for this task.)

- [ ] **Step 6: Commit**

```bash
cd backend && git add cicero/api/routers/chambers.py tests/test_api.py
git commit -m "feat: serve the derived outcome summary over the API (F5)"
```

---

### Task 8: Show the headline in the UI

**Files:**
- Modify: `frontend/src/types.ts:86-91` (`ConsensusResult`), plus a new `OutcomeSummary` interface
- Modify: `frontend/src/api.ts` (new `getOutcome`)
- Modify: `frontend/src/ChamberDetail.tsx:729-742` (the outcome card)
- Create: `frontend/src/ChamberDetail.outcome.test.tsx`

**Interfaces:**
- Consumes: `GET /chambers/{id}/outcome` (Task 7)
- Produces: `OutcomeSummary` type; `getOutcome(id: string): Promise<OutcomeSummary>`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/ChamberDetail.outcome.test.tsx`. Copy the setup from `frontend/src/ChamberDetail.mute.test.tsx` lines 1-40 verbatim — the `vi.mock("./useDebateStream", ...)` stub, the partial `vi.mock("./api", async (importOriginal) => ...)` block, and its `participant()` / `chamber()` builders — then add `getOutcome: () => Promise.resolve(outcomeFixture)` to the api mock and give `chamber()` a `consensus` field. Note the stream mock returns `consensus: null`, so the card reads `chamber.consensus` (`ChamberDetail.tsx:281`).

```tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

// Mock ./api exactly as ChamberDetail.mute.test.tsx does, adding getOutcome.

describe("outcome card", () => {
  it("leads with the headline and shows the derived facts", async () => {
    renderChamber({
      consensus: {
        outcome: "majority",
        statement: "The majority prevailed.",
        headline: "Mars should wait for cheaper launch costs.",
        winning_stance: "neutral",
        final_stances: {},
        unparsed: [],
      },
      outcome: {
        headline: "Mars should wait for cheaper launch costs.",
        support: "contested — 2 of 3 debaters settled on neutral, 1 dissent",
        decided_by: "majority of final positions",
        movements: ["Ada (pro→neutral)"],
      },
    });

    expect(
      await screen.findByText("Mars should wait for cheaper launch costs."),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/contested — 2 of 3 debaters settled on neutral/),
    ).toBeInTheDocument();
    expect(screen.getByText(/majority of final positions/)).toBeInTheDocument();
    expect(screen.getByText(/Ada \(pro→neutral\)/)).toBeInTheDocument();
    // The stance word is evidence in the support line, not the headline.
    expect(screen.queryByText("Winning position:")).not.toBeInTheDocument();
  });

  it("falls back to the outcome label when there is no headline", async () => {
    renderChamber({
      consensus: {
        outcome: "majority",
        statement: "The majority prevailed.",
        headline: "",
        winning_stance: "neutral",
        final_stances: {},
        unparsed: null,
      },
      outcome: {
        headline: "",
        support: "contested — 2 of 3 debaters settled on neutral, 1 dissent",
        decided_by: "majority of final positions",
        movements: [],
      },
    });

    expect(await screen.findByText(/Majority decision/)).toBeInTheDocument();
    expect(screen.getByText(/Winning position/)).toBeInTheDocument();
  });

  it("omits the movement line when nobody moved", async () => {
    renderChamber({
      consensus: {
        outcome: "consensus",
        statement: "Agreed.",
        headline: "Mars should wait.",
        winning_stance: "neutral",
        final_stances: {},
        unparsed: [],
      },
      outcome: {
        headline: "Mars should wait.",
        support: "unanimous — all 2 debaters",
        decided_by: "all debaters converged",
        movements: [],
      },
    });

    await screen.findByText("Mars should wait.");
    expect(screen.queryByText(/Positions moved/)).not.toBeInTheDocument();
  });
});
```

Write `renderChamber` as a local helper that builds a full `Chamber` object, stubs `getChamber` to resolve it and `getOutcome` to resolve the `outcome` argument, and renders `<ChamberDetail>` with whatever props `ChamberDetail.mute.test.tsx` passes.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/ChamberDetail.outcome.test.tsx`
Expected: FAIL — `getOutcome` is not exported from `./api`.

- [ ] **Step 3: Add the types**

In `frontend/src/types.ts`, extend `ConsensusResult` (lines 86-91):

```ts
export interface ConsensusResult {
  outcome: Outcome;
  statement: string;
  // One declarative sentence stating what the chamber concluded. Empty when the
  // moderator produced none that was usable — the card then falls back.
  headline: string;
  winning_stance: Stance | null;
  final_stances: Record<string, Stance>;
  // Ids whose final position could not be read. null means "not recorded" — a
  // chamber concluded before this field existed — not "none failed".
  unparsed: string[] | null;
}

/** Derived outcome facts, served by GET /chambers/{id}/outcome. */
export interface OutcomeSummary {
  headline: string;
  support: string;
  decided_by: string;
  movements: string[];
}
```

- [ ] **Step 4: Add the fetch**

In `frontend/src/api.ts`, next to `getMetrics` (line 176), matching its exact style:

```ts
export function getOutcome(id: string): Promise<OutcomeSummary> {
  return request<OutcomeSummary>(`/chambers/${id}/outcome`);
}
```

Import `OutcomeSummary` in the file's type import block. Use whatever the file's internal fetch helper is actually called — read `getMetrics` and copy it.

- [ ] **Step 5: Render the card**

In `frontend/src/ChamberDetail.tsx`, add state and a fetch mirroring the existing metrics effect at lines 100-106. The module is imported namespaced, so calls are `api.getOutcome(...)`, not a bare import. `consensus` is already computed at line 281 as `stream.consensus ?? chamber.consensus`, so declare the effect **after** that line:

```tsx
const [outcome, setOutcome] = useState<OutcomeSummary | null>(null);

// The headline comes with `consensus`; the derived lines are served separately
// so their wording has one implementation (backend `core/outcome.py`).
useEffect(() => {
  if (!consensus) {
    setOutcome(null);
    return;
  }
  let cancelled = false;
  void api
    .getOutcome(chamberId)
    .then((summary) => {
      if (!cancelled) setOutcome(summary);
    })
    .catch(() => {
      // The card still renders from `consensus` alone — the derived lines are
      // an enhancement, not a prerequisite. A 404 here just means the stream
      // announced the outcome before the chamber was readable.
      if (!cancelled) setOutcome(null);
    });
  return () => {
    cancelled = true;
  };
}, [chamberId, consensus]);
```

Hooks must not sit below a conditional return — check where line 281 falls relative to any early `return` in the component and hoist both the `useState` and the `useEffect` above it, reading `consensus` from the same expression.

Replace lines 729-742 with:

```tsx
{consensus && (
  <div className="card">
    {consensus.headline ? (
      <h2>{consensus.headline}</h2>
    ) : (
      <>
        <h2>Outcome: {outcomeLabel(consensus.outcome)}</h2>
        {consensus.winning_stance && (
          <p>
            Winning position:{" "}
            <span className={`stance ${consensus.winning_stance}`}>
              {stanceLabel(consensus.winning_stance)}
            </span>
          </p>
        )}
      </>
    )}
    {outcome && (
      <dl className="outcome-facts">
        <dt>Support</dt>
        <dd>{outcome.support}</dd>
        <dt>How decided</dt>
        <dd>{outcome.decided_by}</dd>
        {outcome.movements.length > 0 && (
          <>
            <dt>Positions moved</dt>
            <dd>{outcome.movements.join(", ")}</dd>
          </>
        )}
      </dl>
    )}
    <p style={{ whiteSpace: "pre-wrap" }}>{consensus.statement}</p>
  </div>
)}
```

Add a modest `.outcome-facts` rule to `frontend/src/index.css` next to the existing card styles — a two-column grid (`display: grid; grid-template-columns: auto 1fr; gap: 0.25rem 0.75rem;`) with the `dt` in the muted label colour already used elsewhere in that file. Match the existing palette; do not introduce new colours.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/ChamberDetail.outcome.test.tsx`
Expected: PASS

- [ ] **Step 7: Run the frontend gate**

Run: `cd frontend && npm test && npm run lint && npm run typecheck`
Expected: all clean. Existing tests that build a `ConsensusResult` fixture will fail type-checking until `headline` and `unparsed` are added to them — update each fixture.

- [ ] **Step 8: Commit**

```bash
cd frontend && git add src/types.ts src/api.ts src/ChamberDetail.tsx src/index.css src/ChamberDetail.outcome.test.tsx
git commit -m "feat: lead the outcome card with the headline (F5)"
```

---

### Task 9: Keep the offline acceptance path honest

`make demo` walks the acceptance path with PASS/FAIL per requirement. Its FR-23 check must assert a headline, which means the mock provider has to emit one — otherwise the deterministic path never exercises the real parser.

**Files:**
- Modify: `backend/cicero/providers/mock.py` (moderator detection)
- Modify: `backend/tests/test_mock_provider.py`
- Modify: `backend/scripts/demo.py:800-806` (FR-23/24 check)

**Interfaces:**
- Produces: `mock.MODERATOR_MARKER: str` — the literal that identifies a moderator system prompt

`mock.py` **is** in the mutation gate (`setup.cfg`), so the new branch needs tests that kill its mutants.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_mock_provider.py`:

```python
async def test_mock_answers_a_moderator_prompt_with_a_headline() -> None:
    provider = MockProvider()
    result = await provider.generate(
        [
            Message(role=Role.SYSTEM, content=MODERATOR_SYSTEM),
            Message(role=Role.USER, content="Motion: x\n\nWrite a CONSENSUS STATEMENT."),
        ],
        GenerateOptions(model="mock-small"),
    )
    # The offline acceptance path has to exercise the real directive parser, not
    # a shape that only looks like a moderator reply.
    assert result.content.startswith("HEADLINE: ")
    assert "\n" in result.content


async def test_mock_moderator_marker_matches_the_real_moderator_prompt() -> None:
    # Kept as a literal so the provider layer stays independent of core.prompts;
    # this is what stops the two drifting apart (same contract as POLL_MARKER).
    assert MODERATOR_MARKER in MODERATOR_SYSTEM.lower()


async def test_mock_still_echoes_for_a_debate_turn() -> None:
    provider = MockProvider()
    result = await provider.generate(
        [
            Message(role=Role.SYSTEM, content='You are "Ada", a participant.'),
            Message(role=Role.USER, content="Give your next contribution."),
        ],
        GenerateOptions(model="mock-small"),
    )
    assert result.content.startswith("[mock:mock-small]")


async def test_mock_scripted_replies_still_win_over_the_moderator_branch() -> None:
    provider = MockProvider(scripted=["exact reply"])
    result = await provider.generate(
        [
            Message(role=Role.SYSTEM, content=MODERATOR_SYSTEM),
            Message(role=Role.USER, content="Write a CONSENSUS STATEMENT."),
        ],
        GenerateOptions(model="mock-small"),
    )
    assert result.content == "exact reply"
```

Import `MODERATOR_MARKER` from `cicero.providers.mock` and `MODERATOR_SYSTEM` from `cicero.core.prompts`, plus `Message` / `Role` / `GenerateOptions` if not already imported.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_mock_provider.py -v -k "moderator or scripted_replies_still"`
Expected: FAIL — `ImportError: cannot import name 'MODERATOR_MARKER'`

- [ ] **Step 3: Implement the moderator branch**

In `backend/cicero/providers/mock.py`, after `POLL_MARKER` (line 27):

```python
#: Identifies a moderator prompt from its system text, on the same terms as
#: ``POLL_MARKER``: a literal, so the provider layer stays independent of
#: ``core.prompts``, with a test asserting the two never drift apart.
MODERATOR_MARKER = "impartial moderator"

#: A moderator reply that exercises the real directive parser. A mock has no
#: view to summarise, so it says so — but it says so in the right shape, which
#: is what makes the offline acceptance path meaningful.
_DEFAULT_MODERATOR_REPLY = (
    "HEADLINE: The chamber reached a deterministic mock outcome.\n"
    "This is a mock synthesis of the debate."
)
```

In `generate`, insert a branch between the scripted check and the poll check:

```python
        last = messages[-1].content if messages else ""
        system = messages[0].content if messages else ""
        if self._scripted:
            content = self._scripted.pop(0)
        elif POLL_MARKER in last.lower():
            # A stance poll needs an *answer*, not an echo. Echoing used to
            # "work" only because the parser matched pro/con out of the quoted
            # transcript — the mock never reported a position at all.
            content = self._poll_answer
        elif MODERATOR_MARKER in system.lower():
            content = _DEFAULT_MODERATOR_REPLY
        else:
            snippet = last[:80]
            content = f"[mock:{options.model}] response to: {snippet}"
```

Verify `MODERATOR_SYSTEM` (`core/prompts.py:106`) really contains "impartial MODERATOR" — it reads `"You are the impartial MODERATOR of a structured debate."`, so the lower-cased marker matches. The new test asserts this.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_mock_provider.py -v`
Expected: PASS

- [ ] **Step 5: Assert the headline in the release demo**

In `backend/scripts/demo.py`, after the FR-23/24 statement check (lines 800-806), add:

```python
    headline = str(consensus.get("headline") or "")
    report.check(
        "FR-23",
        "outcome carries a one-sentence headline",
        bool(headline.strip()),
        f"{headline[:60]!r}" if headline else "missing",
    )
```

- [ ] **Step 6: Run the demo end to end**

Run: `cd backend && make demo`
Expected: every check PASS, including the new `FR-23 outcome carries a one-sentence headline`.

If the demo reads the export endpoint's JSON anywhere, it now needs `["chamber"]` (Task 5 changed that shape) — search `demo.py` for `export` and fix any such read.

- [ ] **Step 7: Run the full gate**

Run: `cd backend && pytest -q && make lint && make type && make mutation`
Expected: all clean, mutation score ≥ 80.

- [ ] **Step 8: Commit**

```bash
cd backend && git add cicero/providers/mock.py tests/test_mock_provider.py scripts/demo.py
git commit -m "test: exercise the headline on the offline acceptance path (F5)"
```

---

### Task 10: Documentation

**Files:**
- Modify: `docs/requirements.md:127-129` (FR-23)
- Modify: `docs/backlog.md:187-195` (Epic F table — add F5)
- Modify: `README.md` (Status section)

- [ ] **Step 1: Extend FR-23**

In `docs/requirements.md`, extend the FR-23 bullet so the headline is part of the required artifact rather than a separate capability:

```markdown
- **FR-23 (M)** Produce a **Consensus Statement**: a synthesized final position
  with each participant's final stance. Every outcome — consensus, majority,
  verdict, or disagreement — additionally carries a **one-sentence headline**
  stating what the chamber concluded (for a disagreement, the unresolved crux),
  because a stance word alone cannot express a compromise and collapses to
  `neutral`.
```

Keep the surrounding lines and any existing sub-bullets intact — read the current text first and preserve everything the rewrite does not deliberately change.

- [ ] **Step 2: Add F5 to the backlog**

In `docs/backlog.md`, append a row to the Epic F table after F4:

```markdown
| F5 | As a reader, every outcome opens with a one-sentence headline saying what the chamber concluded, plus how firmly it was held, how it was decided, and who moved. | FR-23 | S | 3 | ✅ done — the moderator emits a `HEADLINE:` directive on the existing call (no extra request); `core/outcome.py` derives support/basis/movement from data already recorded, so chambers concluded before F5 gain them retroactively. `ConsensusResult` also stores the `unparsed` set it decided on, because the final poll is not always in `stance_history`. Served by `GET /chambers/{id}/outcome`; falls back to the pre-F5 display when no usable headline was produced |
```

- [ ] **Step 3: Update the README status**

In `README.md`, add a bullet to the Milestone status list, matching the surrounding bullets' voice:

```markdown
- **Outcomes that say something (F5)** — every concluded debate opens with a
  one-sentence headline of what the chamber actually concluded, above the
  support (`contested — 2 of 3 debaters settled on neutral, 1 dissent`), how it
  was decided, and who changed position. `**Winning position:** neutral` was
  never a result — it is what a compromise collapses to when the only vocabulary
  is pro/con/neutral. The stance word is still shown, as evidence rather than
  as the headline.
```

- [ ] **Step 4: Verify the docs match the code**

Run: `cd backend && make demo`
Read the demo's Markdown output (it writes to `demo-output/`) and confirm the rendered outcome block matches what the README and spec describe.

- [ ] **Step 5: Commit**

```bash
git add docs/requirements.md docs/backlog.md README.md
git commit -m "docs: record the outcome headline (F5)"
```

---

### Task 11: Full verification

- [ ] **Step 1: Backend gate**

Run: `cd backend && make check`
Expected: lint clean, mypy clean, coverage reported, mutation score ≥ 80.

- [ ] **Step 2: Frontend gate**

Run: `cd frontend && npm test && npm run lint && npm run typecheck && npm run build`
Expected: all clean.

- [ ] **Step 3: Frontend mutation gate**

Run: `cd frontend && npm run mutation`
Expected: passes its configured threshold. The outcome card is presentation, so if Stryker flags surviving mutants in `ChamberDetail.tsx` check whether they were already surviving before this change (`git stash` and re-run) rather than assuming they are new.

- [ ] **Step 4: Release demo**

Run: `cd backend && make demo`
Expected: every requirement PASS, including `FR-23 outcome carries a one-sentence headline`.

- [ ] **Step 5: Real-model check (the one thing tests cannot tell you)**

Run a real debate against a local Ollama model through the UI and read the outcome card. Confirm:
- the model actually emitted the `HEADLINE:` line (if it did not, the card falls back — that is the signal to tune `MODERATOR_HEADLINE_DIRECTIVE`, not to add a second call);
- the headline is a **claim**, not a description of the discussion ("The debate covered several perspectives" is a compliance pass and a quality failure);
- the support line agrees with the stance-history table shown above it.

Record what you saw in the PR description. This is the evidence Spec B needs before it builds on the headline.

- [ ] **Step 6: Open the PR**

```bash
git push -u origin spec/outcome-headline
gh pr create --title "F5: outcomes that say something — headline + derived support/basis/movement" --body "$(cat <<'EOF'
## Summary

Replaces `**Winning position:** neutral` with a one-sentence headline of what the
chamber actually concluded, plus support, how it was decided, and who moved.

`neutral` was never a result: `CONVERGE_GUIDANCE` invites debaters to propose a
compromise and the one-word poll has no way to name one, so every compromise
collapsed to `neutral`. The stance word is still shown — as evidence in the
support line, not as the headline.

## Notes for review

- No extra model call: the headline rides along on the existing moderator call as
  a `HEADLINE:` directive, parsed by the same mechanism that already reads `WINNER:`.
- `ConsensusResult.unparsed` is stored rather than derived because `_record_poll`
  does not always persist the final poll, so `stance_history[-1]` can name a
  different poll's failures than the one `finalize()` decided on.
- Support/basis/movement are derived, so chambers concluded before this change
  gain them retroactively.
- `to_export_dict` now returns `{"chamber": ..., "outcome_summary": ...}`.
- Behaviour change: an unparseable `WINNER:` line is now consumed rather than
  left at the top of the statement.

## Real-model evidence

<paste what step 5 showed>

Spec: `docs/superpowers/specs/2026-08-05-outcome-headline-design.md`
Plan: `docs/superpowers/plans/2026-08-05-outcome-headline.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Deviations from the spec, for the record

1. **`GET /chambers/{id}/outcome` is new.** The spec said the UI shows the derived values but not how it gets them. Re-deriving the wording in TypeScript would duplicate it in two languages; an endpoint keeps one implementation, and `/metrics` is the existing precedent for serving derived data this way.
2. **`parse_verdict` is removed rather than kept as a caller of `parse_directives`.** Keeping it would leave dead code, since `finalize` is its only non-test caller. `parse_moderator_reply` replaces it and reads both directives at once.
3. **An unparseable `WINNER:` line is now consumed.** Previously it was left in the statement body. One existing test changes. A failed directive is not content.
4. **`to_export_dict` returns a wrapper object.** The spec called for `outcome_summary` as a sibling of the chamber dump, which requires the top level to become `{"chamber": ..., "outcome_summary": ...}`.
