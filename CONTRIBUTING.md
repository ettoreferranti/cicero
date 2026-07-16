# Contributing to Cicero

Thanks for your interest! Cicero is in active early development. Please read this
guide and the [docs](./docs) before contributing.

## Ground rules (security & privacy — this is a public repo)

- **Never commit secrets** (API keys, tokens, credentials). They load from the
  environment only. Copy `backend/.env.example` to `backend/.env` (git-ignored).
- **Never commit personal or confidential data** — use non-personal placeholders
  in code, tests, fixtures, and docs.
- Follow the practices in [`docs/security.md`](./docs/security.md). Report
  vulnerabilities privately per [`SECURITY.md`](./SECURITY.md).

## Development setup (backend)

Requires Python 3.11+ and [`uv`](https://github.com/astral-sh/uv).

```bash
cd backend
uv venv --python 3.11 .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

## Quality gates (must pass before a PR)

Run the whole suite with `make check` (from `backend/`), or individually:

```bash
ruff check .          # lint (incl. security lints)
mypy cicero           # strict type checking
pytest --cov=cicero   # unit + integration (providers mocked; no live/network calls)
mutmut run && python scripts/check_mutation_score.py --min 80   # mutation gate
```

CI runs the same gates plus secret scanning and dependency (SCA) scanning.

## Definition of Done (every change)

1. Tests added/updated; providers mocked (no live LLM or network calls in tests).
2. Mutation score on touched **behavioural** modules meets the threshold
   (see [`docs/testing.md`](./docs/testing.md) §3.1 for what's in scope).
3. Lint, type-check pass; no secrets/PII added.
4. Untrusted content (web/model output) handled safely — see security docs.
5. Relevant docs updated (requirements/architecture/testing/security/README).
6. Link the change to a requirement (`FR-*`/`NFR-*`) and backlog item where
   applicable.

## Commit & branch conventions

- Small, focused commits with clear messages (imperative mood).
- Reference the backlog item / requirement id in the body where relevant.
