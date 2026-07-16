# Cicero — Threat Model & Security Practices

> **Status:** Draft v0.1. Supports NFR-SEC-* in [`requirements.md`](./requirements.md)
> and §7 of [`architecture.md`](./architecture.md). Vulnerability reporting
> process lives in the repo-root [`SECURITY.md`](../SECURITY.md).

## 1. Assets to protect
- **Provider credentials** (e.g. `ANTHROPIC_API_KEY`).
- **The host running Cicero** and its internal network (SSRF target).
- **Integrity of the debate** (no external actor injecting instructions).
- **The public repository** (no secrets/PII committed).

## 2. Trust boundaries
- User ↔ API.
- API/Core ↔ LLM providers (model output is **untrusted data**, not commands).
- Web Evidence tool ↔ the internet (**fully untrusted**).
- Runtime config/secrets ↔ code (secrets one-directional: env → runtime only).

## 3. Threats & mitigations (STRIDE-lite)
| # | Threat | Vector | Mitigation |
|---|--------|--------|-----------|
| T1 | **Secret leakage** | Committed keys, logs, API responses | Env-only secrets; `.gitignore`; secrets never logged/serialised; CI secret scanning (NFR-SEC-1/3). |
| T2 | **Prompt injection** | Malicious text in fetched pages or model output | Delimit untrusted content; "data not instructions" rule; engine/moderator never executes embedded commands (NFR-SEC-5). |
| T3 | **SSRF** | Web tool pointed at internal/metadata IPs | Block private/loopback/link-local/metadata ranges; domain allowlist; https-only; validate redirects (NFR-SEC-4). |
| T4 | **Resource exhaustion / cost blowup** | Infinite debate loops, huge fetches | Round/token/time budgets; response size caps; rate limits (NFR-SEC-8). |
| T5 | **XSS** | Model/web content rendered in UI | Escape/text-only rendering; no `innerHTML` of untrusted content (NFR-SEC-6). |
| T6 | **Supply-chain compromise** | Malicious/vulnerable deps | Pinned deps; SCA scanning; minimal image; least privilege (NFR-SEC-7). |
| T7 | **Unauthorized access** | Exposed API beyond localhost | Localhost default; CORS lockdown; security headers; authn/authz before remote deploy (NFR-SEC-9). |
| T8 | **PII/confidential disclosure** | Personal data in samples/fixtures/docs | Non-personal placeholders only; review before commit (NFR-SEC-2). |

## 4. Secure defaults
- Web access **off** by default; enabled per-chamber, explicitly.
- API bound to **localhost** by default.
- All external I/O has timeouts and size limits.
- No secret is ever returned to a client or written to a log.

## 5. Secrets handling checklist (contributors)
- [ ] No API keys, tokens, or personal data in code, tests, fixtures, or docs.
- [ ] New config read from env via the settings object.
- [ ] `.env.example` documents variable names only (no values).
- [ ] Logs reviewed for accidental secret/PII exposure.

## 6. Ongoing
- Threat model reviewed when a new trust boundary is added (e.g. new tool,
  remote deployment, multi-user).
- CI gates (secret scan + SCA) must stay green.
