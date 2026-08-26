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
| A6 | As an operator, the app runs locally from a documented quickstart (and optionally Docker Compose). | NFR-O-1/2/3 | S | 3 | ✅ done — `uv`+`uvicorn` quickstart plus `docker compose up --build` (API image runs non-root with no baked secrets; nginx serves the SPA and reverse-proxies the API same-origin so SSE works without CORS; both ports published to `127.0.0.1` only). Built and smoke-tested by a dedicated CI job on every push — see the note below |

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
| C4 | As a user, adding a participant validates connectivity / model availability. | FR-12 | S | 3 | ✅ done — add *and* edit check the provider server-side: unreachable → `502`, model the provider cannot serve → `422` naming what it can. Tag/case tolerant (`llama3` matches `llama3:latest`); an empty listing never rejects. UI model selector unchanged |
| C5 | As a developer, provider failures are handled with retries/backoff and clear errors without crashing the debate. | NFR-R-1 | S | 3 | ✅ done — `providers/retry.py` retries transient failures (timeouts, transport errors, 408/425/429/5xx/529) with capped exponential backoff, honouring `Retry-After`; permanent 4xx are never retried. Tunable via `PROVIDER_MAX_ATTEMPTS` / `PROVIDER_RETRY_BASE_DELAY_SECONDS`; the engine's per-turn error containment remains the backstop |
| C6 | As a developer, a `Mock`/`Stub` provider enables deterministic tests with no live calls. | NFR-Q-1/3 | M | 2 | ✅ done |

## Epic D — Chamber & Participant Management (API)
*Goal: manage chambers and participants over the API.*

| ID | Story | FR/NFR | Pri | Est | Status |
|----|-------|--------|-----|-----|--------|
| D1 | As a user, I can create/list/view/delete chambers with topic, category, and description. | FR-1/2 | M | 3 | ✅ done |
| D2 | As a user, I can add participants with provider, model, name, and stance (default `neutral`). | FR-6/7 | M | 3 | ✅ done |
| D3 | As a user, I can set per-participant tuning (temperature, max tokens, persona/style). | FR-11 | S | 3 | ✅ done — tuning accepted on add *and* edit (`PATCH`), with a collapsible **Tuning** panel in the participant form and a `⚙` summary on the roster for anyone off the defaults; per-chamber debate settings tunable via API+UI |
| D4 | As a user, I can edit a chamber while it is a `draft` — its topic/category/description, and the participant roster (add, edit, remove) — including on a **clone before its rerun**, so a rerun can change one variable. | FR-3 | S | 3 | ✅ done — `PATCH /chambers/{id}`, `PATCH`/`DELETE /chambers/{id}/participants/{pid}`, plus edit/remove controls in the UI |
| D5 | As a developer, all API inputs are validated and errors are structured/safe. | NFR-SEC-6 | M | 2 | ✅ done |
| D6 | As a user, I can remove or mute a participant **mid-debate**. | FR-13 | C | 5 | ✅ done (mute) — `POST /chambers/{id}/participants/{pid}/mute`, queued to the **next round boundary** while running, immediate otherwise. A muted debater stops taking turns and stops counting toward the decision rule, but stays on the roster and is still polled, so the stance history has no hole. Mid-debate *removal* was deliberately not built — see the note below |
| D7 | As a user, I can give each debater a free-text **instructions** prompt (default empty) that steers how it argues during the debate — e.g. "be extra polite", "always yield your position", "speak in rhyme", "use jokes". | FR-11 | S | 3 | ✅ done — `ParticipantTuning.instructions` (the renamed `style`, now actually injected) reaches the turn prompt before the engine's rules, and only the turn prompt. Pre-D7 chambers still load via a deprecated `style` read alias |

> **I5 scope.** The story asks for *basic* accessibility and that is what
> shipped: keyboard operability, screen-reader labelling, focus visibility and
> management, table semantics, and a live region for the streaming transcript
> (the one place a screen-reader user was previously told nothing at all). What
> has **not** been done is a real audit — no automated axe/Lighthouse pass, no
> testing with an actual screen reader, and no contrast measurement of the eight
> participant accent colours (they are decorative and always paired with a text
> label, but that is an argument, not a measurement). Reopen as a new story if a
> WCAG conformance claim is ever needed.

> **A6 is verified in CI, not locally.** The machine this was written on has the
> `docker` CLI but no daemon (no Desktop/colima/OrbStack), so instead of a
> one-off local run the stack is built and smoke-tested by a **`docker` job in
> CI** on every push: `docker compose up --build --wait`, then a health check, a
> UI fetch, a create-and-read-back chamber round-trip through the nginx proxy,
> the `/config` capability probe, and an assertion that the API container is not
> running as root. That is a stronger guarantee than a manual check, since it
> keeps holding: a broken Dockerfile, a wrong build context, or a missing proxy
> route now fails the build rather than the first operator to try it.

> **Why D4 and D6 are separate.** The original D4 bundled FR-3 (edit while
> `draft`) with FR-13 (remove/mute mid-debate); they are very different jobs.
> D4 touches only draft-state CRUD — the chamber has no turns, so the existing
> draft guard is the whole safety story. D6 reaches into the engine:
> `participants_spoken()` and `round_complete()` in `core/orchestrator.py` both
> assume a fixed roster, and `resume_round()` — and therefore the step control —
> derives its resume point from "has everyone spoken in this round?".

> **D6 as decided and built (2026-08-03).** Four questions had to be answered
> before any code; the answers are what the implementation encodes:
>
> | Question | Decision | Why |
> |---|---|---|
> | Remove *and* mute? | **Mute only.** | Removal mid-debate leaves a transcript whose speakers are no longer on the roster; mute is reversible and says what an operator actually wants — "stop arguing", not "stop existing". |
> | Does a muted debater count toward round completion? | **No**, and the change lands at the **next round boundary**, never mid-round. | Boundary application keeps a debater's muted state constant for a whole round, which is the invariant `round_complete()` — and so resume and step — depend on. Applying mid-round would make a completed round read as incomplete, or the reverse. |
> | Still polled for its stance? | **Yes.** | Dropping it from the poll would put a hole in the stance history (FR-25) exactly where the interesting thing happened. |
> | Still counted in the decision rule? | **No.** | This is the point of the control: `core/roster.py::active_stances` restricts the tally, so muting can flip a split into a consensus or a majority into a judge's verdict. Deliberate, and documented as such. |
>
> Two guards fall out of this: muting is refused (`409`) if it would leave fewer
> than two active debaters, and a fully muted chamber falls back to counting
> everyone rather than resolving an empty tally as "no agreement".
>
> Mid-debate **removal** (the other half of FR-13) remains unbuilt. It needs its
> own decision about what a transcript means when a speaker is no longer on the
> roster, and mute covers the operator need that motivated the story.

> **D7 as specified (2026-08-04).** A requirements session before any code; the
> answers below *are* the spec.
>
> The session opened on a finding: **`tuning.style` already exists and is dead.**
> It is declared on `ParticipantTuning` (`domain/models.py`), accepted by
> `TuningIn` (`api/schemas.py`), has an input in `ParticipantForm.tsx`, is
> reported by `tuningSummary()` and shown on the roster — but
> `prompt_builder.build_turn_messages` injects only `persona`, so nothing an
> operator has ever typed into that box has reached a model. The feature request
> is, almost exactly, the unfinished half of FR-11.
>
> | Question | Decision | Why |
> |---|---|---|
> | New field, or the existing `persona`/`style`? | **Rename `style` → `instructions` and actually inject it.** | Adding a third free-text box beside a dead second one would ship the same promise twice. `persona` stays *who the debater is*; `instructions` becomes *how it should behave*. |
> | Which prompts does it enter? | **Debate turns only** — `build_turn_messages`, including its `converge` and `research` variants. Stance poll and moderator untouched. | The stance poll asks for exactly one word and its parse has already broken twice (both bug notes below). "Speak in rhyme" reaching that prompt would reintroduce the failure it took two fixes to close. The moderator is deliberately impartial and belongs to no debater. |
> | When can it change? | **Draft only** — on add and via `PATCH`, exactly like the rest of `ParticipantTuning`; `409` while running or paused, as today. | Mid-debate steering is a D6-shaped story of its own (when does a change take effect, and which instructions produced which turn?). Not what this asks for. |
> | Instructions vs. the engine's own rules? | **Engine rules win.** `Instructions:` sits after `Persona:` but *before* `TURN_GUIDANCE`, `FIRST_PERSON_RULE` and `SAFETY_RULE`. | The operator is trusted, but the last word stays with the rules the engine's own parsing depends on. "Be polite", "use jokes", "always yield your position" all work — they are about tone and argument. "Ignore the transcript delimiters" does not, so NFR-SEC-5 holds. |
>
> **Acceptance criteria.**
> 1. `ParticipantTuning.instructions`: free text, default `""`, keeping `style`'s
>    500-character cap (a directive, not prose — `persona` remains the 2000-char
>    field). Blank or whitespace-only is omitted from the prompt entirely, with no
>    empty `Instructions:` label.
> 2. **Stored chambers must still load.** Chambers persist as a JSON document
>    (`persistence/sqlalchemy_repo.py`) and every domain model is `extra="forbid"`
>    — so a chamber saved before D7 carries a `"style"` key that will *fail
>    validation* after the rename. Accept `style` as a deprecated read alias (e.g.
>    `AliasChoices("instructions", "style")`) or upgrade the document on load;
>    serialisation emits `instructions` only. A regression test loads a pre-D7
>    chamber fixture.
> 3. Injection lives in `prompt_builder.py`, which is inside the mutation gate:
>    a test asserts the text appears in the turn system prompt, *before*
>    `SAFETY_RULE`, and does **not** appear in the stance-poll or moderator
>    prompts.
> 4. API: accepted on `POST /chambers/{id}/participants` and
>    `PATCH /chambers/{id}/participants/{pid}`, subject to the existing draft guard.
> 5. UI: the existing *Style* input becomes *Instructions* with a placeholder
>    drawn from the real examples; `tuningSummary()` and the roster's `⚙` summary
>    and `ChamberDetail`'s persona/style join follow the rename.
> 6. Docs: FR-11 reworded (done, 2026-08-04); `README.md` §UI tuning line and
>    `architecture.md`'s prompt-assembly description updated when the code lands.
>
> **Confirmed against live models (2026-08-04).** The dead-field finding above
> came from reading the code; a real Ollama debate then reproduced it. Three
> debaters were given a `style` and nothing else changed:
>
> | Debater | `style` set | What it did |
> |---|---|---|
> | Alice | "Only write in rhyme" | Plain prose in all three turns. Not one rhyme. |
> | Eve | "be aggressive" | Conciliatory throughout — opened two of three turns with "While … is a sensible approach" and conceded immediately. |
> | Bob | "terse, cite numbers and sources" | Terse, cited ASHRAE 55 and EPA figures. **Appeared to comply.** |
>
> Bob is the trap, and the reason this went unnoticed for so long: Bob is the
> only one of the three that also had a **persona** (`"rigorous"`), and persona
> *is* injected. Bob's behaviour came from the field that works plus qwen3's own
> citation habit. Verifying the D7 fix on a debater that has a persona set proves
> nothing — use one with `instructions` **only**.

> **Known interaction, deliberate.** "Always yield your position" will genuinely
> move a debater's stance poll, and therefore the decision rule and the recorded
> `winning_stance`. That is the point of the control, but it makes the outcome an
> artifact of the setup rather than of the arguments — worth a line in the UI help
> text, in the same spirit as the D6 note that muting can flip a split into a
> consensus.
>
> Exports and `compare` carry no tuning today, so neither is affected.

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
| F4 | As an evaluator, stance changes over time are recorded and viewable. | FR-25 | S | 3 | ✅ done — every convergence poll is persisted as a `StancePoll` on `chamber.stance_history` (one per round, never double-recorded across steps), surfaced in the API, both exports, and a **Stance history** table in the UI that flags who moved |
| F5 | As a reader, every outcome opens with a one-sentence headline saying what the chamber concluded, plus how firmly it was held, how it was decided, and who moved. | FR-23 | S | 3 | ✅ done — the moderator emits a `HEADLINE:` directive on the existing call (no extra request); `core/outcome.py` derives support/basis/movement from data already recorded, so chambers concluded before F5 gain them retroactively. `ConsensusResult` also stores the `unparsed` set it decided on, because the final poll is not always in `stance_history`. Served by `GET /chambers/{id}/outcome`; falls back to the pre-F5 display when no usable headline was produced |
| F6 | As a reader, the derived outcome fields no longer contradict the moderator's statement: stance changes are labelled as the poll record they are, a caveat appears when `neutral` could be read as a characterisation, and the caveat points at the transcript rather than vouching for the statement. | FR-23/25 | S | 2 | ✅ done — `neutral` is the one overloaded stance label (it means both "holds no position" and "holds a position the poll cannot name"), so it is the trigger; a debate that never touches it gets no caveat. A fourth change — asking the moderator statement to narrate who moved — was built and **reverted**: a controlled re-run showed it made the moderator claim a debater had endorsed a position his final turn explicitly rejected. See `docs/superpowers/decisions/2026-08-06-chamber-scoped-positions-not-built.md` for the five experiments that rejected the larger position-registry design and identified this as the real defect |
| F7 | As a user, a debate is not ended early by an unchanged stance poll while debaters are still making new arguments. | FR-16/22 | S | 2 | ✅ done — `STANCES_STABLE` now also requires `round_all_repeated`, so an early stop needs the deterministic textual signal as well as the poll. A real debate had ended at round 3 of 8 on three identical polls in a round where one debater announced a reversal. Requiring *more* matching polls would not have helped — that debate already had three. `round_all_repeated` is now measured unconditionally; `stop_on_repetition` still gates only the `REPETITION` stop reason |
| F8 | As a user, I choose which model writes the outcome, instead of it being whichever debater I happened to add first. | FR-1/12/22/23 | S | 3 | ✅ done — a `Moderator` value object on the chamber (provider, model, max_tokens, temperature), validated like a participant and editable while a draft; `None` still means the first participant. The token budget is the point: at the old 2048 the moderator produced no readable `WINNER:` on **5 of 9** measured judge calls (qwen3:30b via the production provider), so a tie failed to name a winner about half the time; 4096 cleared 9 of 9 and 8192 gained nothing. A bounded retry covers the stochastic residue. The bias hypothesis that prompted this was measured and **not** supported — across three debates and three moderator models, 8 of 9 parseable verdicts ruled the same way regardless of the moderator's own assigned stance |
| F9 | As a reader, I can tell whether the chamber's winning position was ever actually argued against, rather than assuming a consensus means debaters converged. | FR-34 | S | 5 | ✅ done — each turn is judged for the side its *prose* argues, by the moderator, which is never told who wrote the turn or what they were assigned; the comparison against the assignment is pure Python in `core/compliance.py`. Motivated by five of twelve con-assigned opening turns arguing the pro case, and by a five-debater run reporting "unanimous consensus" when nobody had argued the other side. Three prompt-level fixes were already refuted by measurement — holding an assigned side is a per-model capability (`llama3.1` 8/8, `command-r` 1/8), so this measures rather than rewords. Decides nothing: no decision rule, stop condition or outcome value reads it. Judge agreement with hand-reading measured at 20 of 21 (95%) on `llama3.1:latest` |
| F10 | As a reader, I can check the compliance judge's verdict against the sentence it rests on, instead of trusting a bare label. | FR-34 | S | 5 | ❌ **not shipped — built, measured, reverted.** The judge was asked to quote the author's own position sentence before labelling it, with Python verifying the quote appears in the turn and discarding the verdict when it does not. Measured on 32 hand-read turns (`f10_*` fixtures), same judge (`qwen3:30b`) for both arms: the shipped one-word protocol agrees with hand-reading **25 of 32** with 0 unmeasured; quote-first agreed **0 of 32** and left **32 of 32 (100%)** unmeasured — it produced no usable judgement on any turn, against a gate of ≤25% unmeasured. Kept from the work: the hand-read benchmark fixtures and `POST /chambers/{id}/compliance/rejudge`, which re-runs the current judge over a concluded chamber. The 7 baseline errors are all pro↔con inversions on rebuttals, so the defect is real and unfixed. See `docs/superpowers/decisions/2026-08-14-quote-first-judging-not-shipped.md` |
| F11 | As a user, a debate does not conclude on a unanimous stance poll while the turns of that same round are still arguing opposite sides. | FR-16/22/34 | S | 3 | 📝 **specced, not built** — the `CONSENSUS` stop gains a second, independent condition: the compliance judge's read of the same round must agree before the poll may end the debate. No new model call (F9 already produces `argued` on every judged turn), no new stop reason, no new setting. Replayed offline over the 16 concluded chambers that carry both signals: the poll alone fires in 6 and the conjunction in 3 — it blocks the 3 stops whose judged turns contradict them (Colombia rerun 2–2, Zürich 3–1, US presidency 2–2) and keeps the 3 an independent reader confirms, changing no `max_rounds` debate. **The decisive evidence is a post-fix rerun:** the Colombia chamber cloned with `min_rounds: 3` and a readable poll still stopped on a false unanimity — at round 4 instead of round 1 — while *both* pro debaters were judged `pro` in all four rounds and were still arguing the motion in the round that ended it. The round floor delays the false stop; it does not prevent it. The obvious stronger move — *replacing* the self-report with the judge — was measured and is worse than what ships: it would cut **8 of 16** debates short, one by 7 rounds, because `argued` describes one speech and one round of agreement is not convergence (F7's finding, arriving again by a different route). Reproduce with `backend/scripts/replay_consensus_gate.py`. See [`2026-08-25-consensus-needs-two-signals-design.md`](superpowers/specs/2026-08-25-consensus-needs-two-signals-design.md) |
| F12 | As a reader, the recorded stance changes describe debaters who actually moved, rather than artifacts of a poll that misread one round. | FR-23/25/34 | S | 2 | ⚠️ **known defect, not built.** `outcome.movements()` compares a debater's first and last *measured* poll and reports the difference, reading only the instrument already known to be unreliable. In the watering-lawns chamber (2026-08-26) it published **"Bob (pro to con)"** for a debater the compliance judge scored `con` in all 7 of his turns: he never moved, and the outcome tells the reader he was persuaded when he did the persuading. The whole line comes from one inverted poll at round 3, with `unparsed` empty — the model's answer, not a parse failure. F6's caveat did fire, but for an unrelated reason (another debater's trajectory starts on `neutral`) and its text only qualifies `neutral`; a polar pro↔con artifact carries no warning at all. This is F6's finding recurring in the case F6 did not cover. `Turn.metadata["argued"]` already provides an independent per-round trajectory, so a reported movement the judged record contradicts can be suppressed or flagged — the same principle as F11, applied to the outcome fields rather than the stop condition |

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
| I5 | As a user, the UI meets basic accessibility. | NFR-U-2 | C | 3 | ✅ done (basic) — labelled controls, a `<main>` landmark, a visible focus ring on the dark palette, `scope`d table headers with row headers, spelled-out "Yes/No" instead of a bare tick, focus moved into (and back out of) the participant edit form, and an `aria-live` region announcing debate progress without reading whole turns aloud. **Not** a full WCAG audit — see the note below |

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

### Bug — stance polling inverted concessions (2026-08-03)
Found from a real Ollama debate whose `winning_stance` was `pro` while its own
resolution text rejected the motion, and whose stance history showed **zero**
movement across three rounds in which three debaters explicitly conceded.

Root cause: `parse_stance` matched **substrings**, so `prohibit` → *pro* and
`context`/`concede` → *con*. A debater conceding "we should prohibit it" was
recorded as supporting the motion. Words models actually use when conceding
(`against`, `oppose`, `no`) matched nothing, and the parse failure was then
silently replaced with the participant's **declared starting stance** — which is
why the history looked stable rather than broken.

Fixed: whole-word matching, generous on a one-word reply and narrow inside
prose, with `<think>` blocks stripped; unreadable replies now reported via
`StanceReport.unparsed` and `StancePoll.unparsed` and surfaced in the exports
and UI; `MockProvider` answers stance polls instead of echoing the prompt (the
echo was being "parsed" out of the quoted transcript, so offline debates
resolved on parser noise). Knock-on: the `STANCES_STABLE` early stop could fire
on a debate that was actively converging, since stances never appeared to move.

The tests missed it because `ScriptedProvider` only ever returned exactly
`"pro"`/`"con"`/`"neutral"` — the one input shape that worked. Regression tests
now use the reply shapes real models produce.

### Bug — reasoning models never answered the stance poll (2026-08-03)
Follow-on from the above, found once the `(?)` markers made it visible: a second
debate showed **every** poll for the qwen3 debater flagged unreadable, so its
recorded stance was pure fallback for all three rounds.

Measured against the live model: Ollama keeps reasoning in a separate
`message.thinking` field, and qwen3 asked for one word spent the entire 512-token
budget there — `done_reason: "length"`, `content: ""`. Nothing to parse.

Fixed: `GenerateOptions.allow_reasoning`, set `False` for the poll and mapped to
Ollama's `"think": false` — the same model then answers in **2 tokens**.
Verified end-to-end through the engine: 0 unparsed where it was previously 3 of
3, with qwen3 correctly reporting a stance *against* its assigned role. The
adapter now also errors instead of returning `""` when a response is truncated
before producing content.

Related fix in the same pass: a stance that was **never** successfully measured
no longer carries a vote (`deciding_stances`). Its value bottoms out in the
participant's assigned starting role, so counting it let the setup decide the
outcome — the same "fabricated data reaching the tally" failure as the parsing
bug above, one layer down.

### Bug — muting a debater did nothing visible, and could not be undone (2026-08-04)
Reported from real use: "if I click on mute, there's no indication that a
participant is muted, nor can I unmute them". Three separate defects, which
together produce exactly that experience.

| # | Where | Defect |
|---|---|---|
| 1 | `core/orchestrator.py` | A queued mute was **silently discarded**. `_apply_mutes` ran only at the *top* of each round, so a mute requested during the last round had no boundary left to land on and died with the debate task — after the API had already answered `"queued"`. |
| 2 | `ChamberDetail.tsx` | A queued mute left **no mark on the row**. The only feedback was a page-level notice, far from the button clicked, and the roster is not refetched mid-run. |
| 3 | `ChamberDetail.tsx` | The control was hidden once the chamber concluded (`status !== "concluded"`), so a debater muted mid-debate **could never be unmuted**. |

Mute on a *draft* chamber always worked — that path refetches and shows the
badge — which is why this survived D6's tests: they drove the engine directly
with a mute already queued before round 0, the one timing that works.

Fixed: pending mutes are now drained at conclusion **and** on park, applied
*after* the outcome is computed so the roster records the request without a
change that never survived a full round retroactively altering the tally (the
D6 boundary invariant is preserved); a per-row `⏳ muting at next round` marker,
cleared when a reload shows the roster caught up; and Mute/Unmute stays
available on a concluded chamber, where it changes the roster and never the
stored consensus.

### Bug — the stance parser read the model's reasoning, not its answer (2026-08-18)

Found while setting up the F10 follow-up experiment, from the raw judge replies
the old harness never recorded.

`parse_stance` strips reasoning with `_THINK_BLOCK = <think>.*?</think>`, which
requires the **opening** tag. Ollama asked for `think: false` does not send one:
qwen3:30b writes its narration straight into `content` and closes it with a bare
`</think>`. Across the 32 F10 benchmark replies, **0 carried an opening tag and
32 carried the closing one**, so the strip never fired once and the whole
narration was parsed.

The narration quotes the instruction back to itself — *"the user wants me to
report only 'pro', 'con', or 'neutral'"* — and the first stance word in that
quotation won. The judge's actual answer, sitting after the `</think>`, was never
reached. A representative reply recorded as `pro`:

> "This argument is entirely against the motion, so the answer must be `con`. […]"
> `</think>`
> `con`

Measured on the hand-read F10 benchmark, changing only where the stance is read
from and nothing else:

| parsed from | agreement with hand labels | unmeasured |
|---|---|---|
| the whole reply (shipped) | 25/32 | 0 |
| after the final `</think>` | **31/32** | 0 |

**Six of the seven "judge errors" recorded in F10 were this bug, not the judge.**
The judge had reasoned to the correct answer and said so. Re-run live against
`qwen3:30b`: 6 fixed, 0 broken.

Not confined to the compliance judge — `parse_stance` is shared with the
**stance poll**, where a wrong value feeds the decision rule and the recorded
winning stance. Probed directly against the live model: a debater answering `con`
parsed as `pro` before the fix. That is the same fabricated-data-reaching-the-tally
failure as the two stance bugs above, a third time, one layer up.

Fixed: `_strip_reasoning` drops matched blocks first, then anything up to and
including an unmatched closing tag. A reply that is *all* narration now comes back
empty and reads as unmeasured, which is the honest answer for a model that
thought out loud and never committed. Both strips are load-bearing and both are
now pinned by tests — the matched pair protects an answer written *before* a
reasoning block, which the greedy strip alone would swallow.

The tests missed it because every fixture used a well-formed `<think>…</think>`
pair. No test had ever been written against a reply a real model actually
produced. `tests/fixtures/f10_judge_replies.json` now holds all 32 verbatim
qwen3:30b replies, and `scripts/score_compliance_judge.py --reparse` re-reads them
with the current parser and scores against the hand labels **offline, with no
model call** — which is how this defect should have been caught and how the next
one will be.

#### Knock-on: what F10's numbers actually measured

The F10 decision record reports 7 baseline errors, "all pro↔con inversions on
rebuttals", and names the rebuttal-warning clause as a cheap separable experiment
against them. Six of those 7 were the parser. The real target is **one** turn
(`614d9e5b`), which is a genuine judge inversion on a rebuttal and remains
unfixed — too small to measure a prompt change against on this fixture. The
clause is therefore still untested, and now needs a larger benchmark rather than a
cheap re-run. Recorded in that decision record too.

### Bug — a debater's token budget was shared with invisible reasoning (2026-08-19)

Found while running a rhetoric experiment where the turn text *is* the
measurement, so a silently truncated speech corrupts data rather than merely
reading badly. Filed as #28.

Debate turns were generated with `GenerateOptions.allow_reasoning` at its default
of `True` — the orchestrator built the options from `tuning.temperature` and
`tuning.max_tokens` and never touched the flag. Ollama counts reasoning tokens
against `num_predict` but returns them in a **separate `thinking` field**, so on a
thinking model `tuning.max_tokens` was not a budget for the turn: it was a budget
shared between invisible narration and the turn, in that order.

Measured on `muse-glimmer:30b-mlx` at `num_predict=1400` with a debate transcript
in context, three samples per arm:

| reasoning | done_reason | tokens | thinking chars | content chars | ends cleanly |
|---|---|---|---|---|---|
| on (the default) | length | 1400 | 6319 | **0** | no |
| on | length | 1400 | 3733 | 2635 | no |
| on | length | 1400 | 5297 | 898 | no |
| off | stop | 863 | 0 | 3041 | yes |
| off | stop | 875 | 0 | 3294 | yes |
| off | stop | 723 | 0 | 2525 | yes |

With reasoning on, every sample hit the cap and stopped mid-sentence; one
returned **no content at all**. With it off, every sample finished cleanly on
~60% of the same budget. In a real three-round debate this cut **4 of 6** turns
from that model, one of them to 173 characters — served by the API as an ordinary
turn, because nothing records that a turn was truncated.

This is the same mechanism as the *"reasoning models never answered the stance
poll"* entry above, which added `GenerateOptions.allow_reasoning` and set it
`False` for the poll. Turns never got the same treatment and there was no way to
ask for it: `ParticipantTuning` exposed `temperature`, `max_tokens`, `persona`
and `instructions` only. Affects any thinking model; in a five-model local roster
it hit three.

Fixed by exposing the control that already existed rather than inventing a new
one: `ParticipantTuning.allow_reasoning` (and `TuningIn`), passed through when the
turn's options are built. **Default `True`**, so every existing chamber behaves
exactly as before — reasoning is useful in a debate, and the defect was that it
was unavoidable and unbudgeted, not that it existed.

Note this changes the serialized shape of the tuning block, which is part of the
API response. The exact-shape assertion in `test_add_participant_accepts_tuning`
caught it, which is what an exact assertion there is for.

**Not fixed:** truncation is still invisible. Nothing records `done_reason` on a
turn, so a cut speech cannot be identified after the fact — by the compliance
judge, by repetition detection, by an export, or by a reader. That is the deeper
fix and is left open in #28.

### Bug — a reasoning model's narration was stored as the turn itself (2026-08-20)

Filed as #30, found in the same rhetoric experiment as the entry above and
directly caused by its fix.

`allow_reasoning=False` maps to Ollama's `think: false`, which does not silence
every reasoning model. `qwen3:30b` responds by writing its narration into
**`content`** instead of the separate `thinking` field, terminated by a bare
`</think>` that was never opened. One real turn:

> Okay, let me unpack this. The user wants me to continue as "Castellan" in a
> structured EU immigration debate… *checks rules again* Must avoid bullet
> points, write like a rally speech, ~4000 characters…
> `</think>`
> [the actual speech]

2,814 characters of deliberation, then 2,294 of speech. Across one 30-turn run,
**6 of 6** turns from that model, 16,471 characters of task narration stored as
argument. The other four models in the roster were clean.

Note the direction: with reasoning *on*, 1 turn in 6; with it *off*, 6 of 6.
Suppressing reasoning moved the narration out of a field nothing reads and into
the one everything reads. The `_JUDGE_ATTEMPTS` note already recorded the
mechanism — "because it re-asks with reasoning suppressed, makes a reasoning
model narrate instead of answering" — for the retry path only.

`parse_stance` had been taught to strip exactly this. Turn content never was, and
it is read by more: the compliance judge (so `argued` was decided from prose
discussing *both* sides and the instructions), repetition detection (narration
boilerplate is more alike than the speeches inside it, which pushes
`round_all_repeated` toward an early stop), the moderator's transcript, exports,
and the reader.

Fixed by stripping when the turn is recorded, beside `strip_echoed_speaker_label`
which already handled the cosmetic version of the same problem. Two decisions
worth stating:

- **Stripped, not destroyed.** The narration is kept on the turn under
  `metadata["reasoning"]`, present only when there was some. `turns` is
  append-only and the narration is evidence about how the turn was produced; a
  strip nobody can audit is its own kind of unreadable record.
- **One implementation.** `_strip_reasoning` moved out of `consensus.py` into
  `prompt_builder.strip_reasoning`, which both callers now use. This defect
  existed *because* the stance parser stripped and the turn path did not; two
  copies of the rule would let them drift apart again.

A turn that is **all** narration now has empty content — the engine's existing
"this debater did not speak" state, which already skips judging and cannot match
a repeat — with the narration still on the turn.

**Not fixed:** a turn still carries no provenance about how it was produced.
Nothing records `done_reason`, so a *truncated* turn remains as invisible as this
one was. Both are the same gap; the truncation half is still open on #28.

### Bug — a debate could conclude on its opening statements (2026-08-25)

Two chambers on the same motion — *"Colombia should make coca production
completely legal, regulated, and taxed…"* — each ran **one round, four turns**,
and recorded `stop_reason: consensus` with a `con` winner. Nobody had answered
anybody. Reproduced exactly by replaying the stored transcripts against the same
Ollama models at temperature 0, so none of the below is inference.

Three things stacked, in the order they fire:

- **`min_rounds` defaulted to `1`.** `may_stop_early()` is
  `rounds_completed >= min_rounds`, so the consensus check ran the moment round 0
  closed — with `max_rounds: 8` and `convergence_rounds: 2`, five rounds before
  the convergence phase it was configured to reach. This was already known and
  already written down: `model-selection.md` ends its worked example with *"raise
  `min_rounds` above 1 — a unanimous first poll otherwise ends the run before any
  exchange happens."* It was advice to the operator; it is now the default (`3`,
  and it gives way rather than raising when `max_rounds` is smaller, so asking for
  a two-round debate stays legal).

- **The 512-token poll budget left `deepseek-r1:8b` unreadable.** It spent the
  whole budget reasoning and Ollama returned no content at all → `ProviderError` →
  `unparsed`. Because it was that debater's **first** poll, `deciding_stances`
  classified it a phantom and dropped it from the tally entirely. Measured at
  increasing caps against the real transcript: 512 fails, **1024 answers cleanly**
  (`_POLL_MAX_TOKENS` raised accordingly). This is the third defect in this file
  caused by a reasoning model and a tight budget.

- **Both pro debaters reported `con` about themselves.** Not a parse failure —
  `strip_reasoning` correctly reduced 1,975 characters of `qwen3:30b` narration to
  the bare word `con`. The poll drops the persona and instructions by design and
  explicitly invites side-switching, and the pro side had been instructed to
  invent facts and attack people, so polled without their personas the models read
  their own transcript and scored the other side higher. In the second run the pro
  debater did not even need the poll: it defected inside its opening turn — *"I
  withdraw my support immediately."*

So the engine did what it says. The tally that ended debate 1 had three voters,
all `con`, and `is_consensus` was correct about them.

**What the record shows the poll missed.** The compliance judge had already read
the same four turns and scored the round **2–2** — it reads Donald's turn as `pro`,
which is what the prose argues, and it read `deepseek-r1:8b` in both runs where the
poll could read it in neither. Debate 1 had no moderator configured, so the judge
fell back to the first participant's model: `qwen3:30b`, the *same model* as the
debater it disagreed with. The variable is the question asked, not the model.

The first two causes are fixed. The third is not a defect to fix but a signal to
stop trusting alone — specified as **F11** in
[`2026-08-25-consensus-needs-two-signals-design.md`](superpowers/specs/2026-08-25-consensus-needs-two-signals-design.md).

### The Markdown export became a complete record (2026-08-20)

Prompted by a real need: sharing two runs with an outside researcher, where the
question is not "what did they say" but "under what conditions did they say it".
The export named the models and printed the prose, and dropped everything else —
so two runs differing only in `max_rounds`, in temperature, or in the moderator
exported **identically**, and nothing in the file said which model produced which
turn beyond a display name someone chose.

Added, all of it data the app already held:

- **Run configuration** — rounds, convergence, decision rule, token budget, web
  evidence, repetition stop, the moderator with its own settings, and from
  `chamber.config` how the debate ended: stop reason, rounds completed, tokens
  used.
- **Per-debater tuning** — provider/model, assigned stance, temperature, max
  tokens, and whether reasoning was allowed. The roster moved from a bullet list
  to a table to fit.
- **The prompts** — instructions and personas. Printed **once** when identical,
  and *said* to be identical: in a comparison where the model is the variable,
  that sameness is the control, and a reader cannot verify it from a per-debater
  list that happens to repeat.
- **Per-turn provenance** — the model that produced it, the side it was
  assigned, the side the compliance judge said it argued, token cost, and the
  repeat / provider-error flags.
- **Model reasoning**, under `⟨model reasoning — not part of the debate⟩`, with a
  note above the transcript saying the engine stripped it before the turn was
  stored, so no debater, the moderator or the judge ever read it. The note is
  omitted when there is no reasoning — a caveat about something absent is noise.
- **Token use per debater**, from the existing metrics.

Full record by default rather than behind `?detail=`. The only consumer is a
download link in the UI — nothing machine-parses this output, unlike the JSON
export (#16) — so there is no contract to break, and an export that omits the
settings and prompts that produced the run is simply incomplete.

`REASONING_KEY` moved from `orchestrator` to `prompt_builder`, beside the
`strip_reasoning` that produces its content, so a leaf renderer does not have to
import the engine to name a metadata key.

Five whole-document layout tests changed, which is what they are for. Two
containment tests changed too, both legitimately: the roster's names are table
cells rather than bold runs now, and one asserted no debater name appeared after
`## Transcript` - the token-use table names them all, so it was rescoped to the
transcript section, which is what it meant.

#### Everything the export writes is now ASCII

Reported from a real pipeline: an md-to-PDF converter mangled the em dashes, and
an editor flagged the `U+00B7` middle dot used as a separator on the provenance
line. Measured on one export: 287 non-ASCII characters, **219 of them inside the
debaters' own prose** - curly quotes, en dashes, `Henin`, `Bjorn`. Those cannot
be touched; the turns are the record.

What the app *writes* now is ASCII throughout: the provenance separator (45
occurrences per file), the reasoning heading's angle brackets, the round headings
(`### Round 1 - Ada`), the instructions labels, the `(?)` note, and the outcome
strings in `outcome.py` and `compliance.py` - `unanimous:`, `contested:`,
`judge-decided:`, `unmeasured:`, `pro to neutral`.

`test_markdown_scaffolding_is_ascii` pins it: a chamber whose every input is
ASCII must render as ASCII. That is the honest form of the promise - the export
adds nothing, and a file will still carry whatever the models wrote. A downstream
converter needs to read UTF-8 regardless.

## Cross-cutting "Definition of Done" (every story)
1. Code + tests (unit/integration) with providers mocked.
2. Mutation score on touched core logic meets threshold (or justified exclusion).
3. Lint/type-check/format clean; secret & dependency scans pass.
4. No secrets/PII added; untrusted content handled safely.
5. Relevant docs (requirements/architecture/testing/security/README) updated.
6. Traceability link (`FR-*`/`NFR-*`) recorded.
