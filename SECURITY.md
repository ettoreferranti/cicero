# Security Policy

## Reporting a vulnerability

If you discover a security vulnerability in Cicero, please report it
**privately**. Do **not** open a public issue for security problems.

- Preferred: open a **GitHub private security advisory**
  (repository → *Security* → *Report a vulnerability*).

Please include steps to reproduce, affected version/commit, and impact. We aim
to acknowledge reports promptly and will coordinate a fix and disclosure
timeline with you.

## Scope & principles

Cicero is designed to be safe to run and safe to open-source:

- **No secrets in the repository.** Credentials (e.g. `ANTHROPIC_API_KEY`) are
  provided only via environment variables and are never committed, logged, or
  returned to clients.
- **Sandboxed web access.** Any web-evidence feature is off by default and, when
  enabled, is constrained by SSRF protections, domain allowlists, timeouts, and
  size limits.
- **Prompt-injection resistance.** Untrusted content (web pages, model output
  used as context) is treated as data, never as instructions.
- **Safe rendering.** Model/web-derived content is escaped in the UI to prevent
  XSS.

See [`docs/security.md`](./docs/security.md) for the full threat model.

## Please do not

- Do not include real personal data, credentials, or confidential information in
  issues, pull requests, tests, or fixtures — this is a **public** repository.
