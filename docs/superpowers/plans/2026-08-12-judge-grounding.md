# Judge Grounding (F10) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the compliance judge quote the sentence its verdict rests on, verify that quote appears in the turn, and record nothing when it does not.

**Architecture:** The judge stops replying with one word and replies with two directives — `POSITION:` (a sentence copied from the turn) and `SIDE:` — parsed by the existing `parse_directives`. A pure Python check requires the quote to be a whitespace/case-normalised substring of the turn; failure means unmeasured. Both the stance and the verified quote land in `Turn.metadata`. Nothing decides on this signal, exactly as in F9.

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI, pytest, mutmut.

**Spec:** [`docs/superpowers/specs/2026-08-12-judge-grounding-design.md`](../specs/2026-08-12-judge-grounding-design.md)

## Global Constraints

- **Task 1 must complete before any code changes.** It captures the current protocol's verdicts as the comparison baseline. Once the prompt is rewritten that baseline is unrecoverable without reverting — the spec states this as a requirement, not a convenience.
- **An ungrounded answer records nothing.** Four paths mean "not measured": provider error, unparseable reply, `POSITION: none`, and a quote that fails the grounding check. None of them may write a partial record.
- **Both metadata keys are written together or not at all.** `argued` without `argued_quote` is only valid for chambers judged before this change.
- **Grounding normalisation is whitespace and case only.** No fuzzy ratios, no token overlap — looseness reintroduces the judgement call the check exists to remove.
- **`COMPLIANCE_SYSTEM` must keep the exact phrase `impartial reader`.** `providers/mock.py` matches on it (`COMPLIANCE_MARKER`) and a drift test asserts the two agree. Changing it without changing both breaks the offline path.
- **Nothing in this feature may influence a decision:** no decision rule, stop condition, or outcome value.
- Backend commands run from `backend/`. The venv is not on PATH: `export PATH="$PWD/.venv/bin:$PATH"` first.
- **Never run bare `make mutation`.** Scope it: `mutmut run --paths-to-mutate cicero/core/compliance.py`. The repo `.venv` is Python 3.14 where mutmut dies in deepcopy; use a 3.11 venv. Check `git status` afterwards for a stray `.bak` or a source file left carrying a mutant, and **never run the test suite while mutmut is running** — it mutates files on disk.

---

## File Structure

**Created:**
- `backend/tests/fixtures/f10_invasion_turns.md` — the 32 turns, no verdicts
- `backend/tests/fixtures/f10_baseline_verdicts.json` — current protocol's verdicts
- `backend/tests/fixtures/f10_hand_labels.json` — the hand-read ground truth

**Modified:**
- `backend/cicero/core/prompts.py` — the two-directive prompt + rebuttal clause
- `backend/cicero/core/compliance.py` — `Judgement`, grounding check, reply parsing, `ComplianceJudge.judge`
- `backend/cicero/core/orchestrator.py` — write both metadata keys
- `backend/cicero/providers/mock.py` — emit the two-directive shape
- `backend/cicero/api/routers/chambers.py` — extract the judge builder; add the re-judge endpoint
- `backend/scripts/check_compliance_judge.py` — record quotes alongside verdicts
- `docs/` — requirements, backlog, decision record, model-selection

---

### Task 1: Capture the baseline — BEFORE ANY CODE CHANGE

**Files:**
- Create: `backend/tests/fixtures/f10_invasion_turns.md`, `backend/tests/fixtures/f10_baseline_verdicts.json`

**Interfaces:**
- Produces: the two fixture files later tasks measure against.

**This task changes no source.** It runs the existing script against the existing judge. If any later task's code lands first, the baseline is gone.

The chamber is the four-debater run of *"Switzerland should military invade Italy and take over its government, to finally fix the country"* — 32 turns, debaters on `qwen3:30b` and `llama3.1:latest`, moderator `qwen3:30b`. It lives in the Docker volume, not `backend/cicero.db`, so the API must be the one serving that data.

- [ ] **Step 1: Find the chamber id**

```bash
curl -s http://localhost:8000/chambers | python3 -c "
import sys, json
for c in json.load(sys.stdin):
    if 'invade Italy' in c['topic']:
        print(c['id'], c['status'], len(c['turns']), 'turns')
"
```
Expected: one id, status `concluded`, 32 turns. If it reports a different count, stop and report — the fixture size is quoted throughout this plan.

- [ ] **Step 2: Capture turns and baseline verdicts**

Run from `backend/`, with the 3.11-or-3.14 venv either way (no mutmut involved):

```bash
export PATH="$PWD/.venv/bin:$PATH"
python scripts/check_compliance_judge.py <chamber-id> \
  --model qwen3:30b \
  --turns-out tests/fixtures/f10_invasion_turns.md \
  --verdicts-out tests/fixtures/f10_baseline_verdicts.json
```

`--model qwen3:30b` on purpose: that is the judge that produced the errors, so the baseline measures the real failure rather than a different model's behaviour.

- [ ] **Step 3: Verify the fixtures**

```bash
grep -c '^## Turn ' tests/fixtures/f10_invasion_turns.md
python -c "import json; d=json.load(open('tests/fixtures/f10_baseline_verdicts.json')); print(len(d), 'verdicts')"
grep -ciE 'pro|con|neutral' tests/fixtures/f10_invasion_turns.md
```
Expected: 32 sections, 32 verdicts. The third command is a leak check — the turns file must contain no verdict lines; matches inside the debate prose are fine, but confirm by eye that no `SIDE:`/`argued` line appears.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/fixtures/f10_invasion_turns.md backend/tests/fixtures/f10_baseline_verdicts.json
git commit -m "test: capture the F10 baseline before changing the judge"
```

---

### Task 2: Hand-label the 32 turns — THE GROUND TRUTH

**Files:**
- Create: `backend/tests/fixtures/f10_hand_labels.json`

**Interfaces:**
- Consumes: `f10_invasion_turns.md` (Task 1)
- Produces: `f10_hand_labels.json` — `{turn_id: "pro"|"con"|"neutral"}` for all 32 turns

**Read the turns file only. Do not open `f10_baseline_verdicts.json` until this file is committed.** That is the entire methodology: a labeller who has seen the verdicts is checking their memory, not the prose. F9's validation used the same split for the same reason, and this project has a keyword classifier on record that was wrong by a factor of seven precisely because nobody read the turns.

- [ ] **Step 1: Read every turn and record a judgement**

For each `## Turn <id>` section, decide which side of the motion **the author supports** — not the side they discuss, quote, or attack. The known failure mode is rebuttals reading as the position they demolish, so a turn that spends four paragraphs dismantling the pro case is `con`.

Write `backend/tests/fixtures/f10_hand_labels.json`:

```json
{
  "<turn-uuid>": "con",
  "<turn-uuid>": "pro"
}
```

All 32 ids, one of `pro` / `con` / `neutral` each. Where a turn is genuinely ambiguous, pick the better reading and note the id — ambiguity is a finding, not a defect.

- [ ] **Step 2: Verify coverage before looking at anything else**

```bash
cd backend && python -c "
import json,re
labels=json.load(open('tests/fixtures/f10_hand_labels.json'))
ids=re.findall(r'^## Turn (\S+)', open('tests/fixtures/f10_invasion_turns.md').read(), re.M)
print('turns:',len(ids),'labelled:',len(labels))
missing=[i for i in ids if i not in labels]
extra=[k for k in labels if k not in ids]
print('missing:',missing); print('extra:',extra)
assert not missing and not extra and set(labels.values())<={'pro','con','neutral'}
print('OK')
"
```
Expected: `turns: 32 labelled: 32`, no missing, no extra, `OK`.

- [ ] **Step 3: Commit, then measure the baseline**

```bash
git add backend/tests/fixtures/f10_hand_labels.json
git commit -m "test: hand-read ground truth for the F10 invasion fixture"
```

Only now compare against the baseline:

```bash
cd backend && python -c "
import json
h=json.load(open('tests/fixtures/f10_hand_labels.json'))
b=json.load(open('tests/fixtures/f10_baseline_verdicts.json'))
agree=sum(1 for k,v in h.items() if b.get(k)==v)
unmeasured=sum(1 for k in h if b.get(k) is None)
print(f'baseline agreement: {agree}/{len(h)}  unmeasured: {unmeasured}')
for k,v in h.items():
    if b.get(k)!=v: print('  MISMATCH', k[:8], 'hand:',v,'judge:',b.get(k))
"
```

Record the printed numbers in the commit message of the next task and in the final docs. **This is the number the new protocol must beat.**

---

### Task 3: The grounding check

**Files:**
- Modify: `backend/cicero/core/compliance.py`
- Test: `backend/tests/test_compliance.py`

**Interfaces:**
- Produces: `quote_is_grounded(quote: str, content: str) -> bool`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_compliance.py`:

```python
from cicero.core.compliance import quote_is_grounded


def test_an_exact_quote_is_grounded() -> None:
    assert quote_is_grounded("the case is indefensible", "I think the case is indefensible.")


def test_whitespace_and_case_differences_are_tolerated() -> None:
    """A model that re-wraps or re-cases a copied sentence still copied it. The
    turn itself may have the sentence split across lines."""
    assert quote_is_grounded(
        "The  CASE\nis indefensible", "I think the case is indefensible."
    )


def test_a_paraphrase_is_not_grounded() -> None:
    """One word different is a paraphrase, and a paraphrase is the failure this
    check exists to catch — the judge did not read that sentence, it wrote one."""
    assert not quote_is_grounded("the case is weak", "I think the case is indefensible.")


def test_an_invented_quote_is_not_grounded() -> None:
    assert not quote_is_grounded("I concede entirely", "I think the case is indefensible.")


@pytest.mark.parametrize("quote", ["", "   ", "\n"])
def test_an_empty_quote_is_never_grounded(quote: str) -> None:
    """Empty normalises to "", which is a substring of everything — the one input
    that would pass by accident."""
    assert not quote_is_grounded(quote, "I think the case is indefensible.")
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && export PATH="$PWD/.venv/bin:$PATH" && pytest tests/test_compliance.py -k grounded -v`
Expected: FAIL — `ImportError: cannot import name 'quote_is_grounded'`

- [ ] **Step 3: Implement**

Add to `backend/cicero/core/compliance.py`, after `argued_stance`:

```python
def _normalise(text: str) -> str:
    """Collapse whitespace and case — nothing else.

    Deliberately narrow. Anything looser (fuzzy ratios, token overlap) puts a
    judgement call back into the one part of this module that has none.
    """
    return " ".join(text.split()).casefold()


def quote_is_grounded(quote: str, content: str) -> bool:
    """Whether ``quote`` really appears in ``content``.

    This is what makes the judge's answer checkable rather than trusted: a model
    that paraphrases instead of copying, or invents a sentence that is not in the
    turn, fails here and its verdict is discarded. An empty quote is rejected
    explicitly — it normalises to ``""``, which is a substring of everything.
    """
    if not quote.strip():
        return False
    return _normalise(quote) in _normalise(content)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && export PATH="$PWD/.venv/bin:$PATH" && pytest tests/test_compliance.py -k grounded -v`
Expected: PASS (7 tests — the parametrize contributes 3)

- [ ] **Step 5: Commit**

```bash
git add backend/cicero/core/compliance.py backend/tests/test_compliance.py
git commit -m "feat: verify a judge quote actually appears in the turn (F10)"
```

---

### Task 4: The two-directive prompt

**Files:**
- Modify: `backend/cicero/core/prompts.py` (the compliance fragments, ~line 160)
- Test: `backend/tests/test_prompt_builder.py`

**Interfaces:**
- Produces: `DIRECTIVE_POSITION = "POSITION"`, `DIRECTIVE_SIDE = "SIDE"`, `NO_POSITION = "none"`, and rewritten `COMPLIANCE_SYSTEM` / `COMPLIANCE_USER_INSTRUCTION`.

`build_compliance_messages(topic, content)` keeps its signature — it still must not receive a `Chamber` or `Participant`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_prompt_builder.py`:

```python
def test_compliance_prompt_asks_for_both_directives() -> None:
    messages = build_compliance_messages("a motion", "an argument")
    user = messages[-1].content
    assert f"{prompts.DIRECTIVE_POSITION}:" in user
    assert f"{prompts.DIRECTIVE_SIDE}:" in user


def test_compliance_prompt_warns_about_rebuttals() -> None:
    """The measured failure was rebuttals reading as the side they demolish. The
    prompt has to name it; the previous version never mentioned it."""
    user = build_compliance_messages("a motion", "an argument")[-1].content.lower()
    assert "rebut" in user
    assert "supports" in user


def test_compliance_prompt_offers_the_no_position_escape() -> None:
    user = build_compliance_messages("a motion", "an argument")[-1].content
    assert f"{prompts.DIRECTIVE_POSITION}: {prompts.NO_POSITION}" in user


def test_compliance_system_still_carries_the_mock_marker() -> None:
    """providers/mock.py matches on this literal to answer offline. Changing the
    wording without changing the marker silently stops `make demo` exercising
    the feature."""
    assert "impartial reader" in prompts.COMPLIANCE_SYSTEM.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && export PATH="$PWD/.venv/bin:$PATH" && pytest tests/test_prompt_builder.py -k compliance -v`
Expected: FAIL — `AttributeError: module 'cicero.core.prompts' has no attribute 'DIRECTIVE_POSITION'`

- [ ] **Step 3: Implement**

Replace the compliance fragments in `backend/cicero/core/prompts.py`:

```python
# Compliance-judge fragments (FR-34).
#: Directive keys the judge must open its reply with. Bare keys — the ``:`` is
#: the parser's, exactly as with the moderator's directives above.
DIRECTIVE_POSITION = "POSITION"
DIRECTIVE_SIDE = "SIDE"
#: The honest escape when a turn states no position of its own. Anything else
#: under POSITION must be a sentence copied from the turn.
NO_POSITION = "none"

#: The judge is told nothing about who wrote the turn or what they were assigned.
#: That omission is the design: assigned-stance labels in a prompt were measured
#: to *change* a model's answer, so a judge that knows the expected answer is a
#: judge that can be anchored to it.
COMPLIANCE_SYSTEM = (
    "You are an impartial reader. You will be shown one argument from a debate. "
    "Report which side of the motion its author supports, judging only what it "
    "actually says. " + SAFETY_RULE
)
#: Asks for a *sentence* before a label, deliberately. Measured across six
#: mechanisms, these models do structured analytical meta-work badly and
#: extraction well; the F5 headline works for the same reason. Quoting first
#: turns "classify this argument" into "find the sentence, then read it".
COMPLIANCE_USER_INSTRUCTION = (
    f"Reply with exactly two lines and nothing else:\n"
    f"{DIRECTIVE_POSITION}: <one sentence, copied word for word from the argument "
    f"above, in which the author states their own position>\n"
    f"{DIRECTIVE_SIDE}: pro, con, or neutral\n\n"
    f"Copy the {DIRECTIVE_POSITION} sentence exactly as it appears — do not "
    f"paraphrase, shorten or re-word it, and keep it on a single line. If the "
    f"argument never states a position of its own, reply "
    f"'{DIRECTIVE_POSITION}: {NO_POSITION}'.\n\n"
    f"An argument may quote, summarise or attack a position in order to argue "
    f"AGAINST it. Report the side the author supports, not the side they mention "
    f"or rebut. Use 'pro' if the author supports the motion, 'con' if the author "
    f"argues against the motion, and 'neutral' if the author takes no side."
)
COMPLIANCE_MOTION_LABEL = "The motion under debate is: {topic}"
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && export PATH="$PWD/.venv/bin:$PATH" && pytest tests/test_prompt_builder.py -v && ruff check . && mypy cicero`
Expected: PASS, clean.

- [ ] **Step 5: Commit**

```bash
git add backend/cicero/core/prompts.py backend/tests/test_prompt_builder.py
git commit -m "feat: ask the compliance judge to quote before it labels (F10)"
```

---

### Task 5: Parse the reply into a grounded `Judgement`

**Files:**
- Modify: `backend/cicero/core/compliance.py`
- Test: `backend/tests/test_compliance.py`

**Interfaces:**
- Consumes: `quote_is_grounded` (Task 3); `DIRECTIVE_POSITION`, `DIRECTIVE_SIDE`, `NO_POSITION` (Task 4); `parse_directives` and `parse_stance` from `cicero.core.consensus`
- Produces: `ARGUED_QUOTE_KEY = "argued_quote"`, `Judgement`, `parse_judgement(reply: str, content: str) -> Judgement | None`, and `ComplianceJudge.judge` returning `Judgement | None`

`parse_directives(text, keys)` peels leading `KEY: value` lines in any order and stops at the first line that is neither blank nor a recognised directive. It splits on the **first** colon only, so a quoted sentence containing a colon survives.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_compliance.py`:

```python
from cicero.core.compliance import ARGUED_QUOTE_KEY, Judgement, parse_judgement

TURN = "Invading would violate the UN Charter. I therefore oppose the motion."


def test_a_grounded_reply_yields_a_judgement() -> None:
    reply = "POSITION: I therefore oppose the motion.\nSIDE: con"
    assert parse_judgement(reply, TURN) == Judgement(
        stance=Stance.CON, quote="I therefore oppose the motion."
    )


def test_directive_order_does_not_matter() -> None:
    reply = "SIDE: con\nPOSITION: I therefore oppose the motion."
    result = parse_judgement(reply, TURN)
    assert result is not None and result.stance is Stance.CON


def test_a_paraphrased_quote_is_rejected() -> None:
    """The verdict may even be right — it is discarded anyway, because nothing
    ties it to the turn. An ungrounded judgement is what this feature exists to
    stop recording."""
    reply = "POSITION: I am against this motion.\nSIDE: con"
    assert parse_judgement(reply, TURN) is None


def test_no_position_is_not_a_judgement() -> None:
    assert parse_judgement("POSITION: none\nSIDE: neutral", TURN) is None


def test_a_missing_side_is_not_a_judgement() -> None:
    assert parse_judgement("POSITION: I therefore oppose the motion.", TURN) is None


def test_an_unreadable_side_is_not_a_judgement() -> None:
    reply = "POSITION: I therefore oppose the motion.\nSIDE: sideways"
    assert parse_judgement(reply, TURN) is None


def test_prose_instead_of_directives_is_not_a_judgement() -> None:
    assert parse_judgement("The author argues against the motion.", TURN) is None


def test_a_quote_containing_a_colon_survives() -> None:
    """partition(':') splits once, so punctuation inside the sentence is safe."""
    turn = "He said: this is indefensible. I agree."
    reply = "POSITION: He said: this is indefensible.\nSIDE: con"
    result = parse_judgement(reply, turn)
    assert result is not None and result.quote == "He said: this is indefensible."
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && export PATH="$PWD/.venv/bin:$PATH" && pytest tests/test_compliance.py -k judgement -v`
Expected: FAIL — `ImportError: cannot import name 'ARGUED_QUOTE_KEY'`

- [ ] **Step 3: Implement**

Add the import at the top of `backend/cicero/core/compliance.py`:

```python
from cicero.core.consensus import parse_directives, parse_stance
from cicero.core.prompts import DIRECTIVE_POSITION, DIRECTIVE_SIDE, NO_POSITION
```

Then, beside `ARGUED_KEY`:

```python
#: The sentence the judge's verdict rests on, verified to appear in the turn.
#: Written with ``ARGUED_KEY`` or not at all: a stance without its evidence is
#: the ungrounded record this feature removes.
ARGUED_QUOTE_KEY = "argued_quote"

_COMPLIANCE_DIRECTIVES = frozenset({DIRECTIVE_POSITION, DIRECTIVE_SIDE})
```

And after `quote_is_grounded`:

```python
@dataclass(frozen=True)
class Judgement:
    """A side, and the sentence in the turn that says so."""

    stance: Stance
    quote: str


def parse_judgement(reply: str, content: str) -> Judgement | None:
    """Read a judge reply, or ``None`` when it cannot be trusted.

    Every rejection path collapses to ``None`` on purpose — a missing directive,
    an unreadable side, ``POSITION: none`` and a quote that is not in the turn
    are all "not measured", and the caller records absence rather than guessing.
    """
    directives, _ = parse_directives(reply, _COMPLIANCE_DIRECTIVES)
    quote = (directives.get(DIRECTIVE_POSITION) or "").strip()
    if not quote or quote.casefold() == NO_POSITION:
        return None
    if not quote_is_grounded(quote, content):
        return None
    stance = parse_stance(directives.get(DIRECTIVE_SIDE) or "")
    if stance is None:
        return None
    return Judgement(stance=stance, quote=quote)
```

Finally, change `ComplianceJudge.judge` to return a `Judgement`:

```python
    async def judge(self, topic: str, content: str) -> Judgement | None:
        """The side ``content`` argues and the sentence that says so, or ``None``.

        ``None`` keeps its F9 meaning — not measured — and now covers a reply
        whose quote could not be found in the turn.
        """
        messages = build_compliance_messages(topic, content)
        try:
            result = await self._provider.generate(messages, self._options)
        except ProviderError:
            return None
        return parse_judgement(result.content, content)
```

Changing the return type breaks `orchestrator.py`, which does
`metadata[ARGUED_KEY] = argued.value` on what is now a `Judgement`. **Update that
call site in the same commit** so the tree never lands type-broken — Task 6 adds
the tests and the mock support around it:

```python
                if judge is not None and content:
                    judgement = await judge.judge(chamber.topic, content)
                    if judgement is not None:
                        metadata[ARGUED_KEY] = judgement.stance.value
                        metadata[ARGUED_QUOTE_KEY] = judgement.quote
```

with the import extended to `from cicero.core.compliance import ARGUED_KEY, ARGUED_QUOTE_KEY, ComplianceJudge`.
The judge block must stay **outside** the debater's `try/except` — that handler
discards the turn and blames the debater's provider. Do not move it.

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && export PATH="$PWD/.venv/bin:$PATH" && pytest -q && ruff check . && mypy cicero`
Expected: full suite passes, clean lint and types. A `mypy` error on `orchestrator.py` means the call site above was missed.

- [ ] **Step 5: Commit**

```bash
git add backend/cicero/core/compliance.py backend/tests/test_compliance.py
git commit -m "feat: parse the judge reply into a grounded judgement (F10)"
```

---

### Task 6: Record the quote, and keep the offline path working

**Files:**
- Modify: `backend/cicero/providers/mock.py`
- Test: `backend/tests/test_orchestrator.py`, `backend/tests/test_mock_provider.py`

**Interfaces:**
- Consumes: `Judgement`, `ARGUED_KEY`, `ARGUED_QUOTE_KEY` (Task 5)

Task 5 already updated the orchestrator call site so its commit stayed green. This task adds the tests that pin that behaviour, and the mock support without which they cannot pass.

`MockProvider` must emit a *grounded* quote or every offline judgement is discarded and `make demo` stops covering the feature. It can copy the first line of the turn, which it already receives between the transcript markers. The provider layer keeps its own literals rather than importing `core.prompts`, with a drift test — the established pattern for `POLL_MARKER` and `COMPLIANCE_MARKER`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_orchestrator.py`:

```python
from cicero.core.compliance import ARGUED_KEY, ARGUED_QUOTE_KEY


async def test_a_judged_turn_records_the_quote_beside_the_stance() -> None:
    chamber, factory, repo = _judged_chamber()
    judge = ComplianceJudge(MockProvider(), "mock-small")
    result = await _engine(factory, repo, judge).run(
        chamber, DebateBudget(max_rounds=1, max_total_tokens=100_000)
    )
    debate_turns = [t for t in result.turns if t.participant_id is not None]
    assert debate_turns
    for turn in debate_turns:
        assert ARGUED_KEY in turn.metadata
        quote = turn.metadata[ARGUED_QUOTE_KEY]
        assert isinstance(quote, str) and quote
        # The recorded evidence must be in the turn it describes.
        assert " ".join(quote.split()).casefold() in " ".join(turn.content.split()).casefold()


async def test_an_ungrounded_reply_records_neither_key() -> None:
    """A judge that paraphrases leaves no trace at all — not a stance without
    evidence."""
    chamber, factory, repo = _judged_chamber()
    judge = ComplianceJudge(
        MockProvider(scripted=["POSITION: a sentence not in the turn\nSIDE: con"] * 50),
        "mock-small",
    )
    result = await _engine(factory, repo, judge).run(
        chamber, DebateBudget(max_rounds=1, max_total_tokens=100_000)
    )
    debate_turns = [t for t in result.turns if t.participant_id is not None]
    assert debate_turns
    assert all(ARGUED_KEY not in t.metadata for t in debate_turns)
    assert all(ARGUED_QUOTE_KEY not in t.metadata for t in debate_turns)
```

Add to `backend/tests/test_mock_provider.py`:

```python
from cicero.core.compliance import parse_judgement
from cicero.providers.mock import TRANSCRIPT_CLOSE_MARKER, TRANSCRIPT_OPEN_MARKER


async def test_mock_emits_a_grounded_two_directive_judgement() -> None:
    provider = MockProvider()
    content = "Invading would violate the UN Charter. I oppose the motion."
    messages = build_compliance_messages("a motion", content)
    result = await provider.generate(messages, GenerateOptions(model="mock-small"))
    assert parse_judgement(result.content, content) is not None


def test_transcript_markers_match_the_real_prompt() -> None:
    """The provider layer keeps its own literals so it does not import
    core.prompts; this is what stops the two drifting apart."""
    assert TRANSCRIPT_OPEN_MARKER == prompts.TRANSCRIPT_OPEN
    assert TRANSCRIPT_CLOSE_MARKER == prompts.TRANSCRIPT_CLOSE
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && export PATH="$PWD/.venv/bin:$PATH" && pytest tests/test_orchestrator.py tests/test_mock_provider.py -k "quote or grounded or marker" -v`
Expected: FAIL — `ImportError: cannot import name 'ARGUED_QUOTE_KEY'` in the orchestrator test, and `TRANSCRIPT_OPEN_MARKER` missing in the mock test.

- [ ] **Step 3: Implement**

`orchestrator.py` already writes both keys (Task 5). In
`backend/cicero/providers/mock.py`, add beside `COMPLIANCE_MARKER`:

```python
#: The delimiters the compliance prompt wraps the judged turn in. Literals, so
#: the provider layer stays independent of ``core.prompts``; a test asserts the
#: two never drift apart.
TRANSCRIPT_OPEN_MARKER = "<<<TRANSCRIPT>>>"
TRANSCRIPT_CLOSE_MARKER = "<<<END_TRANSCRIPT>>>"


def _first_sentence_of_judged_turn(user_message: str) -> str:
    """The first non-empty line of the turn the judge was asked to read.

    A mock has no view on which side an argument takes, but its answer still has
    to be *grounded* or the real parser discards it and the offline path stops
    exercising the feature. Copying a line out of the turn is the cheapest way to
    produce a quote that genuinely appears in it.
    """
    _, _, rest = user_message.partition(TRANSCRIPT_OPEN_MARKER)
    body, _, _ = rest.partition(TRANSCRIPT_CLOSE_MARKER)
    for line in body.splitlines():
        if line.strip():
            return line.strip()
    return ""
```

and change the compliance branch in `generate`:

```python
        elif COMPLIANCE_MARKER in system.lower():
            quoted = _first_sentence_of_judged_turn(last)
            content = f"POSITION: {quoted}\nSIDE: {self._compliance_answer}"
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd backend && export PATH="$PWD/.venv/bin:$PATH" && pytest -q && ruff check . && mypy cicero`
Expected: full suite passes, clean lint and types.

- [ ] **Step 5: Confirm the offline path still exercises the feature**

An Ollama on this machine makes `make demo` prefer live models (issue #15), so force the mock:

```bash
cd backend && OLLAMA_HOST=http://localhost:1 make demo
```
Expected: `only mock available` in the output, and `0 failed`. If it reports a live provider, stop — the run proves nothing about the offline path.

- [ ] **Step 6: Commit**

```bash
git add backend/cicero/core/orchestrator.py backend/cicero/providers/mock.py backend/tests/
git commit -m "feat: record the judge's evidence beside its verdict (F10)"
```

---

### Task 7: Re-judge a concluded chamber

**Files:**
- Modify: `backend/cicero/api/routers/chambers.py`
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Produces: `POST /chambers/{chamber_id}/compliance/rejudge` → `Chamber`; and `_compliance_judge_for(chamber, factory) -> ComplianceJudge`, extracted from `_build_engine`.

**Deviation from the spec, deliberate.** The spec said `check_compliance_judge.py` gains a `--write` flag. That cannot reach the chambers that matter: they live in the Docker volume behind the API, and `scripts/` is excluded from the image by `backend/.dockerignore`, so the script is not even present in the container. No endpoint writes turn metadata either. A server-side endpoint is the only route that works against a Dockerised deployment, and it removes the need for a client to hold write access to the store. The script keeps its read-only measuring role, which is all the validation gate needs.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_api.py`, following the file's existing `TestClient` construction:

These follow `test_build_engine_wires_the_moderators_compliance_judge` (around line 954) for client construction — `InMemoryChamberRepository`, `ConstantFactory`, `create_app()` with the three dependency overrides, inside a `TestClient` context.

```python
def _concluded_chamber_with_stale_compliance(
    repo: InMemoryChamberRepository, stale: dict[str, object]
) -> Chamber:
    """A concluded one-turn chamber whose turn already carries a judgement."""
    debater = Participant(
        display_name="Ada", provider=ProviderType.MOCK, model="mock-small",
        stance=Stance.PRO,
    )
    chamber = Chamber(
        topic="Should we colonise Mars?",
        status=ChamberStatus.CONCLUDED,
        participants=[debater],
        moderator=Moderator(provider=ProviderType.MOCK, model="mock-small"),
    )
    chamber.turns = [
        Turn(
            participant_id=debater.id,
            round_index=0,
            content="Colonising Mars is worth the cost. I support the motion.",
            metadata=dict(stale),
        )
    ]
    return repo.add(chamber)


def _rejudge_client(repo: InMemoryChamberRepository, provider: MockProvider) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_provider_factory] = lambda: ConstantFactory(provider)
    app.dependency_overrides[get_debate_manager] = lambda: DebateManager()
    return TestClient(app)


def test_rejudge_rewrites_compliance_on_a_concluded_chamber() -> None:
    """The point of the endpoint: a chamber judged by an older protocol is
    brought up to date without re-running the debate."""
    repo = InMemoryChamberRepository()
    chamber = _concluded_chamber_with_stale_compliance(repo, {"argued": "con"})
    with _rejudge_client(repo, MockProvider(compliance_answer="pro")) as client:
        resp = client.post(f"/chambers/{chamber.id}/compliance/rejudge")
    assert resp.status_code == 200
    turn = resp.json()["turns"][0]
    assert turn["metadata"]["argued"] == "pro"
    # MockProvider quotes the first line of the turn, so the evidence is real.
    assert turn["metadata"]["argued_quote"] in turn["content"]


def test_rejudge_clears_a_stale_verdict_it_can_no_longer_ground() -> None:
    """Re-judging replaces, it does not merge. A turn whose new reply fails the
    grounding check must lose its old verdict, or the chamber keeps one that
    nothing supports."""
    repo = InMemoryChamberRepository()
    chamber = _concluded_chamber_with_stale_compliance(repo, {"argued": "con"})
    ungrounded = MockProvider(scripted=["POSITION: a sentence not in the turn\nSIDE: pro"])
    with _rejudge_client(repo, ungrounded) as client:
        resp = client.post(f"/chambers/{chamber.id}/compliance/rejudge")
    assert resp.status_code == 200
    metadata = resp.json()["turns"][0]["metadata"]
    assert "argued" not in metadata
    assert "argued_quote" not in metadata


def test_rejudge_refuses_a_chamber_that_is_not_concluded() -> None:
    """A running debate is being written by the engine; a concurrent rewrite of
    its turns is how one gets corrupted."""
    repo = InMemoryChamberRepository()
    chamber = _concluded_chamber_with_stale_compliance(repo, {})
    chamber.status = ChamberStatus.RUNNING
    repo.update(chamber)
    with _rejudge_client(repo, MockProvider()) as client:
        resp = client.post(f"/chambers/{chamber.id}/compliance/rejudge")
    assert resp.status_code == 409
```

Add whatever of `Chamber`, `Participant`, `Turn`, `Moderator`, `ChamberStatus`, `ProviderType`, `Stance` the file does not already import.

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && export PATH="$PWD/.venv/bin:$PATH" && pytest tests/test_api.py -k rejudge -v`
Expected: FAIL — 404, the route does not exist.

- [ ] **Step 3: Implement**

First extract the judge builder from `_build_engine` so both callers share it:

```python
def _compliance_judge_for(chamber: Chamber, factory: ProviderFactory) -> ComplianceJudge:
    """The chamber's moderator, wired as its compliance judge."""
    config = chamber.moderator
    if config is None:
        source = chamber.participants[0]
        config = Moderator(provider=source.provider, model=source.model)
    return ComplianceJudge(factory.get_for_type(config.provider), config.model)
```

Use it inside `_build_engine` in place of the inline construction, then add the route:

```python
@router.post("/{chamber_id}/compliance/rejudge", response_model=Chamber)
async def rejudge_compliance(
    chamber_id: UUID, repo: RepoDep, factory: FactoryDep
) -> Chamber:
    """Re-judge every turn's compliance with the current protocol (FR-34).

    Concluded chambers only: a running debate is being written by the engine, and
    a concurrent rewrite of its turns is how one gets corrupted.
    """
    chamber = _require_chamber(repo, chamber_id)
    if chamber.status is not ChamberStatus.CONCLUDED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "compliance can only be re-judged on a concluded chamber",
        )
    judge = _compliance_judge_for(chamber, factory)
    for turn in chamber.turns:
        if turn.participant_id is None or not turn.content:
            continue
        judgement = await judge.judge(chamber.topic, turn.content)
        # Cleared first: a turn the judge can no longer ground must lose its old
        # verdict rather than keep one nothing supports.
        turn.metadata.pop(ARGUED_KEY, None)
        turn.metadata.pop(ARGUED_QUOTE_KEY, None)
        if judgement is not None:
            turn.metadata[ARGUED_KEY] = judgement.stance.value
            turn.metadata[ARGUED_QUOTE_KEY] = judgement.quote
    return repo.update(chamber)
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd backend && export PATH="$PWD/.venv/bin:$PATH" && pytest -q && ruff check . && mypy cicero`
Expected: all pass, clean.

- [ ] **Step 5: Commit**

```bash
git add backend/cicero/api/routers/chambers.py backend/tests/test_api.py
git commit -m "feat: re-judge compliance on a concluded chamber (F10)"
```

---

### Task 8: Teach the script to record quotes

**Files:**
- Modify: `backend/scripts/check_compliance_judge.py`

**Interfaces:**
- Consumes: `Judgement` (Task 5)

`judge()` now returns a `Judgement`, so the script's verdict map needs updating or it will write objects it cannot serialise.

- [ ] **Step 1: Update the script**

In `_judge_chamber`, record both the side and the quote:

```python
        judgement = await judge.judge(chamber_topic, turn_content)
        verdicts[turn_id] = None if judgement is None else judgement.stance.value
        quotes[turn_id] = None if judgement is None else judgement.quote
```

Write the quotes into the verdicts JSON as a sibling map so the comparison scripts in Tasks 2 and 9 keep working unchanged against the `{turn_id: side}` shape:

```json
{"verdicts": {"<id>": "con"}, "quotes": {"<id>": "I therefore oppose the motion."}}
```

**This changes the verdicts file shape.** Task 9's comparison snippet already reads `["verdicts"]` for the new file and the flat mapping for the baseline — that asymmetry is correct and deliberate, because `f10_baseline_verdicts.json` was written by the old script in Task 1 and must not be regenerated. Note it in the script's docstring so the next reader is not surprised. Task 2's snippet reads the flat baseline and needs no change.

- [ ] **Step 2: Verify it runs**

```bash
cd backend && export PATH="$PWD/.venv/bin:$PATH" && ruff check . && mypy --strict scripts/check_compliance_judge.py
```
Expected: clean. A live run happens in Task 9.

- [ ] **Step 3: Commit**

```bash
git add backend/scripts/check_compliance_judge.py
git commit -m "test: record judge quotes alongside verdicts in the harness (F10)"
```

---

### Task 9: The gate — head-to-head measurement

**Files:**
- Create: `backend/tests/fixtures/f10_new_verdicts.json`

**Interfaces:**
- Consumes: all fixtures from Tasks 1-2, the new protocol from Tasks 3-6

**This task decides whether the feature ships.** It is allowed to conclude "do not ship".

- [ ] **Step 1: Run the new protocol over the same 32 turns**

```bash
cd backend && export PATH="$PWD/.venv/bin:$PATH"
python scripts/check_compliance_judge.py <chamber-id> \
  --model qwen3:30b \
  --turns-out /tmp/f10_turns_check.md \
  --verdicts-out tests/fixtures/f10_new_verdicts.json
```

Same chamber id and same `--model qwen3:30b` as Task 1 — changing either makes the comparison meaningless.

- [ ] **Step 2: Compute both numbers, both protocols**

```bash
cd backend && python -c "
import json
h=json.load(open('tests/fixtures/f10_hand_labels.json'))
base=json.load(open('tests/fixtures/f10_baseline_verdicts.json'))
new=json.load(open('tests/fixtures/f10_new_verdicts.json'))['verdicts']
def score(v,name):
    agree=sum(1 for k,x in h.items() if v.get(k)==x)
    un=sum(1 for k in h if v.get(k) is None)
    print(f'{name}: agreement {agree}/{len(h)}  unmeasured {un}/{len(h)} ({100*un//len(h)}%)')
    return agree,un
score(base,'baseline')
score(new,'new     ')
for k,x in h.items():
    if new.get(k)!=x: print('  new MISMATCH',k[:8],'hand:',x,'judge:',new.get(k))
"
```

- [ ] **Step 3: Re-run the easy set for non-regression**

Re-run the four chambers from F9's validation through the new protocol and compare against `f9`'s hand labels if still available; if they are not, hand-read any turn the new protocol now disagrees with. The requirement is that the new protocol does not *lose* turns the old one got right.

- [ ] **Step 4: Apply the gate**

Ship only if **all three** hold:

1. Agreement on the 32-turn invasion set beats the baseline from Task 2.
2. The easy set does not regress.
3. Unmeasured turns ≤ **25%** on both sets.

If any fails, **stop and report**. Do not tune the prompt until the number improves — that is fitting to the test set. The spec names the two fallbacks: document the limitation, or drop the per-debater lines and keep only the caveat.

- [ ] **Step 5: Commit the measurement**

```bash
git add backend/tests/fixtures/f10_new_verdicts.json
git commit -m "test: F10 head-to-head measurement — <new> vs <baseline> agreement"
```

---

### Task 10: Documentation and traceability

**Files:**
- Modify: `docs/requirements.md`, `docs/backlog.md`, `docs/model-selection.md`, `docs/superpowers/decisions/2026-08-06-debaters-do-not-hold-assigned-sides.md`, `docs/architecture.md`

- [ ] **Step 1: Amend FR-34**

FR-34 currently says the judged side is read from prose. Add that the judge must quote the sentence its verdict rests on, and that an unverifiable quote records nothing.

- [ ] **Step 2: Add backlog story F10**

Add a row to the Epic F table after F9, in the style of F5–F9, carrying the **real** measured numbers from Task 9 — agreement before and after, and the unmeasured rate. No placeholders.

- [ ] **Step 3: Update the decision record**

Add a section recording the rebuttal failure mode, the four hand-read errors that exposed it, and the head-to-head result. If the gate failed, record that too — the document already carries six failed mechanisms and a seventh belongs beside them.

- [ ] **Step 4: Update `docs/model-selection.md` §4**

The judge-agreement figure quoted there is `20 of 21` on `llama3.1`. Add the invasion-set numbers and say plainly that agreement is task-dependent as well as model-dependent: rebuttal-dense prose is measurably harder than the policy debates the original figure came from.

- [ ] **Step 5: Document the endpoint**

Add `POST /chambers/{id}/compliance/rejudge` to the endpoint table in `docs/architecture.md`, noting the concluded-only guard.

- [ ] **Step 6: Verify and commit**

```bash
cd /Users/feaa/Code/cicero && grep -n "F10\|rejudge" docs/requirements.md docs/backlog.md docs/architecture.md docs/model-selection.md | head
git add docs/ && git commit -m "docs: record judge grounding (F10)"
```

---

### Task 11: Whole-branch verification

- [ ] **Step 1: Backend gate**

```bash
cd backend && export PATH="$PWD/.venv/bin:$PATH" && ruff check . && mypy cicero && pytest
```
Expected: all pass. Record the test count.

- [ ] **Step 2: Frontend gate**

```bash
cd frontend && npm run typecheck && npm run lint && npm test && npm run build
```
Expected: all pass. No frontend change is expected in this plan; a failure here means something leaked.

- [ ] **Step 3: Scoped mutation**

From a Python 3.11 venv, and **not** while any test run is in flight:

```bash
cd backend && mutmut run --paths-to-mutate cicero/core/compliance.py && mutmut results
git status --short
```
Expected: every mutant killed, or each survivor named and argued equivalent. `git status` must be clean — no `.bak`, no mutated source left behind.

- [ ] **Step 4: Offline demo**

```bash
cd backend && OLLAMA_HOST=http://localhost:1 make demo
```
Expected: `only mock available`, 0 failed.

- [ ] **Step 5: Re-judge the invasion chamber and read the result**

```bash
curl -s -X POST http://localhost:8000/chambers/<chamber-id>/compliance/rejudge | python3 -c "
import sys, json
d=json.load(sys.stdin)
who={p['id']:(p['display_name'],p['stance']) for p in d['participants']}
for t in d['turns']:
    if not t.get('participant_id'): continue
    n,s=who[t['participant_id']]
    print(f\"r{t['round_index']} {n:8} assigned={s:8} argued={t['metadata'].get('argued')} quote={(t['metadata'].get('argued_quote') or '')[:60]!r}\")
"
```
Read the quotes. Each should be a sentence in which that debater states their own position — this is the audit the whole design exists to make possible, and the first chance to use it.

- [ ] **Step 6: Confirm nothing became load-bearing**

```bash
cd backend && grep -rn "compliance\|ARGUED_KEY\|argued" cicero/core/budget.py cicero/core/consensus.py cicero/core/repetition.py cicero/core/roster.py cicero/core/state_machine.py
```
Expected: no matches. The spec forbids this signal reaching any decision.

---

## Self-Review

**Spec coverage:** §1 protocol → Tasks 4, 5. §2 grounding check → Task 3. §3 what is recorded → Tasks 5, 6. §4 prompt → Task 4. §5 interface → Task 5. §6 re-judging → Task 7 (with the documented deviation). Validation → Tasks 1, 2, 8, 9. Testing → Tasks 3, 5, 6, 7, 11. Risks → surfaced in Task 9's gate and Task 10's docs.

**Ordering constraint honoured:** Task 1 captures the baseline before Task 3 touches code, which the spec requires and which nothing else in the plan can substitute for.

**Naming consistency:** `ARGUED_KEY` / `ARGUED_QUOTE_KEY`, `Judgement(stance, quote)`, `quote_is_grounded(quote, content)`, `parse_judgement(reply, content)`, `_compliance_judge_for(chamber, factory)` are used identically wherever they appear.

**Placeholder scan:** clean. Task 7's tests were initially described rather than written — the exact failure this skill names — and are now written out against `test_api.py`'s real construction pattern.

**Known soft spot:** Task 9's non-regression step (the easy set) depends on F9's hand labels, which were archived to a gitignored workspace that has since been deleted. If they cannot be recovered from the F9 commits, that step degrades to hand-reading only the turns where the two protocols disagree — stated in the step itself so the implementer does not discover it mid-task.
