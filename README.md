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

✅ **Milestone 3 — Web Evidence & Hardening (complete).**
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
- **Provider resilience (C5)** — transient failures (timeouts, 429s, 5xx,
  Anthropic's 529) are retried with capped exponential backoff that honours
  `Retry-After`; permanent 4xx are never retried. A provider that stays down
  costs one debater its turn, not the debate.
- **Moderator notes (E7)** — inject a note between turns (API + UI) — and
  **run comparison (H4)** via `GET /chambers/{a}/compare/{b}`.
- **Stance history (F4)** — every convergence poll is kept, so you can see where
  each debater stood after each round and who actually moved (API, both exports,
  and a table in the UI).
- **Repetition stop** — a turn that merely restates the speaker's previous one is
  flagged, and once every debater is doing it the debate ends rather than paying
  for rounds that add no argument. Per-chamber: toggle it off, or tighten the
  similarity threshold to 1.0 for word-for-word only.
- **Draft editing (D4)** — while a chamber is a draft, its topic/category/
  description and its participant roster (add, edit, remove) are all editable,
  API + UI. A clone is a fresh draft, so this is how a rerun changes exactly one
  variable before being compared against the original.
- **Muting (D6)** — take a debater out of the argument mid-debate without
  removing it: it stops taking turns and stops counting toward the decision
  rule, but stays on the roster and is still polled, so the stance record stays
  continuous. Applied at the next round boundary.
- **Restart resilience (J3)** and an **executable release demo (J4)** —
  `make demo` walks the whole acceptance path against a live API and reports
  PASS/FAIL per requirement (see [Release demo](#release-demo)).
- **Outcomes that say something (F5)** — every concluded debate opens with a
  one-sentence headline of what the chamber actually concluded, above the
  support (`contested — 2 of 3 debaters settled on neutral (1 con)`), how it
  was decided, and who changed position. `**Winning position:** neutral` was
  never a result — it is what a compromise collapses to when the only vocabulary
  is pro/con/neutral. The stance word is still shown, as evidence rather than
  as the headline.

Fully runnable end-to-end with the deterministic mock provider — no keys required.
See [`docs/backlog.md`](./docs/backlog.md) for milestone progress and the
follow-ups carried past the milestone.

> Controls note: **start**, **step**, **pause** (stop parks the debate as
> resumable), and **resume** are all implemented — including after a server
> restart: interrupted debates are recovered as `paused` and
> `POST /chambers/{id}/resume` continues from the exact turn they stopped at,
> with token/round budgets counting the prior spend. `POST /chambers/{id}/step`
> takes a single turn and parks the debate again, so you can walk a debate
> forward one argument at a time. The participant
> form's **model selector** lists the models
> actually available from the chosen provider (e.g. those loaded in your local
> Ollama, via `GET /providers/{provider}/models`), falling back to free-text when
> the provider is unreachable. Adding or editing a debater is also validated
> server-side: an unreachable provider is a `502`, and a model the provider
> cannot serve is a `422` that names the ones it can.

### Quickstart (Docker Compose)

The whole stack, UI included:

```bash
cp backend/.env.example .env    # optional: add ANTHROPIC_API_KEY, etc.
docker compose up --build       # UI on http://localhost:8080
```

Both ports publish to `127.0.0.1` only, the API image runs as a non-root user,
and no secrets are baked into any image — compose reads them from the
git-ignored `.env` beside `docker-compose.yml`. To use an Ollama running on your
host, leave `OLLAMA_HOST` **unset/commented out** in `.env` — the container
needs `http://host.docker.internal:11434`, which is only what you get by
omitting it (`docker-compose.yml` supplies that as the default). Setting it
explicitly to `http://localhost:11434` — e.g. by copying an older `.env`, or
any guide that assumes a bare-metal run — points the container at itself
instead of your host, and Ollama calls fail with a connection error.

> **macOS + Homebrew:** `brew install docker docker-compose` gets you the CLI
> only — it does **not** include a Docker daemon or wire up `docker compose`
> as a subcommand, so `docker compose up --build` fails two different ways in
> a row (daemon not found, then `unknown flag: --build` because `docker`
> doesn't recognise `compose` at all). Fix both:
> 1. **Daemon** — Homebrew ships no daemon. Install one, e.g.
>    [colima](https://github.com/abiosoft/colima):
>    `brew install colima && colima start`. (Docker Desktop works too, but
>    isn't Homebrew-CLI-only.) Colima's VM only runs until you stop it or
>    reboot; run `colima start` again after a reboot, or
>    `brew services start colima` once to have it start automatically at
>    login.
> 2. **Compose plugin wiring** — `brew info docker-compose` prints a caveat
>    that's easy to miss: add the formula's plugin directory to
>    `~/.docker/config.json`:
>    ```json
>    { "cliPluginsExtraDirs": ["/opt/homebrew/lib/docker/cli-plugins"] }
>    ```
>    (Apple Silicon path shown; Intel Homebrew uses `/usr/local/lib/...`.)
>
> Verify with `docker compose version` before retrying `docker compose up
> --build`.

CI builds this stack and smoke-tests it (health, UI, a chamber round-trip
through the proxy, non-root check) on every push, so it does not rot.

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
# Take a single turn and pause again, to watch the debate build up argument by argument
curl -s -X POST localhost:8000/chambers/$CID/step | python -m json.tool
# Run the debate to a consensus / disagreement result (note: POST)
# (use /resume instead of /run once you have stepped)
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
**Step** takes one turn at a time instead, and **Pause** parks a running debate
so **Resume** can pick it up where it stopped.

Two kinds of settings sit side by side, and the UI labels which is which:
**Debate settings** (badged *whole chamber*) governs the run itself — rounds,
budgets, decision rule — and applies to everyone, while each row under
**Participants** (badged *per debater*) carries its own **Tuning for &lt;name&gt;**
panel (temperature, max tokens, persona, instructions — collapsed unless it
differs from the defaults). **Persona** says *who* a debater is ("a cautious
economist"); **instructions** say *how* it should argue ("be extra polite",
"speak in rhyme", "always yield your position"). Instructions shape a debater's
turns only — not the stance poll or the moderator's final statement — and never
override the engine's own safety rules. Anything scoped to a single debater is drawn with that
debater's colour down its left edge, the same cue the transcript uses.

While a chamber is still a draft you can **Edit** its topic/category/description
and edit or remove any participant, including that Tuning panel.
**Clone & rerun** makes a fresh draft copy, so the usual way to
compare runs is to clone, change one variable (say a single debater's model or
temperature), and run it again.

Frontend checks: `npm run typecheck`, `npm run lint`, `npm test` (Vitest).

### Release demo

The release acceptance criteria are executable. From `backend/`:

```bash
make demo            # starts its own API on a free port, runs the whole path
make demo-release    # same, but fails unless >=2 real providers debated
```

It creates a chamber, adds debaters, steps one turn by hand, resumes and streams
the rest live, checks the
outcome, reads metrics, exports JSON + Markdown (into `demo-output/`), and
prints a PASS/FAIL row per requirement — exiting non-zero if any fails. It uses
whichever providers actually answer (a local Ollama, Anthropic when
`ANTHROPIC_API_KEY` is set) and falls back to the offline mock, so it runs
anywhere. `python scripts/demo.py --help` lists the options
(`--base-url` to hit an already-running server, `--participant`, `--compare`,
`--web-evidence`, …).

## Documentation

| Doc | What it covers |
|---|---|
| [`docs/requirements.md`](./docs/requirements.md) | Vision, personas, functional & non-functional requirements. |
| [`docs/backlog.md`](./docs/backlog.md) | Epics, user stories, estimates, milestones. |
| [`docs/architecture.md`](./docs/architecture.md) | Proposed architecture, stack, components, data model. |
| [`docs/testing.md`](./docs/testing.md) | Testing strategy, incl. **mutation testing**. |
| [`docs/model-selection.md`](./docs/model-selection.md) | Which models can actually hold an assigned opposing side, and how to measure it. |
| [`docs/security.md`](./docs/security.md) | Threat model & security practices. |
| [`SECURITY.md`](./SECURITY.md) | Vulnerability reporting policy. |

## Key principles

- **Provider-agnostic:** new LLM providers plug in behind one interface.
- **Security-first (public repo):** no secrets or personal data committed; web
  access sandboxed; prompt-injection resistant.
- **Deeply tested:** deterministic core, mocked providers, and **mutation
  testing** as the quality gate.

## Tech stack (approved 2026-07-16, see architecture §10)

Python 3.11+ · FastAPI + Uvicorn · SQLite/SQLAlchemy 2.0 · React + TypeScript +
Vite · pytest + `mutmut` (mutation testing) · StrykerJS (frontend mutation) ·
GitHub Actions CI.

## License

[MIT](./LICENSE)
