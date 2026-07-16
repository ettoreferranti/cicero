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
  need finer operator control.
- **Targeted modules (must meet threshold):**
  `core/orchestrator`, `core/prompt_builder`, `core/consensus`,
  `core/state_machine`, `tools/ssrf_guard`, `persistence/repository` logic.
- **Threshold:** start at **≥ 80%** killed mutants on targeted modules;
  ratchet upward as the suite matures. The threshold is enforced in CI.
- **Workflow:**
  1. `mutmut run` over targeted paths.
  2. CI fails if the killed-ratio on targeted modules drops below threshold.
  3. Surviving mutants are triaged: **kill** (add/strengthen a test) or
     **justify** (documented equivalent mutant / out-of-scope, recorded in the
     mutmut config with a reason).
- **Scope control:** mutation runs are scoped to core paths to keep CI time
  bounded; a nightly/full run may cover more.

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
