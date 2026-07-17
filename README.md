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

🚧 **Milestone 2 — Usable & Observable (in progress).**
Architecture approved; Milestone 0 (foundation) merged; Milestone 1 (first real
debate) complete. **Backend:** Ollama + Anthropic adapters, a turn-based debate
engine (injection-delimited prompts, hard round/token budgets, per-turn
resilience), a hybrid consensus engine (with disagreement fallback), and a
FastAPI API that now **runs debates in the background, streams every turn live
over Server-Sent Events**, supports a **stop** control, and offers **export**
(JSON/Markdown) and per-participant **metrics**. **Frontend:** a lightweight
**React + TypeScript** web UI to create chambers, add participants with stances,
start a debate, watch turns stream in live, and view/export the outcome.
Fully runnable end-to-end with the deterministic mock provider — no keys required.
See [`docs/backlog.md`](./docs/backlog.md) for milestone progress.

> Controls note: **start** and **stop** are implemented; pause/resume/step are a
> planned follow-up. Provider connectivity validation is not yet added.

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
