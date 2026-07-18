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
- Per-client rate limiting on by default (`RATE_LIMIT_PER_MINUTE`, J2).
- Optional bearer-token auth (`API_AUTH_TOKEN`) — set it before any
  non-localhost exposure (J1).

## 4.1 Web evidence controls (Epic G, implemented)
The `cicero.tools.web` module is the only code that touches the internet.
Both switches must be on for a debate to gather evidence: the global
`WEB_ACCESS_ENABLED` env flag **and** the chamber's `settings.web_evidence`.

Evidence flows in two ways, and **models never make network calls
themselves** in either: (1) an upfront research brief gathered once by the
engine before round 1, and (2) per-turn searches the model *requests* —
via a `SEARCH: <query>` text reply (any provider) or native tool use
(Anthropic) — which the engine executes through the same sandboxed
gatherer, capped at 1 search per turn, with the queries and resulting
citations recorded on that turn. Controls enforced by `SafeWebClient` /
`validate_public_url` for every query, whoever initiates it:

- **SSRF policy**: http/https only, default ports only, no credentials in
  URLs; the hostname is resolved and *every* address must be globally
  routable (blocks loopback, RFC-1918, link-local incl. `169.254.169.254`
  cloud metadata, CGNAT `100.64/10`, multicast, reserved). Redirects are
  re-validated hop by hop, capped at 3.
- **Allow/deny lists**: `WEB_DOMAIN_ALLOWLIST` (when set, only those domains
  and their subdomains) and `WEB_DOMAIN_DENYLIST` (always blocked).
- **Caps**: `WEB_FETCH_TIMEOUT_SECONDS`, `WEB_MAX_RESPONSE_BYTES` (enforced
  while streaming), a text-only MIME allowlist, `WEB_MAX_RESULTS` sources.
- **Sanitisation**: HTML is reduced to visible plain text (scripts/styles
  dropped, control characters stripped) and enters prompts only inside the
  delimited transcript block, covered by the "data, not instructions" rule
  (T2). Sources are recorded as citations on the transcript turn (FR-28).

*Known limitation*: DNS is resolved for validation separately from the
fetch, so a fast-flux DNS rebind between the two lookups is theoretically
possible. Strict deployments should set a domain allowlist, which bounds
the damage to the listed domains.

## 5. Secrets handling checklist (contributors)
- [ ] No API keys, tokens, or personal data in code, tests, fixtures, or docs.
- [ ] New config read from env via the settings object.
- [ ] `.env.example` documents variable names only (no values).
- [ ] Logs reviewed for accidental secret/PII exposure.

## 6. Ongoing
- Threat model reviewed when a new trust boundary is added (e.g. new tool,
  remote deployment, multi-user).
- CI gates (secret scan + SCA) must stay green.
