# Cicero — Product Backlog

> **Status:** Draft v0.1. Derived from [`requirements.md`](./requirements.md).
> Priority uses MoSCoW. Estimates are relative story points (Fibonacci).
> Stories are grouped into epics and sequenced into milestones. Traceability
> (`FR-*` / `NFR-*`) links each story back to a requirement.

## Legend
- **Priority:** M = Must, S = Should, C = Could, W = Won't (this release)
- **Est:** relative story points (1, 2, 3, 5, 8, 13)
- **Status:** `todo` (default). Updated as work progresses.

---

## Epic A — Project Foundation & Security Baseline
*Goal: a safe, testable skeleton before any feature code. Security-first because the repo is public.*

| ID | Story | FR/NFR | Pri | Est | Status |
|----|-------|--------|-----|-----|--------|
| A1 | As an operator, I have a repo scaffold (backend/frontend layout, package config, formatting/lint/type-check) so contributions are consistent. | NFR-M-3, NFR-Q-4 | M | 3 | ✅ done |
| A2 | As a security-conscious owner, all secrets load from env/untracked config and `.env`/secrets are git-ignored, so nothing sensitive is committed. | NFR-SEC-1/2/3 | M | 2 | ✅ done |
| A3 | As an operator, CI runs lint, type-check, tests, secret scanning, and dependency (SCA) scanning on every push. | NFR-Q-1/4, NFR-SEC-1/7 | M | 5 | ✅ done |
| A4 | As a developer, mutation testing is wired up with a configured threshold on core modules and runs in CI. | NFR-Q-2 | M | 5 | ✅ done |
| A5 | As a maintainer, `SECURITY.md`, threat model, and a CONTRIBUTING guide exist. | NFR-SEC-10, NFR-M-2 | M | 2 | ✅ done |
| A6 | As an operator, the app runs locally from a documented quickstart (and optionally Docker Compose). | NFR-O-1/2/3 | S | 3 | 🟡 partial |

## Epic B — Domain Model & Persistence
*Goal: the core entities and their storage.*

| ID | Story | FR/NFR | Pri | Est | Status |
|----|-------|--------|-----|-----|--------|
| B1 | As a developer, the domain model (Chamber, Topic, Participant, Turn, Round, ConsensusResult) is defined with validation. | FR-1/6/7/17/23 | M | 5 | ✅ done |
| B2 | As a developer, entities persist to a store (SQLite default) via a repository layer, with migrations. | FR-2/17, NFR-O-3 | M | 5 | ✅ done |
| B3 | As a developer, the chamber lifecycle state machine (`draft→running→paused→concluded→archived`) is enforced. | FR-4 | S | 3 | ✅ done |

## Epic C — Provider Abstraction
*Goal: pluggable LLM providers behind one interface.*

| ID | Story | FR/NFR | Pri | Est | Status |
|----|-------|--------|-----|-----|--------|
| C1 | As a developer, a `Provider` interface defines `generate()`/streaming, model listing, and capability flags. | FR-10, NFR-M-1 | M | 5 | ✅ done |
| C2 | As a user, an **Ollama** provider connects to a local Ollama server and generates turns. | FR-8 | M | 5 | ✅ done |
| C3 | As a user, an **Anthropic** provider generates turns using an env-supplied API key. | FR-9, NFR-SEC-1/3 | M | 5 | ✅ done |
| C4 | As a user, adding a participant validates connectivity / model availability. | FR-12 | S | 3 | 🟡 partial — UI model selector lists the provider's live models (`GET /providers/{p}/models`); server-side validation on add still todo |
| C5 | As a developer, provider failures are handled with retries/backoff and clear errors without crashing the debate. | NFR-R-1 | S | 3 | 🟡 partial |
| C6 | As a developer, a `Mock`/`Stub` provider enables deterministic tests with no live calls. | NFR-Q-1/3 | M | 2 | ✅ done |

## Epic D — Chamber & Participant Management (API)
*Goal: manage chambers and participants over the API.*

| ID | Story | FR/NFR | Pri | Est | Status |
|----|-------|--------|-----|-----|--------|
| D1 | As a user, I can create/list/view/delete chambers with topic, category, and description. | FR-1/2 | M | 3 | ✅ done |
| D2 | As a user, I can add participants with provider, model, name, and stance (default `neutral`). | FR-6/7 | M | 3 | ✅ done |
| D3 | As a user, I can set per-participant tuning (temperature, max tokens, persona/style). | FR-11 | S | 3 | 🟡 partial — API accepts tuning on add; per-chamber debate settings (rounds/tokens/duration/decision rule) tunable via API+UI; per-participant tuning UI still todo |
| D4 | As a user, I can edit a chamber while it is a `draft` — its topic/category/description, and the participant roster (add, edit, remove) — including on a **clone before its rerun**, so a rerun can change one variable. | FR-3 | S | 3 | ✅ done — `PATCH /chambers/{id}`, `PATCH`/`DELETE /chambers/{id}/participants/{pid}`, plus edit/remove controls in the UI |
| D5 | As a developer, all API inputs are validated and errors are structured/safe. | NFR-SEC-6 | M | 2 | ✅ done |
| D6 | As a user, I can remove or mute a participant **mid-debate**. | FR-13 | C | 5 | todo — split out of the original D4 (see note below) |

> **Why D4 and D6 are separate.** The original D4 bundled FR-3 (edit while
> `draft`) with FR-13 (remove/mute mid-debate); they are very different jobs.
> D4 touches only draft-state CRUD — the chamber has no turns, so the existing
> draft guard is the whole safety story. D6 reaches into the engine:
> `participants_spoken()` and `round_complete()` in `core/orchestrator.py` both
> assume a fixed roster, and `resume_round()` — and therefore the step control —
> derives its resume point from "has everyone spoken in this round?". Dropping a
> debater mid-round would make an already-complete round read as incomplete (or
> the reverse), and muting needs an explicit decision on whether a muted debater
> still counts toward round completion, still gets polled for its stance, and
> still counts in the `unanimous`/`majority` tallies in `core/consensus.py`.

## Epic E — Debate Engine (turn-based group chat)
*Goal: the core orchestration loop.*

| ID | Story | FR/NFR | Pri | Est | Status |
|----|-------|--------|-----|-----|--------|
| E1 | As a user, participants debate in turns (round-robin), each seeing the shared transcript and arguing per stance. | FR-14/15/20 | M | 8 | ✅ done |
| E2 | As a developer, prompt construction injects topic, stance, persona, transcript window, and rules, with clear delimiting of untrusted content. | FR-15, NFR-SEC-5 | M | 5 | ✅ done |
| E3 | As a user, stop conditions (max rounds / token / time budget) end the debate safely. | FR-16, NFR-SEC-8 | M | 3 | ✅ done |
| E4 | As a user, every turn is persisted with metadata (participant, timestamp, tokens, tools). | FR-17 | M | 3 | ✅ done |
| E5 | As a user, I can start, pause, resume, step, and stop a debate. | FR-19 | S | 5 | ✅ done — start, pause (stop parks as `paused`), resume (continues mid-round), and step (`POST /chambers/{id}/step` runs one turn synchronously, then parks) |
| E6 | As a user, turns stream live as they generate. | FR-18, NFR-R-2 | S | 5 | ✅ done |
| E7 | As an observer, I can inject a moderator note between turns. | FR-21 | C | 3 | ✅ done |

## Epic F — Consensus
*Goal: converge and produce the final artifact.*

| ID | Story | FR/NFR | Pri | Est | Status |
|----|-------|--------|-----|-----|--------|
| F1 | As a developer, a consensus detector decides convergence (hybrid: stance-stability signal + moderator-LLM check), with two-phase prompting (adversarial → convergence) and per-chamber decision rules (unanimous / majority / judge) so a winner can always emerge. | FR-22, OQ-1 | M | 8 | ✅ done |
| F2 | As a user, on convergence the chamber produces a Consensus Statement (or majority Resolution / judge's Verdict, incl. the winning stance) plus each participant's final stance/agreement. | FR-23 | M | 5 | ✅ done |
| F3 | As a user, if no consensus within budget, I get a Summary of Disagreement (positions, cruxes, open points). | FR-24 | S | 5 | ✅ done |
| F4 | As an evaluator, stance changes over time are recorded and viewable. | FR-25 | S | 3 | todo |

## Epic G — Evidence / Web Access (opt-in, sandboxed) — **v1 scope (approved D-3)**
*Goal: participants can cite web evidence — safely. Built behind security controls from the start.*

| ID | Story | FR/NFR | Pri | Est | Status |
|----|-------|--------|-----|-----|--------|
| G1 | As a developer, a controlled web-search+fetch tool exists (off by default, per-chamber opt-in). | FR-26/27 | M | 5 | ✅ done — DuckDuckGo search + `SafeWebClient`; needs `WEB_ACCESS_ENABLED` *and* chamber `settings.web_evidence`. Upfront brief **and** per-turn model-requested searches (`SEARCH:` text protocol for any provider; native tool use on Anthropic), engine-executed, ≤1/turn |
| G2 | As a security owner, the tool enforces SSRF protection, allow/deny lists, timeouts, and size/MIME caps. | FR-29, NFR-SEC-4 | M | 5 | ✅ done |
| G3 | As a developer, fetched content is sanitised and injection-delimited before entering a prompt. | NFR-SEC-5 | M | 3 | ✅ done |
| G4 | As a user, sources used in a turn are recorded as citations and shown in the transcript. | FR-28 | M | 3 | ✅ done — upfront brief is a cited system turn; per-turn searches attach queries + citations to the exact turn that used them |

## Epic H — Review, Export & Observability
*Goal: make debates reviewable and comparable.*

| ID | Story | FR/NFR | Pri | Est | Status |
|----|-------|--------|-----|-----|--------|
| H1 | As a user, I can read a full transcript with per-turn metadata and citations. | FR-30 | S | 3 | ✅ done |
| H2 | As a user, I can export a chamber to JSON and Markdown. | FR-31 | S | 3 | ✅ done |
| H3 | As an evaluator, I can see token/cost/latency metrics per participant and chamber. | FR-33 | S | 3 | ✅ done |
| H4 | As an evaluator, I can compare two runs of the same topic. | FR-32 | C | 5 | ✅ done — `GET /chambers/{a}/compare/{b}` (API) |

## Epic I — User Interface
*Goal: a usable UI to compose and watch debates.*

| ID | Story | FR/NFR | Pri | Est | Status |
|----|-------|--------|-----|-----|--------|
| I1 | As a user, I can create chambers and add participants from a UI. | FR-1/6/7, NFR-U-1 | S | 5 | ✅ done |
| I2 | As a user, I can watch a debate stream live and use start/pause/step/stop controls. | FR-18/19 | S | 5 | ✅ done — live SSE transcript with Start/Resume, Step, and Pause controls |
| I3 | As a user, I can view the consensus/disagreement result and export it. | FR-23/24/31 | S | 3 | ✅ done |
| I4 | As a developer, all model/web-derived content is rendered safely (no XSS). | NFR-SEC-6 | M | 3 | ✅ done |
| I5 | As a user, the UI meets basic accessibility. | NFR-U-2 | C | 3 | 🟡 partial — labelled form controls / aria-labels; no full audit |

## Epic J — Hardening & Release
*Goal: production-readiness for self-hosting.*

| ID | Story | FR/NFR | Pri | Est | Status |
|----|-------|--------|-----|-----|--------|
| J1 | As an operator, security headers, CORS, and (optional) authn/authz are in place before non-localhost use. | NFR-SEC-9 | S | 5 | ✅ done — headers + CORS since M0; optional bearer-token auth via `API_AUTH_TOKEN` |
| J2 | As an operator, rate limiting and resource budgets prevent runaway cost/loops. | NFR-SEC-8 | S | 3 | ✅ done — per-IP rate limiter + per-chamber round/token/time budgets |
| J3 | As an operator, debates can resume after a restart. | NFR-R-3 | C | 5 | ✅ done — startup recovery parks interrupted `running` chambers as `paused`; `POST /chambers/{id}/resume` continues from the first missing turn with budgets counting prior spend |
| J4 | As a maintainer, all docs are verified in sync at release; end-to-end demo passes. | NFR-M-2, §9 | M | 3 | ✅ done — `backend/scripts/demo.py` (`make demo`) walks the §9 path against a live API and asserts a PASS/FAIL row per requirement; run in CI and by `tests/test_demo_e2e.py`. Docs audited against the shipped code (see below) |

---

## Milestones (approved 2026-07-16)
*Reflects approved decisions: Python+React stack, lightweight web UI, web evidence in v1, hybrid consensus, SQLite.*

### Milestone 0 — Walking Skeleton (Foundation + Security) ✅
Epics **A**, **B1/B2**, **C1/C6**. Deliverable: scaffold, CI (lint/type/test/secret/SCA), mutation testing wired, domain model + persistence, mock provider. *No live LLMs yet — everything testable.*

### Milestone 1 — First Real Debate (MVP core)
**C2/C3**, **D1/D2/D5**, **E1/E2/E3/E4**, **F1/F2**. Deliverable: create a chamber, add Ollama + Anthropic participants with stances, run a turn-based debate to a consensus statement — via API.

### Milestone 2 — Usable & Observable
**E5/E6**, **F3/F4**, **H1/H2/H3**, **I1/I2/I3/I4**, **C4/C5**, **B3**, **D3/D4**. Deliverable: lightweight live-streaming web UI, controls, disagreement summaries, exports, metrics.

### Milestone 3 — Web Evidence (v1 feature) & Hardening ← **done**
Epic **G** (SSRF-sandboxed web search + fetch + citations), **J1/J2**, **H4**, **E7**, **I5**, **J3**, **J4**. Deliverable: safe web evidence with citations wired into the debate loop, security hardening, release.
Also delivered here (user-driven): per-chamber **debate settings** (rounds, token budget, wall-clock duration, convergence rounds, decision rule) via API + UI, and the **convergence redesign** (two-phase prompting + unanimous/majority/judge decision rules with a recorded winning stance) so debates end with one position winning. **J3 landed** (restart recovery + pause/resume). **J4 landed** (executable release demo + doc audit).
Carried forward as follow-ups, none blocking the release: full a11y audit (I5), server-side model validation on add (C4), provider retry/backoff (C5), per-participant tuning UI (D3), draft editing (D4) and mid-debate muting (D6), stance-change history (F4), Docker Compose (A6).

### Post-release follow-up — step control (E5/I2) ✅ *2026-08-03*
`POST /chambers/{id}/step` runs exactly one participant turn and parks the chamber
as `paused`, reusing the J3 resume machinery (the engine already finishes partial
rounds), plus a **Step** button in the debate controls. A step that spends the last
round/token/time budget — or that closes a round on consensus — concludes the debate
instead of parking. The release demo now steps a turn by hand before resuming, so
FR-19 has a PASS row for start, step, and resume. Closes **E5** and **I2**.

#### J4 doc audit — drift found and fixed (2026-08-03)
| Doc | Claimed | Actual | Action |
|---|---|---|---|
| `architecture.md` §2, §10 | Python **3.12** | `requires-python >=3.11`; CI pins 3.11 | Corrected to 3.11+ |
| `architecture.md` §2 | SQLAlchemy + **Alembic migrations** | Schema bootstrapped via `metadata.create_all`; no Alembic | Corrected, with a note on when migrations land |
| `architecture.md` §4 | ER model without `settings` / `winning_stance` | Both shipped, plus system-authored turns | Diagram + note updated |
| `architecture.md` §9 | Layout incl. `docker-compose.yml`, `migrations/` | Neither exists; `scripts/` does | Layout matched to the tree; gap called out |
| `architecture.md` §11 | 5 endpoints listed | 15 endpoints shipped (resume, clone, compare, notes, settings, providers, …) | Full table added |
| `architecture.md` | J3 resume/recovery undocumented | Shipped in the previous PR | New §12 |
| `requirements.md` §10 | 5 open questions "to resolve" | All closed at architecture approval (D-1…D-5) | Resolutions recorded; doc baselined v1.0 |
| `testing.md` §3 | Mutation scope "Milestone 1"; `metrics`/`export` listed as in-gate | `setup.cfg` mutated neither, and omitted 4 newer modules | `metrics`+`export` added to the gate (100% branch coverage first); scope list matched to `setup.cfg` |
| `README.md` | "Planned tech (proposed — pending approval)"; Python 3.12 | Stack approved 2026-07-16 and shipped on 3.11 | Rewritten as the shipped stack |

---

## Cross-cutting "Definition of Done" (every story)
1. Code + tests (unit/integration) with providers mocked.
2. Mutation score on touched core logic meets threshold (or justified exclusion).
3. Lint/type-check/format clean; secret & dependency scans pass.
4. No secrets/PII added; untrusted content handled safely.
5. Relevant docs (requirements/architecture/testing/security/README) updated.
6. Traceability link (`FR-*`/`NFR-*`) recorded.
