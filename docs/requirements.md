# Cicero — Requirements Specification

> **Status:** **Baselined v1.0** — open questions closed at architecture approval
> (2026-07-16, see §10). This document is the single source of truth for *what*
> Cicero must do. The *how* lives in [`architecture.md`](./architecture.md); the
> *when/priority* and delivery status live in [`backlog.md`](./backlog.md).
>
> **Amendments since baseline:** FR-11 extended 2026-08-04 — the vague "debating
> style" is now a specified per-debater **instructions** prompt; see backlog
> story **D7** for the decisions behind it.

---

## 1. Vision

**Cicero** is a virtual debating platform for pitting different Large Language
Models (LLMs) against one another. A user creates a **debate chamber** built
around a topic or question, populates it with LLM **participants** — each drawn
from a configurable provider (e.g. a local Ollama model, or the Anthropic API) —
and assigns each participant a **stance** (pro / con / neutral). The
participants then debate in a turn-based group chat, each trying to persuade the
others, optionally citing evidence gathered from the web, until the chamber
converges on a **consensus statement** that the participants endorse.

The primary purpose is **evaluation and observation**: comparing how different
models reason, argue, use evidence, hold or change a position, and cooperate to
reach consensus.

## 2. Goals & Non-Goals

### 2.1 Goals
- Let a user compose heterogeneous debates across providers (local + hosted).
- Make model reasoning, persuasion, and position changes observable and
  reviewable.
- Produce a defensible, auditable consensus artifact at the end of a debate.
- Support (optional) evidence gathering from the web under strict safety
  controls.
- Be safe to run and safe to open-source (no secrets, no PII in the repo).

### 2.2 Non-Goals (initial release)
- Not a general-purpose chat UI or model playground.
- Not a benchmarking leaderboard / scoring service (may be a later epic).
- Not a fine-tuning or training platform.
- Not a multi-tenant SaaS with billing (single-user / self-hosted first).
- Not a human-vs-LLM debate arena (humans observe and steer, they do not debate
  as participants in v1).

## 3. Stakeholders & Personas

| Persona | Description | Primary needs |
|---|---|---|
| **Researcher / Evaluator** (primary) | Wants to compare model behaviour on contested questions. | Reproducibility, transcripts, evidence trails, export. |
| **Educator / Facilitator** | Uses debates as teaching artifacts. | Clear topics, readable transcripts, safe content. |
| **Developer / Operator** | Runs and extends the platform. | Easy provider plug-ins, config, observability, security. |
| **Casual user** | Curious about how models argue. | Simple setup, watch a debate unfold live. |

## 4. Definitions (Ubiquitous Language)

- **Chamber** — a debate context: a topic/question plus its participants,
  configuration, and transcript.
- **Topic** — the motion/question under debate, with an optional category
  (scientific, political, ethical, …) and description.
- **Participant** — an LLM instance in a chamber, bound to a provider + model +
  stance + persona settings.
- **Provider** — an integration that can produce completions (Ollama, Anthropic,
  …) behind a common interface.
- **Stance** — a participant's assigned position: `pro`, `con`, or `neutral`
  (default `neutral`).
- **Turn** — one participant's contribution in the group chat.
- **Round** — one full cycle in which each participant has taken a turn.
- **Consensus** — the terminal state where participants converge; produces a
  **Consensus Statement**.
- **Moderator** — the system role that orchestrates turns, enforces rules, and
  drives consensus detection (may itself be LLM-assisted).

## 5. Functional Requirements

Requirements use MoSCoW priority: **M**ust / **S**hould / **C**ould / **W**on't (this release).

### 5.1 Chamber management
- **FR-1 (M)** Create a chamber with a topic/question, optional category, and
  optional free-text description/framing.
- **FR-2 (M)** List, view, and delete chambers.
- **FR-3 (S)** Edit a chamber's topic/config while it is in a `draft` state.
- **FR-4 (S)** A chamber has an explicit lifecycle: `draft → running → paused →
  concluded` (and `archived`).
- **FR-5 (C)** Duplicate/clone a chamber (topic + participants) to re-run.

### 5.2 Participants & providers
- **FR-6 (M)** Add one or more participants to a chamber, each configured with a
  provider, a model identifier, and a display name.
- **FR-7 (M)** Assign each participant a stance: `pro` / `con` / `neutral`,
  defaulting to `neutral`.
- **FR-8 (M)** Support an **Ollama** provider (local models via the Ollama HTTP
  API).
- **FR-9 (M)** Support an **Anthropic** provider (Claude models via API key).
- **FR-10 (M)** Provider integrations conform to a common interface so new
  providers can be added without changing the debate engine.
- **FR-11 (S)** Per-participant tuning: temperature, max tokens, a system-prompt
  **persona** (who the debater is), and free-text **instructions** (default
  empty) steering how it argues during the debate — e.g. "be extra polite",
  "always yield your position", "speak in rhyme", "use jokes". Instructions
  influence tone, format and argumentative posture only; they never override the
  engine's structural rules or the transcript-is-data defence (NFR-SEC-5).
- **FR-12 (S)** Validate provider connectivity/model availability when a
  participant is added (e.g. list local Ollama models).
- **FR-13 (C)** Remove or mute a participant mid-debate.

### 5.3 Debate orchestration
- **FR-14 (M)** Run a debate as a **turn-based group chat**: participants speak
  in a defined order; each sees the shared transcript so far.
- **FR-15 (M)** Each turn, a participant produces an argument consistent with its
  stance, may rebut others, and may attempt to persuade.
- **FR-16 (M)** Configurable stop conditions: max rounds, max tokens/time budget,
  or consensus reached — whichever comes first.
- **FR-17 (M)** Persist the full transcript (every turn, with metadata:
  participant, timestamp, token usage, tools used).
- **FR-18 (S)** Stream turns to the UI live as they are generated.
- **FR-19 (S)** Human controls: start, pause, resume, step (one turn), and stop.
- **FR-20 (S)** Configurable turn order (round-robin default; later: moderated /
  reactive ordering).
- **FR-21 (C)** Allow a human observer to inject a moderator note/prompt between
  turns.

### 5.4 Consensus
- **FR-22 (M)** Detect convergence and drive the chamber to a `concluded` state.
  Convergence requires more than an unchanged stance poll: the one-word poll is a
  coarse instrument that does not reliably track what debaters argue, so an early
  stop also requires that the round produced no new argument.
- **FR-23 (M)** Produce a **Consensus Statement**: a synthesized final position
  with each participant's final stance. Every outcome — consensus, majority,
  verdict, or disagreement — additionally carries a **one-sentence headline**
  stating what the chamber concluded (for a disagreement, the unresolved crux),
  because a stance word alone cannot express a compromise and collapses to
  `neutral`.
- **FR-24 (S)** If no consensus is reached within budget, produce a **Summary of
  Disagreement** instead (positions, key cruxes, unresolved points).
- **FR-25 (S)** Record stance changes over time (who moved, when, why) for
  post-hoc analysis. Recorded changes are presented as **what the poll captured**,
  not as a characterisation of the debater: the three-word vocabulary records a
  debater who moved to a compromise as `neutral`, so where that label could be
  misread the display says so and defers to the moderator's statement for what
  actually changed.

### 5.5 Evidence / web access (optional capability)
- **FR-26 (S)** Participants may request web evidence via a controlled **tool**
  (search + fetch) rather than raw internet access.
- **FR-27 (M, if 5.5 enabled)** Web access is **off by default** and enabled
  per-chamber explicitly.
- **FR-28 (M, if 5.5 enabled)** All fetched sources are recorded as citations and
  attached to the turn that used them.
- **FR-29 (M, if 5.5 enabled)** Web access is constrained by allowlist/denylist,
  domain/SSRF protections, size/time limits, and content sanitisation (see
  NFR-Security).

### 5.6 Review, export & observability
- **FR-30 (S)** View a full, readable transcript with per-turn metadata and
  citations.
- **FR-31 (S)** Export a chamber (topic, participants, transcript, consensus) to
  JSON and Markdown.
- **FR-32 (C)** Compare two runs of the same topic side by side.
- **FR-33 (S)** Surface token/cost/latency metrics per participant and per
  chamber.

## 6. Non-Functional Requirements

### 6.1 Security & Privacy (first-class — public repo)
- **NFR-SEC-1 (M)** **No secrets in the repository.** API keys/credentials come
  only from environment variables or an untracked local config; `.env` and
  secret files are git-ignored. CI includes secret scanning.
- **NFR-SEC-2 (M)** **No PII / confidential data committed.** Sample data,
  fixtures, and docs use non-personal placeholders.
- **NFR-SEC-3 (M)** Secrets never logged, never returned by the API, never sent
  to the frontend.
- **NFR-SEC-4 (M)** **Web/tool access is sandboxed:** SSRF protection (block
  private/link-local/loopback/metadata IP ranges), domain allowlisting,
  request timeouts, response size caps, and MIME/type restrictions.
- **NFR-SEC-5 (M)** **Prompt-injection resistance:** untrusted content (web
  pages, other models' output treated as data where relevant) is clearly
  delimited; the moderator/engine does not execute instructions embedded in
  fetched content or participant output.
- **NFR-SEC-6 (M)** Input validation and output encoding everywhere; transcripts
  rendered safely (no XSS from model/web content in the UI).
- **NFR-SEC-7 (S)** Dependency and container scanning in CI (SCA); pinned
  dependencies; least-privilege runtime.
- **NFR-SEC-8 (S)** Rate limiting and resource budgets to prevent runaway
  cost/loops.
- **NFR-SEC-9 (S)** Security headers, CORS locked down, and authn/authz on the
  API before any non-localhost deployment.
- **NFR-SEC-10 (M)** A documented threat model and `SECURITY.md` (responsible
  disclosure).

### 6.2 Quality & Testing
- **NFR-Q-1 (M)** Automated unit + integration tests; LLM providers are mocked in
  tests (no live calls in CI).
- **NFR-Q-2 (M)** **Mutation testing** is part of the quality gate (see
  [`testing.md`](./testing.md)); a minimum mutation score is enforced on core
  logic.
- **NFR-Q-3 (M)** Deterministic, reproducible test suite; debate engine testable
  with stubbed providers and fixed seeds.
- **NFR-Q-4 (S)** Linting, type checking, and formatting enforced in CI.
- **NFR-Q-5 (S)** Test coverage tracked (coverage is a floor; mutation score is
  the real signal).

### 6.3 Reliability & Performance
- **NFR-R-1 (S)** Provider failures (timeout, model unavailable) are handled
  gracefully with retries/backoff and clear surfaced errors; one failing
  participant does not crash the debate.
- **NFR-R-2 (S)** Long debates stream incrementally; the UI stays responsive.
- **NFR-R-3 (C)** A debate can be resumed after a process restart (persisted
  state).

### 6.4 Usability & Accessibility
- **NFR-U-1 (S)** A user can create a chamber, add participants, and start a
  debate in a few steps without reading code.
- **NFR-U-2 (C)** UI meets basic accessibility (keyboard nav, contrast, semantic
  markup).

### 6.5 Maintainability & Extensibility
- **NFR-M-1 (M)** New providers are added via the provider interface only.
- **NFR-M-2 (M)** Documentation is kept in sync with the code as features land.
- **NFR-M-3 (S)** Clear module boundaries: providers ⟂ debate engine ⟂ API ⟂ UI.

### 6.6 Portability & Ops
- **NFR-O-1 (S)** Runs locally with minimal setup (documented quickstart).
- **NFR-O-2 (C)** Containerised (Docker/Compose) for reproducible runs.
- **NFR-O-3 (S)** Configuration via environment; sane, safe defaults.

## 7. Constraints & Assumptions
- Local models are served by a user-run **Ollama** instance; Cicero does not ship
  models.
- Hosted models (Anthropic) require the user to supply their own API key via the
  environment.
- The repository is **public**; everything committed must be shareable.
- Initial deployment target is **single-user / self-hosted / localhost**.
- Internet access for participants is **opt-in** and may be entirely disabled.

## 8. Risks
| Risk | Impact | Mitigation |
|---|---|---|
| Prompt injection via web content or model output | High | Delimiting, no-instruction-execution policy, sanitisation (NFR-SEC-5). |
| SSRF / abuse via web tool | High | Allowlist + IP-range blocking + limits (NFR-SEC-4). |
| Secret leakage in public repo | High | Env-only secrets, git-ignore, CI secret scanning (NFR-SEC-1/3). |
| Runaway cost / infinite loops | Medium | Round/token/time budgets, rate limits (FR-16, NFR-SEC-8). |
| "Fake" consensus (sycophancy) | Medium | Explicit final-stance capture, disagreement summary, human review (FR-23/24/25). |
| Provider API drift | Medium | Adapter interface + contract tests (FR-10, NFR-M-1). |

## 9. Acceptance / Definition of Done (release-level)
- All **Must** functional requirements implemented and demoed end-to-end
  (create chamber → add ≥2 participants across ≥2 providers → run debate →
  reach consensus/disagreement → export).
- Security NFRs marked **Must** are satisfied and evidenced (no secrets, SSRF
  tests pass, injection tests pass).
- Test suite green in CI **and** mutation score meets the configured threshold on
  core modules.
- Docs (requirements, architecture, backlog, testing, security) reflect the
  shipped behaviour.

> **How this is verified.** The first criterion is executable:
> `backend/scripts/demo.py` (`make demo`) walks the whole path against a live API
> and prints a PASS/FAIL row per requirement, exiting non-zero on failure. CI runs
> it on every push with the offline mock provider; `make demo-release` is the
> sign-off form, which additionally *requires* the debate to span ≥2 real
> providers. See [`architecture.md`](./architecture.md) §13.

## 10. Open Questions — **resolved 2026-07-16**
All five were closed at architecture approval; the decisions are recorded as
D-1…D-5 in [`architecture.md`](./architecture.md) §10 and are reflected in the
shipped code.

| # | Question | Resolution |
|---|---|---|
| **OQ-1** | Consensus mechanism: moderator-LLM adjudicated, rule-based (stance-stability), voting, or hybrid? | **Hybrid** (D-2): a deterministic stance-stability signal drives detection, an LLM moderator synthesises the artifact, and a per-chamber `decision_rule` (`unanimous` / `majority` / `judge`) resolves the final poll — see architecture §6. |
| **OQ-2** | Frontend depth for v1: full SPA, lightweight web UI, or API + CLI first? | **Lightweight web UI** (D-5): React + TypeScript + Vite, live SSE transcript, compose/watch/export. |
| **OQ-3** | Web evidence in v1, or a fenced later epic? | **In v1** (D-3): Epic G shipped in Milestone 3, off by default and sandboxed (architecture §7, security §4.1). |
| **OQ-4** | Persistence: is SQLite acceptable for v1? | **Yes** (D-4): SQLite via SQLAlchemy 2.0, swappable to Postgres by config. |
| **OQ-5** | Primary implementation language/stack? | **Python 3.11+ / FastAPI backend + React/TypeScript frontend** (D-1). |
