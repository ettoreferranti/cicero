# Cicero — LLM Debate Chamber

> Named after Marcus Tullius Cicero, Rome's great orator.
> A virtual debating platform for pitting local and hosted LLMs against one
> another.

Cicero lets you create **debate chambers** around a topic or question, add
**LLM participants** from different providers (a local **Ollama** model, the
**Anthropic** API, and more), assign each a **stance** (pro / con / neutral),
and watch them debate in a **turn-based group chat** — each trying to persuade
the others — until the chamber reaches a **consensus statement** (or a
principled summary of disagreement). Participants can optionally cite **web
evidence**, under strict safety controls.

**Purpose:** observe and compare how different models reason, argue, use
evidence, hold or change positions, and cooperate.

---

## Status

🚧 **Milestone 3 — Web Evidence & Hardening (mostly done).**
Milestones 0–2 are merged (foundation; first real debate; live-streaming web
UI with exports and metrics). Milestone 3 adds:

- **Tunable debates** — per-chamber settings (max/min rounds, token budget,
  **wall-clock duration**, convergence rounds, decision rule, web evidence)
  set at creation or via `PUT /chambers/{id}/settings`, and editable in the UI
  while the chamber is a draft.
- **Debates that actually end with a winner** — a two-phase debate loop
  (adversarial → **convergence phase** that pushes for concessions and
  compromise) plus a per-chamber **decision rule**: `unanimous`, `majority`,
  or `judge` (default — majority wins, and the moderator breaks ties with an
  explicit `WINNER:` verdict). The outcome records the **winning stance**.
- **Sandboxed web evidence (Epic G)** — opt-in per chamber *and* via
  `WEB_ACCESS_ENABLED`: key-less DuckDuckGo search + an SSRF-guarded fetcher
  (private/metadata IP blocking, allow/deny lists, redirect re-validation,
  timeouts, size/MIME caps). Two flavours, both executed by the engine (models
  never touch the network): an upfront **cited research turn**, and
  **per-turn searches the debaters request themselves** — a universal
  `SEARCH: <query>` text protocol, upgraded to native tool use on Anthropic —
  with queries and citations recorded on the exact turn that used them.
- **Hardening (J1/J2)** — optional bearer-token auth (`API_AUTH_TOKEN`),
  per-IP rate limiting (`RATE_LIMIT_PER_MINUTE`), on top of the existing
  security headers and CORS lockdown.
- **Moderator notes (E7)** — inject a note between turns (API + UI) — and
  **run comparison (H4)** via `GET /chambers/{a}/compare/{b}`.

Fully runnable end-to-end with the deterministic mock provider — no keys required.
See [`docs/backlog.md`](./docs/backlog.md) for milestone progress.

> Controls note: **start** and **stop** are implemented; pause/resume/step are a
> planned follow-up. The participant form's **model selector** lists the models
> actually available from the chosen provider (e.g. those loaded in your local
> Ollama, via `GET /providers/{provider}/models`), falling back to free-text when
> the provider is unreachable. Server-side model validation on add is still todo.

### Quickstart (backend)

```bash
cd backend
uv venv --python 3.11 .venv && source .venv/bin/activate
uv pip install -e ".[dev]"
make check      # lint + type-check + tests(+coverage) + mutation gate

# Run the API (localhost only by default)
uvicorn cicero.api.app:app --reload
```

Then, using the mock provider (no external services needed):

```bash
# Create a chamber
CID=$(curl -s localhost:8000/chambers -H 'content-type: application/json' \
  -d '{"topic":"Should we colonise Mars?"}' | python -c 'import sys,json;print(json.load(sys.stdin)["id"])')
# Add two participants with stances
curl -s localhost:8000/chambers/$CID/participants -H 'content-type: application/json' \
  -d '{"display_name":"Ada","provider":"mock","model":"mock-small","stance":"pro"}' >/dev/null
curl -s localhost:8000/chambers/$CID/participants -H 'content-type: application/json' \
  -d '{"display_name":"Zeno","provider":"mock","model":"mock-small","stance":"con"}' >/dev/null
# Run the debate to a consensus / disagreement result (note: POST)
curl -s -X POST localhost:8000/chambers/$CID/run | python -m json.tool
```

Set `provider` to `ollama` (with a running Ollama server) or `anthropic` (with
`ANTHROPIC_API_KEY` in your environment) to debate with real models.

### Quickstart (web UI)

With the backend running on port 8000:

```bash
cd frontend
npm install
npm run dev        # opens http://localhost:5173 (proxies the API to :8000)
```

Then in the browser: create a chamber, add at least two participants (choose a
provider + model + stance), and click **Start** — turns stream in live, followed
by the consensus/disagreement statement, metrics, and JSON/Markdown export links.

Frontend checks: `npm run typecheck`, `npm run lint`, `npm test` (Vitest).

## Documentation

| Doc | What it covers |
|---|---|
| [`docs/requirements.md`](./docs/requirements.md) | Vision, personas, functional & non-functional requirements. |
| [`docs/backlog.md`](./docs/backlog.md) | Epics, user stories, estimates, milestones. |
| [`docs/architecture.md`](./docs/architecture.md) | Proposed architecture, stack, components, data model. |
| [`docs/testing.md`](./docs/testing.md) | Testing strategy, incl. **mutation testing**. |
| [`docs/security.md`](./docs/security.md) | Threat model & security practices. |
| [`SECURITY.md`](./SECURITY.md) | Vulnerability reporting policy. |

## Key principles

- **Provider-agnostic:** new LLM providers plug in behind one interface.
- **Security-first (public repo):** no secrets or personal data committed; web
  access sandboxed; prompt-injection resistant.
- **Deeply tested:** deterministic core, mocked providers, and **mutation
  testing** as the quality gate.

## Planned tech (proposed — pending approval)

Python 3.12 · FastAPI · SQLite/SQLAlchemy · React + TypeScript · pytest +
`mutmut` (mutation testing) · GitHub Actions CI.

## License

[MIT](./LICENSE)
