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

🚧 **Early stage — requirements & architecture.** The project is currently in
its planning phase. See the docs below. Application code begins after the
architecture is approved.

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
