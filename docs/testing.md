# Cicero — Testing & Mutation-Testing Strategy

> **Status:** Draft v0.1. Supports [`architecture.md`](./architecture.md) §8 and
> NFR-Q-* in [`requirements.md`](./requirements.md).

## 1. Principles
- **No live LLM or network calls in CI.** Every provider is mocked; the debate
  engine is exercised through a deterministic `MockProvider` with scripted
  outputs and fixed seeds.
- **Coverage is a floor; mutation score is the signal.** Line coverage tells us
  what code ran; mutation testing tells us whether our tests would *notice a
  bug*.
- **The core is pure and injectable.** Orchestrator, prompt builder, consensus
  engine, state machine, and SSRF guard take their dependencies (provider,
  clock, store, http client) as injected interfaces, so they are fully testable
  in isolation.

## 2. Test layers
| Layer | Tool | Scope |
|---|---|---|
| Unit | pytest (+ pytest-asyncio) | Pure core logic, adapters (with fakes), validation. |
| Integration | pytest | API ↔ core ↔ repository ↔ mock provider, end to end without live models. |
| Contract | pytest | Each provider adapter honours the `Provider` interface. |
| Security | pytest | SSRF rejection, injection delimiting, secret non-leakage, input validation. |
| Mutation | mutmut (backend), StrykerJS (frontend) | Effectiveness of the above on core modules. |
| Frontend unit | Vitest | Components and client logic. |

## 3. Mutation testing (the quality gate)
- **Backend tool:** `mutmut` (fast, simple). `cosmic-ray` is a fallback if we
  need finer operator control. Config lives in `backend/setup.cfg`.
- **Threshold:** **≥ 80%** killed mutants on the in-scope modules, enforced in CI
  by `backend/scripts/check_mutation_score.py` (parses `mutmut junitxml`). Ratchet
  upward as the suite matures.
- **Current status (Milestone 1):** in-scope modules are the debate core —
  `budget`, `state_machine`, `prompt_builder`, `consensus`, `orchestrator` — plus
  `providers/mock.py` and `persistence/memory.py`. A small number of documented
  *equivalent* mutants are accepted (type-annotation `|`→`&` under
  `from __future__ import annotations`; generation `options` that mock providers
  ignore; dead initial defaults always reassigned before use).
- **Workflow:**
  1. `mutmut run` over the in-scope paths.
  2. `python scripts/check_mutation_score.py --min 80` fails the build if the
     killed ratio is below threshold.
  3. Surviving mutants are triaged: **kill** (add/strengthen a test) or
     **justify** (documented equivalent mutant, recorded here).

### 3.1 Scope: what we mutate, and why
Mutation testing is only meaningful on **behavioural logic** — code where a
mutated operator or value produces observably wrong behaviour. Applied to purely
**declarative** code it generates mostly *equivalent* or trivial mutants (e.g.
changing a `max_length=512` bound the tests never probe, or a string constant),
which add CI time and noise without improving real coverage.

We therefore scope the gate to genuine logic and cover the rest with ordinary
validation unit tests:

| In the mutation gate (behavioural) | Excluded from the gate (declarative) — covered by unit tests |
|---|---|
| `core/budget.py` — budgets & stop conditions | `domain/enums.py`, `domain/models.py` — enum values & Pydantic field declarations |
| `core/state_machine.py` — lifecycle transitions | `providers/base.py` — provider interface + request/response DTOs |
| `core/prompt_builder.py` — prompt **assembly** & transcript delimiting | `core/prompts.py` — prompt template **text** (prose) |
| `core/consensus.py` — stance parsing, consensus rule, finalize | `providers/ollama.py`, `providers/anthropic.py` — HTTP I/O adapters (tested via `httpx.MockTransport`) |
| `core/orchestrator.py` — the debate loop | `providers/factory.py` — provider construction/wiring |
| `providers/mock.py` | `persistence/repository.py` — abstract interface (overridden → decorator mutants equivalent) |
| `persistence/memory.py` | `persistence/sqlalchemy_repo.py` — ORM table/column declarations (equivalent under SQLite) |
| `core/metrics.py` — per-participant aggregation | `api/*` — HTTP layer + SSE transport (tested via FastAPI `TestClient` and the async `DebateManager` tests) |
| `core/export.py` — Markdown/JSON rendering | `config.py`, `**/__init__.py` — settings & re-exports |
| _(new logic modules as they land)_ | |

> **Note:** mutmut only mutates **git-tracked** files, so new modules must be
> committed (or staged) before they enter the gate. CI runs on committed code, so
> it always includes them.

**Why `prompt_builder` is split from `prompts`.** Prompt wording is prose:
mutating it produces mostly equivalent/low-value mutants. So the *text* lives in
`core/prompts.py` (excluded) while the *logic* that selects, orders, windows, and
— critically for security — **delimits** untrusted transcript content lives in
`core/prompt_builder.py` (in the gate). Mutation testing thus verifies the tests
that guard the injection-delimiting behaviour (NFR-SEC-5).

The excluded declarative modules are still validated: `tests/test_domain_models.py`
pins the constraints that matter, `tests/test_config.py` pins secret hygiene, the
provider adapters have hermetic HTTP tests, and the API has `TestClient` tests.

## 4. Determinism
- Time, randomness, IDs, and provider responses are injected/fakeable.
- `MockProvider` returns scripted, deterministic turns so consensus and
  stop-condition logic are tested exactly.

## 4a. Frontend testing (Milestone 2+)
The React/TypeScript UI (`frontend/`) has its own gates:
- **Type-check** (`tsc --noEmit`, strict) and **lint** (`eslint`).
- **Unit tests** (`vitest`, jsdom): pure presentation logic (`src/format.ts`) and
  the API client (`src/api.ts`, with `fetch` mocked). No network; `EventSource`
  is guarded so it never runs under tests.
- **Mutation testing**: a **StrykerJS** config (`frontend/stryker.config.json`)
  targets the pure logic (`src/format.ts`, `src/api.ts`), run with
  `npm run mutation`. It is kept out of the default CI job for now to bound CI
  time; the type-check + lint + vitest gates run in CI.

## 5. What "done" means for a story (testing view)
1. New/changed core logic has unit tests.
2. Mutation score on touched core modules ≥ threshold (or a justified exclusion).
3. Security-relevant changes include a security test.
4. Lint, type-check, and format pass.

## 6. CI pipeline (GitHub Actions)
`lint → type-check → unit+integration tests → mutation (targeted) → secret scan → dependency (SCA) scan`.
Any stage failing fails the build.
