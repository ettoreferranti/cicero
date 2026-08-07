# Configurable Moderator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the moderator an explicit, configurable entity instead of silently inheriting model and token budget from `chamber.participants[0]` — fixing a measured 5-in-9 failure to name a winner on the default `judge` rule.

**Architecture:** A new `Moderator` value object on `Chamber`, defaulting to `None` (meaning "use the first participant", preserving today's behaviour). One production call site changes. A bounded retry in `ConsensusEngine.finalize` covers the residual stochastic judge failure. No engine changes — `ConsensusEngine` already takes a provider and options.

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI, pytest (`asyncio_mode = "auto"`), mutmut; React 18 + TypeScript + Vitest.

**Spec:** [`docs/superpowers/specs/2026-08-06-configurable-moderator-design.md`](../specs/2026-08-06-configurable-moderator-design.md)

## Global Constraints

- Backend commands from `backend/`, frontend from `frontend/`.
- `ruff` line length **100**; lint selects `["E","F","I","B","C4","UP","S"]`.
- `mypy` **strict**, `disallow_untyped_defs = true` — full annotations, tests included (`-> None`).
- Every module starts with `from __future__ import annotations`.
- Docstrings explain *why*, not *what*.
- `cicero/core/consensus.py` is in the mutation gate; `cicero/api/*` and `cicero/domain/models.py` are deliberately **excluded** (see `backend/setup.cfg`). Run mutation **scoped and in the foreground**, never bare `make mutation`:
  `source /private/tmp/claude-503/-Users-feaa-Code-cicero/b73d9d80-9498-4433-a10e-59d1baa61e05/scratchpad/venv311/bin/activate && mutmut run --paths-to-mutate <path> && mutmut results`
  (the repo `.venv` is Python 3.14, where mutmut crashes). Check `git status` for a stray `.bak` afterwards.
- Branch `spec/outcome-headline`, already tracking origin. Do not create a branch or open a PR.

---

### Task 1: The `Moderator` entity

**Files:**
- Modify: `backend/cicero/domain/models.py`
- Test: `backend/tests/test_domain_models.py`

**Interfaces:**
- Produces: `Moderator` with `provider: ProviderType`, `model: str`, `max_tokens: int = 4096`, `temperature: float = 0.3`; `Chamber.moderator: Moderator | None = None`

- [ ] **Step 1: Write the failing tests**

```python
def test_moderator_defaults_to_a_budget_a_reasoning_model_can_answer_within() -> None:
    mod = Moderator(provider=ProviderType.OLLAMA, model="qwen3:30b")
    # 2048 left qwen3:30b no room to answer after thinking — measured at 5 failed
    # judge calls in 9. 4096 cleared all 9.
    assert mod.max_tokens == 4096
    assert mod.temperature == 0.3


def test_moderator_rejects_out_of_range_tuning() -> None:
    for bad in ({"max_tokens": 0}, {"max_tokens": 32769}, {"temperature": -0.1},
                {"temperature": 2.1}):
        with pytest.raises(ValidationError):
            Moderator(provider=ProviderType.OLLAMA, model="m", **bad)


def test_moderator_has_no_debater_fields() -> None:
    # It does not argue, take turns, or vote. Reusing Participant would drag in
    # stance/persona/instructions/muted and invite code that iterates the roster
    # into picking it up.
    with pytest.raises(ValidationError):
        Moderator(provider=ProviderType.OLLAMA, model="m", stance="pro")


def test_chamber_without_a_moderator_is_valid() -> None:
    # None means "use the first participant" — today's behaviour, preserved for
    # any caller that omits the field.
    assert make_chamber_for_test().moderator is None
```

Use the file's existing chamber-construction helper for the last test; if it has none, build a `Chamber(topic="t")` inline. Add missing imports (`Moderator`, `ProviderType`, `ValidationError`, `pytest`).

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && python -m pytest tests/test_domain_models.py -v -k moderator`
Expected: FAIL — `ImportError: cannot import name 'Moderator'`

- [ ] **Step 3: Implement**

In `backend/cicero/domain/models.py`, before `Chamber`:

```python
class Moderator(_Base):
    """Who writes the outcome. Not a debater: no stance, no turns, no vote.

    Was implicit — the engine used ``chamber.participants[0]``'s provider and model
    with two module-level constants for its tuning. That made the arbiter depend on
    roster order, and made its token budget unreachable: at the old 2048, a reasoning
    model spent the whole budget thinking and returned nothing, producing no readable
    ``WINNER:`` on 5 of 9 measured judge calls.
    """

    provider: ProviderType
    model: str = Field(min_length=1, max_length=200)
    #: 4096 cleared all 9 measured judge calls where 2048 failed 5; 8192 gained
    #: nothing. A cap, not a target — a model that answers in 300 tokens costs 300.
    max_tokens: int = Field(default=4096, gt=0, le=32768)
    #: Was an invisible module constant. Exposed so a comparison run that wants
    #: reproducible verdicts can set it to 0.
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
```

On `Chamber`, after `settings`:

```python
    #: Who writes the outcome. ``None`` means the first participant, which is what
    #: the engine did before this field existed.
    moderator: Moderator | None = None
```

- [ ] **Step 4: Verify**

Run: `cd backend && python -m pytest -q && make lint && make type`
Expected: all clean.

- [ ] **Step 5: Commit**

```bash
git add backend/cicero/domain/models.py backend/tests/test_domain_models.py
git commit -m "feat: add a Moderator entity to the chamber (F8)"
```

---

### Task 2: Wire it into the engine build

**Files:**
- Modify: `backend/cicero/api/routers/chambers.py` (`_build_engine`, and the two module constants at lines ~70-74)
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: `Moderator`, `Chamber.moderator` (Task 1)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_api.py`. Drive it through the public API so it exercises the real wiring:

```python
def test_configured_moderator_is_used_instead_of_the_first_participant() -> None:
    # The moderator model must come from the chamber's own config, not from
    # whoever happens to be first on the roster.
    repo = InMemoryChamberRepository()
    seen: list[str] = []

    class _Recording(MockProvider):
        async def generate(self, messages, options):  # type: ignore[no-untyped-def]
            seen.append(options.model)
            return await super().generate(messages, options)

    factory = ConstantFactory(_Recording(models=["scripted", "moderator-model"]))
    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_provider_factory] = lambda: factory
    app.dependency_overrides[get_debate_manager] = lambda: DebateManager()
    with TestClient(app) as client:
        resp = client.post(
            "/chambers",
            json={
                "topic": "Should we colonise Mars?",
                "moderator": {"provider": "mock", "model": "moderator-model"},
            },
        )
        assert resp.status_code == 201
        cid = resp.json()["id"]
        _add_participant(client, cid, "Pro-A", "pro")
        _add_participant(client, cid, "Pro-B", "pro")
        client.post(f"/chambers/{cid}/run", params={"wait": "true"})
    app.dependency_overrides.clear()

    # Debater turns use "scripted"; the moderator call must use its own model.
    assert "moderator-model" in seen
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_api.py -v -k configured_moderator`
Expected: FAIL — 422 (the `moderator` key is rejected) or `assert 'moderator-model' in seen`.

- [ ] **Step 3: Implement**

In `chambers.py`, delete `_MODERATOR_MAX_TOKENS` and `_MODERATOR_TEMPERATURE` and their comment block — they become `Moderator` field defaults. Remove the now-unused `DEFAULT_MAX_TOKENS` import if nothing else uses it.

Replace the moderator lines in `_build_engine`:

```python
    moderator_config = chamber.moderator
    if moderator_config is None:
        # Pre-F8 chambers, and any caller that omits the field: the arbiter is the
        # first participant, which is what the engine did before this was explicit.
        source = chamber.participants[0]
        moderator_config = Moderator(provider=source.provider, model=source.model)
    moderator = factory.get_for_type(moderator_config.provider)
    moderator_options = GenerateOptions(
        model=moderator_config.model,
        max_tokens=moderator_config.max_tokens,
        temperature=moderator_config.temperature,
    )
```

`get_for_type`, not `get`: the factory's `get` takes a `Participant` and the moderator is not one.

Import `Moderator` from `cicero.domain.models`.

- [ ] **Step 4: Verify**

Run: `cd backend && python -m pytest -q && make lint && make type`
Expected: all clean. Task 3 adds the API schema; if the test above still 422s at this point, that is expected — re-run it after Task 3.

- [ ] **Step 5: Commit**

```bash
git add backend/cicero/api/routers/chambers.py backend/tests/test_api.py
git commit -m "feat: build the engine from the chamber's moderator config (F8)"
```

---

### Task 3: API — accept, validate, and edit the moderator

**Files:**
- Modify: `backend/cicero/api/schemas.py`, `backend/cicero/api/routers/chambers.py`
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Produces: `ModeratorIn` schema; `moderator` on `ChamberCreate` and `ChamberUpdate`

- [ ] **Step 1: Write the failing tests**

```python
def test_moderator_model_is_validated_at_creation(client: TestClient) -> None:
    # Without this, a wrong moderator model fails only after the whole debate has
    # run and been paid for.
    resp = client.post(
        "/chambers",
        json={"topic": "t", "moderator": {"provider": "mock", "model": "nope"}},
    )
    assert resp.status_code == 422
    assert "nope" in resp.text


def test_moderator_is_editable_while_draft(client: TestClient) -> None:
    cid = _create_chamber(client)
    resp = client.patch(
        f"/chambers/{cid}",
        json={"moderator": {"provider": "mock", "model": "scripted-large"}},
    )
    assert resp.status_code == 200
    assert resp.json()["moderator"]["model"] == "scripted-large"


def test_moderator_is_frozen_once_the_debate_has_run(client: TestClient) -> None:
    cid = _create_chamber(client)
    _add_participant(client, cid, "Pro-A", "pro")
    _add_participant(client, cid, "Pro-B", "pro")
    client.post(f"/chambers/{cid}/run", params={"wait": "true"})
    resp = client.patch(
        f"/chambers/{cid}", json={"moderator": {"provider": "mock", "model": "scripted"}}
    )
    assert resp.status_code == 409
```

Check the file's existing frozen-chamber test for the exact status code the `_require_draft` helper raises and match it.

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && python -m pytest tests/test_api.py -v -k moderator`

- [ ] **Step 3: Implement**

In `schemas.py`:

```python
class ModeratorIn(BaseModel):
    """Who writes the outcome. Tuning is optional; the domain defaults apply."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider: ProviderType
    model: str = Field(min_length=1, max_length=200)
    max_tokens: int | None = Field(default=None, gt=0, le=32768)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
```

Add `moderator: ModeratorIn | None = None` to both `ChamberCreate` and `ChamberUpdate`.

In `chambers.py`, a shared converter that drops unset tuning so the domain defaults apply:

```python
def _moderator_from(payload: ModeratorIn) -> Moderator:
    fields = payload.model_dump(exclude_none=True)
    return Moderator(**fields)
```

`create_chamber` becomes `async def` and validates before constructing:

```python
    moderator = None
    if payload.moderator is not None:
        await _validate_model(factory, payload.moderator.provider, payload.moderator.model)
        moderator = _moderator_from(payload.moderator)
```

It needs `factory: FactoryDep` added to its signature. `update_chamber` likewise becomes `async def`, validates when `moderator` is present, and converts before `setattr` — the loop over `model_dump(exclude_unset=True)` would otherwise assign a `ModeratorIn` where a `Moderator` belongs:

```python
    changes = payload.model_dump(exclude_unset=True)
    if "moderator" in changes:
        await _validate_model(factory, payload.moderator.provider, payload.moderator.model)
        changes["moderator"] = _moderator_from(payload.moderator)
    for field, value in changes.items():
        setattr(chamber, field, value)
```

- [ ] **Step 4: Verify**

Run: `cd backend && python -m pytest -q && make lint && make type`
Expected: all clean, including Task 2's test which should now pass.

- [ ] **Step 5: Commit**

```bash
git add backend/cicero/api/schemas.py backend/cicero/api/routers/chambers.py backend/tests/test_api.py
git commit -m "feat: accept, validate and edit the moderator over the API (F8)"
```

---

### Task 4: The judge retry

**Files:**
- Modify: `backend/cicero/core/consensus.py` (`finalize`)
- Test: `backend/tests/test_consensus.py`

**Interfaces:**
- Produces: `_JUDGE_ATTEMPTS: int = 3`

- [ ] **Step 1: Write the failing tests**

The third test is the one that matters most — without it the retry silently costs three calls on every debate.

```python
class _FlakyJudge(ScriptedProvider):
    """Emits no WINNER line until the nth moderator call."""

    def __init__(self, succeed_on: int) -> None:
        super().__init__()
        self._succeed_on = succeed_on
        self.moderator_calls = 0

    async def generate(self, messages, options):  # type: ignore[no-untyped-def]
        if "moderator" in messages[0].content.lower():
            self.moderator_calls += 1
            body = (
                "HEADLINE: The pro case won.\n\nWINNER: pro\n\nBecause."
                if self.moderator_calls >= self._succeed_on
                else "HEADLINE: Unclear.\n\nI cannot decide."
            )
            return GenerateResult(content=body, prompt_tokens=3, completion_tokens=3)
        return await super().generate(messages, options)


async def test_the_judge_is_retried_until_it_names_a_winner() -> None:
    ada = make_participant("Ada", Stance.PRO)
    zeno = make_participant("Zeno", Stance.CON)
    chamber = make_chamber(ada, zeno)
    chamber.settings.decision_rule = DecisionRule.JUDGE
    provider = _FlakyJudge(succeed_on=2)
    engine = ConsensusEngine(ConstantFactory(provider), provider, GenerateOptions(model="m"))

    result = await engine.finalize(
        chamber, {str(ada.id): Stance.PRO, str(zeno.id): Stance.CON}
    )

    assert result.winning_stance is Stance.PRO
    assert provider.moderator_calls == 2


async def test_an_exhausted_judge_records_no_winner_rather_than_inventing_one() -> None:
    ada = make_participant("Ada", Stance.PRO)
    zeno = make_participant("Zeno", Stance.CON)
    chamber = make_chamber(ada, zeno)
    chamber.settings.decision_rule = DecisionRule.JUDGE
    provider = _FlakyJudge(succeed_on=99)
    engine = ConsensusEngine(ConstantFactory(provider), provider, GenerateOptions(model="m"))

    result = await engine.finalize(
        chamber, {str(ada.id): Stance.PRO, str(zeno.id): Stance.CON}
    )

    assert result.outcome is ConsensusOutcome.VERDICT
    assert result.winning_stance is None
    assert provider.moderator_calls == 3


async def test_a_non_verdict_outcome_is_never_retried() -> None:
    # The other three tasks have no WINNER: line to fail on, so retrying them
    # would cost three calls on every debate for nothing.
    ada = make_participant("Ada", Stance.PRO)
    zeno = make_participant("Zeno", Stance.PRO)
    chamber = make_chamber(ada, zeno)
    provider = _FlakyJudge(succeed_on=99)
    engine = ConsensusEngine(ConstantFactory(provider), provider, GenerateOptions(model="m"))

    result = await engine.finalize(
        chamber, {str(ada.id): Stance.PRO, str(zeno.id): Stance.PRO}
    )

    assert result.outcome is ConsensusOutcome.CONSENSUS
    assert provider.moderator_calls == 1
```

Import `GenerateResult` if not already imported.

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && python -m pytest tests/test_consensus.py -v -k judge_is_retried`
Expected: FAIL — `assert 1 == 2` (no retry happens).

- [ ] **Step 3: Implement**

Add near the other module constants in `consensus.py`:

```python
#: A judge that names no winner is retried this many times before giving up. The
#: failure is stochastic, not a deterministic inability — measured at 5 in 9 calls
#: on the old token budget, 0 in 9 on the current one — so re-asking usually works.
#: Distinct from the provider's own retry, which fires on an *empty* reply and, when
#: it re-asks with reasoning suppressed, makes a reasoning model narrate instead.
_JUDGE_ATTEMPTS = 3
```

In `finalize`, wrap the generate-and-parse:

```python
        keys = (
            MODERATOR_DIRECTIVES
            if outcome is ConsensusOutcome.VERDICT
            else frozenset({DIRECTIVE_HEADLINE})
        )
        attempts = _JUDGE_ATTEMPTS if outcome is ConsensusOutcome.VERDICT else 1
        for _ in range(attempts):
            result = await self._moderator.generate(messages, self._moderator_options)
            reply = parse_moderator_reply(result.content.strip(), keys=keys)
            if outcome is not ConsensusOutcome.VERDICT or reply.winner is not None:
                break
        statement = reply.body.strip() or EMPTY_MODERATOR_STATEMENT
        if outcome is ConsensusOutcome.VERDICT:
            winner = reply.winner
```

The existing single `generate` call above this block moves into the loop — do not leave it in place, or every debate makes one extra call.

- [ ] **Step 4: Verify and gate**

Run: `cd backend && python -m pytest -q && make lint && make type`
Run the scoped mutation on `cicero/core/consensus.py` per Global Constraints. Kill any survivor on a line you added, or justify it as equivalent — `consensus.py` has ~29 known pre-existing survivors in `_moderator_task`/`ConsensusEngine`; those are not yours.

- [ ] **Step 5: Commit**

```bash
git add backend/cicero/core/consensus.py backend/tests/test_consensus.py
git commit -m "feat: retry a judge that names no winner (F8)"
```

---

### Task 5: Surface the moderator in exports and compare

**Files:**
- Modify: `backend/cicero/core/export.py`, `backend/cicero/core/compare.py`
- Test: `backend/tests/test_export.py`, `backend/tests/test_compare.py`

- [ ] **Step 1: Write the failing tests**

```python
# test_export.py
def test_markdown_names_the_moderator() -> None:
    chamber = _chamber_with_debate()
    chamber.moderator = Moderator(provider=ProviderType.MOCK, model="judge-model")
    md = to_markdown(chamber)
    # A transcript that does not say who judged is missing something material.
    assert "**Moderator:** mock/judge-model" in md


def test_markdown_omits_the_moderator_line_when_unset() -> None:
    assert "**Moderator:**" not in to_markdown(_chamber_with_debate())
```

```python
# test_compare.py
def test_run_summary_carries_the_moderator() -> None:
    chamber = make_chamber(make_participant("Ada", Stance.PRO))
    chamber.moderator = Moderator(provider=ProviderType.MOCK, model="judge-model")
    assert summarize_run(chamber)["moderator"] == "mock/judge-model"


def test_run_summary_moderator_is_none_when_unset() -> None:
    assert summarize_run(make_chamber(make_participant("Ada", Stance.PRO)))["moderator"] is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && python -m pytest tests/test_export.py tests/test_compare.py -v -k moderator`

- [ ] **Step 3: Implement**

In `export.py`, after the participants loop's `lines.append("")`:

```python
    if chamber.moderator is not None:
        lines.append(
            f"**Moderator:** {chamber.moderator.provider.value}/{chamber.moderator.model}"
        )
        lines.append("")
```

In `compare.py`, in the dict returned by `summarize_run`, after `"topic"`:

```python
        # Which model judged is exactly the variable a two-run comparison isolates.
        "moderator": (
            f"{chamber.moderator.provider.value}/{chamber.moderator.model}"
            if chamber.moderator is not None
            else None
        ),
```

- [ ] **Step 4: Verify and gate**

Run: `cd backend && python -m pytest -q && make lint && make type`
Run scoped mutation on both `cicero/core/export.py` and `cicero/core/compare.py`.

- [ ] **Step 5: Commit**

```bash
git add backend/cicero/core/export.py backend/cicero/core/compare.py \
        backend/tests/test_export.py backend/tests/test_compare.py
git commit -m "feat: name the moderator in exports and run comparison (F8)"
```

---

### Task 6: The UI

**Files:**
- Modify: `frontend/src/types.ts`, `frontend/src/api.ts`, `frontend/src/App.tsx`, `frontend/src/ChamberDetail.tsx`
- Test: `frontend/src/App.test.tsx` (create if absent) or the nearest existing creation-form test

- [ ] **Step 1: Write the failing test**

Follow the setup in `frontend/src/ChamberDetail.mute.test.tsx` (partial `vi.mock("./api", …)`). Assert that the creation form renders a moderator model field, that it prefills once a participant exists, and that `createChamber` is called with the moderator block.

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test`

- [ ] **Step 3: Implement**

`types.ts`:

```ts
export interface Moderator {
  provider: Provider;
  model: string;
  max_tokens: number;
  temperature: number;
}
```

Add `moderator: Moderator | null;` to `Chamber`.

`api.ts`: extend `createChamber`'s input and `updateChamber`'s patch to carry an optional `moderator: { provider; model; max_tokens?; temperature? }`.

`App.tsx`: a Moderator row above the participant roster in the creation form — provider select, model select, and a collapsible tuning panel for max_tokens/temperature matching `ParticipantForm`'s `⚙` treatment. Prefill from the first participant when one is added and the field is untouched.

`ChamberDetail.tsx`: show the moderator beside the roster, with the same `⚙` summary when its tuning is off-default, and make it editable while the chamber is a draft.

- [ ] **Step 4: Verify**

Run: `cd frontend && npm test && npm run lint && npm run typecheck && npm run build`
Any existing `Chamber` fixture will fail typecheck until `moderator` is added — update each.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat: choose the moderator when creating a chamber (F8)"
```

---

### Task 7: Documentation and verification

**Files:** `docs/backlog.md`, `docs/requirements.md`, `docs/architecture.md`, `README.md`

- [ ] **Step 1: Backlog**

Append an F8 row to the Epic F table recording the measured judge failure (5 of 9 at 2048, 0 of 9 at 4096, 8192 no better) and that the bias hypothesis was measured and not supported.

- [ ] **Step 2: Requirements**

Extend FR-22/23 to say the moderator is a configured entity rather than the first participant, and add a line to §4 Definitions — the current entry says the moderator "may itself be LLM-assisted", which understates it.

- [ ] **Step 3: Architecture**

`docs/architecture.md`'s ER diagram gains `MODERATOR` on `CHAMBER`. Note the diagram already drifts (`STANCE_POLL.unparsed`, `PARTICIPANT.muted`, `CONSENSUS_RESULT.headline` are all absent) — add those too while there, or say explicitly that you did not.

- [ ] **Step 4: Full verification**

Run: `cd backend && make lint && make type && make cov`
Run: `cd frontend && npm test && npm run lint && npm run typecheck && npm run build`
Run: `cd backend && make demo` — note it prefers a reachable live Ollama over the mock, so an unrelated check can fail intermittently; re-run once before investigating.

- [ ] **Step 5: Real-model check**

Create a chamber whose first participant is a small model and whose **moderator is `qwen3:30b`**, with the decision rule set to `judge` and opposed stances so it reaches a tie. Confirm the verdict names a winner. That is the defect this whole spec exists to fix, and only a live run demonstrates it.

- [ ] **Step 6: Commit and push**

```bash
git add docs README.md && git commit -m "docs: record the configurable moderator (F8)" && git push
```

---

## Self-review notes

- **Spec coverage:** entity → Task 1; wiring → Task 2; retry → Task 4; default/validation/editability → Tasks 2-3; surfacing → Tasks 5-6; testing → per task.
- **The retry's cost guard** (`_a_non_verdict_outcome_is_never_retried`) is the test most likely to be skipped and most expensive to omit — it is the only thing standing between this change and three moderator calls on every debate.
- **Task 2's test may fail until Task 3 lands**, since the API rejects the `moderator` key until the schema accepts it. Called out in the task rather than reordered, because the wiring and the schema are separate reviewable units.
