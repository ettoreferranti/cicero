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
- **Current status (Milestone 0):** **100% (52/52 killed)** on the in-scope
  modules.
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
| `core/` (state machine; later: orchestrator, prompt builder, consensus) | `domain/enums.py`, `domain/models.py` — enum values & Pydantic field declarations |
| `providers/mock.py` | `providers/base.py` — provider interface + request/response DTOs |
| `persistence/memory.py` | `persistence/repository.py` — abstract interface (all methods overridden → decorator mutants are equivalent) |
| _(new logic modules as they land)_ | `persistence/sqlalchemy_repo.py` — ORM table/column declarations (bounds/index flags are equivalent under SQLite) |
| | `config.py`, `**/__init__.py` — settings & re-exports |

As behavioural modules are added (Milestone 1+: orchestrator, consensus engine,
prompt builder, SSRF guard) they are added to `paths_to_mutate` in `setup.cfg`.
The excluded declarative modules are still validated: `tests/test_domain_models.py`
pins the constraints that matter (min-lengths, ranges, defaults, `extra=forbid`),
and `tests/test_config.py` pins secret hygiene.

## 4. Determinism
- Time, randomness, IDs, and provider responses are injected/fakeable.
- `MockProvider` returns scripted, deterministic turns so consensus and
  stop-condition logic are tested exactly.

## 5. What "done" means for a story (testing view)
1. New/changed core logic has unit tests.
2. Mutation score on touched core modules ≥ threshold (or a justified exclusion).
3. Security-relevant changes include a security test.
4. Lint, type-check, and format pass.

## 6. CI pipeline (GitHub Actions)
`lint → type-check → unit+integration tests → mutation (targeted) → secret scan → dependency (SCA) scan`.
Any stage failing fails the build.
