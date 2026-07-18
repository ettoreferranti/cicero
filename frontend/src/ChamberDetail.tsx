import { useCallback, useEffect, useState } from "react";
import * as api from "./api";
import {
  canRun,
  groupTurnsByRound,
  outcomeLabel,
  participantColor,
  stanceLabel,
  turnSpeaker,
} from "./format";
import { useDebateStream } from "./useDebateStream";
import type {
  Chamber,
  DebateSettings,
  DecisionRule,
  ParticipantMetrics,
  Provider,
  Stance,
  Turn,
} from "./types";

const PROVIDERS: Provider[] = ["mock", "ollama", "anthropic"];
const STANCES: Stance[] = ["neutral", "pro", "con"];
const DECISION_RULES: { value: DecisionRule; label: string; hint: string }[] = [
  { value: "judge", label: "Judge", hint: "majority wins; the moderator breaks ties" },
  { value: "majority", label: "Majority", hint: "plurality wins; a tie ends unresolved" },
  { value: "unanimous", label: "Unanimous", hint: "only full agreement counts" },
];

export function ChamberDetail({
  chamberId,
  onBack,
  onOpenChamber,
}: {
  chamberId: string;
  onBack: () => void;
  onOpenChamber?: (id: string) => void;
}) {
  const [chamber, setChamber] = useState<Chamber | null>(null);
  const [metrics, setMetrics] = useState<ParticipantMetrics[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [settingsForm, setSettingsForm] = useState<DebateSettings | null>(null);
  const [note, setNote] = useState("");
  // null = unknown (config not loaded); the checkbox stays usable then.
  const [webAccessEnabled, setWebAccessEnabled] = useState<boolean | null>(null);

  useEffect(() => {
    api
      .getServerConfig()
      .then((config) => setWebAccessEnabled(config.web_access_enabled))
      .catch(() => setWebAccessEnabled(null));
  }, []);

  // participant form
  const [name, setName] = useState("");
  const [provider, setProvider] = useState<Provider>("mock");
  const [model, setModel] = useState("mock-small");
  const [stance, setStance] = useState<Stance>("neutral");
  // Models available from the selected provider (e.g. loaded in Ollama).
  // null = lookup failed/unavailable -> fall back to a free-text field.
  const [availableModels, setAvailableModels] = useState<string[] | null>(null);

  const stream = useDebateStream(chamberId, streaming);

  // Refresh the model choices whenever the provider changes.
  useEffect(() => {
    let cancelled = false;
    setAvailableModels(null);
    api
      .listModels(provider)
      .then((models) => {
        if (cancelled || models.length === 0) return;
        setAvailableModels(models);
        setModel((current) => (models.includes(current) ? current : models[0]));
      })
      .catch(() => {
        // Provider unreachable (e.g. Ollama not running, no API key):
        // keep the free-text input so the user can still type a model.
      });
    return () => {
      cancelled = true;
    };
  }, [provider]);

  const load = useCallback(async () => {
    try {
      const loaded = await api.getChamber(chamberId);
      setChamber(loaded);
      setSettingsForm(loaded.settings);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load chamber");
    }
  }, [chamberId]);

  useEffect(() => {
    void load();
  }, [load]);

  // When the live stream signals completion, refresh persisted state.
  useEffect(() => {
    if (stream.done) {
      setStreaming(false);
      void load();
      void api.getMetrics(chamberId).then(setMetrics).catch(() => undefined);
    }
  }, [stream.done, load, chamberId]);

  async function onAddParticipant(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const updated = await api.addParticipant(chamberId, {
        display_name: name,
        provider,
        model,
        stance,
      });
      setChamber(updated);
      setName("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to add participant");
    }
  }

  async function onSaveSettings(event: React.FormEvent) {
    event.preventDefault();
    if (!settingsForm) return;
    setError(null);
    try {
      const updated = await api.updateSettings(chamberId, settingsForm);
      setChamber(updated);
      setSettingsForm(updated.settings);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to save settings");
    }
  }

  async function onAddNote(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await api.addNote(chamberId, note);
      setNote("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to add note");
    }
  }

  async function onStart() {
    setError(null);
    try {
      await api.startDebate(chamberId);
      setStreaming(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to start debate");
    }
  }

  async function onStop() {
    try {
      await api.stopDebate(chamberId);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to stop debate");
    }
  }

  async function onClone() {
    setError(null);
    try {
      const clone = await api.cloneChamber(chamberId);
      if (onOpenChamber) onOpenChamber(clone.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to clone chamber");
    }
  }

  async function onDelete() {
    if (!window.confirm("Delete this chamber and its transcript? This cannot be undone.")) {
      return;
    }
    setError(null);
    try {
      await api.deleteChamber(chamberId);
      onBack();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to delete chamber");
    }
  }

  if (!chamber) {
    return (
      <div>
        <button onClick={onBack}>← Back</button>
        <p className="muted">{error ?? "Loading…"}</p>
      </div>
    );
  }

  const liveTurns: Turn[] = streaming && stream.liveTurns.length > 0 ? stream.liveTurns : chamber.turns;
  const rounds = groupTurnsByRound(liveTurns);
  const consensus = stream.consensus ?? chamber.consensus;
  const runnable = canRun(chamber.status, chamber.participants.length);

  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <button onClick={onBack}>← Back</button>
        <div className="row">
          <button onClick={onClone} title="Create a fresh draft copy of this chamber to rerun">
            Clone &amp; rerun
          </button>
          <button className="danger" onClick={onDelete} disabled={streaming}>
            Delete
          </button>
        </div>
      </div>
      <h1>{chamber.topic}</h1>
      <p className="muted">
        {chamber.category && <>Category: {chamber.category} · </>}
        Status: {streaming ? (stream.status ?? "running") : chamber.status}
      </p>
      {error && <p className="error">{error}</p>}

      <div className="card">
        <h2>Debate settings</h2>
        {chamber.status === "draft" && settingsForm ? (
          <form onSubmit={onSaveSettings}>
            <div className="row" style={{ flexWrap: "wrap", gap: "0.75rem" }}>
              <label>
                Max rounds{" "}
                <input
                  type="number"
                  min={1}
                  max={100}
                  style={{ width: "4.5rem" }}
                  value={settingsForm.max_rounds}
                  onChange={(e) =>
                    setSettingsForm({ ...settingsForm, max_rounds: Number(e.target.value) })
                  }
                />
              </label>
              <label>
                Convergence rounds{" "}
                <input
                  type="number"
                  min={0}
                  max={100}
                  style={{ width: "4.5rem" }}
                  title="How many closing rounds are steered toward common ground"
                  value={settingsForm.convergence_rounds}
                  onChange={(e) =>
                    setSettingsForm({
                      ...settingsForm,
                      convergence_rounds: Number(e.target.value),
                    })
                  }
                />
              </label>
              <label>
                Token budget{" "}
                <input
                  type="number"
                  min={1}
                  max={5_000_000}
                  style={{ width: "7rem" }}
                  value={settingsForm.max_total_tokens}
                  onChange={(e) =>
                    setSettingsForm({
                      ...settingsForm,
                      max_total_tokens: Number(e.target.value),
                    })
                  }
                />
              </label>
              <label>
                Time limit (s){" "}
                <input
                  type="number"
                  min={1}
                  max={86_400}
                  style={{ width: "5.5rem" }}
                  placeholder="none"
                  value={settingsForm.max_duration_seconds ?? ""}
                  onChange={(e) =>
                    setSettingsForm({
                      ...settingsForm,
                      max_duration_seconds:
                        e.target.value === "" ? null : Number(e.target.value),
                    })
                  }
                />
              </label>
              <label>
                Decision rule{" "}
                <select
                  value={settingsForm.decision_rule}
                  onChange={(e) =>
                    setSettingsForm({
                      ...settingsForm,
                      decision_rule: e.target.value as DecisionRule,
                    })
                  }
                >
                  {DECISION_RULES.map((r) => (
                    <option key={r.value} value={r.value} title={r.hint}>
                      {r.label} — {r.hint}
                    </option>
                  ))}
                </select>
              </label>
              <label
                className={webAccessEnabled === false ? "muted" : undefined}
                title={
                  webAccessEnabled === false
                    ? "Disabled on this server — set WEB_ACCESS_ENABLED=true in backend/.env and restart"
                    : "Upfront research brief + per-turn searches, via the sandboxed fetcher"
                }
              >
                <input
                  type="checkbox"
                  checked={settingsForm.web_evidence}
                  disabled={webAccessEnabled === false}
                  onChange={(e) =>
                    setSettingsForm({ ...settingsForm, web_evidence: e.target.checked })
                  }
                />{" "}
                Web research
                {webAccessEnabled === false && " — disabled on this server (WEB_ACCESS_ENABLED)"}
              </label>
              <button type="submit">Save settings</button>
            </div>
          </form>
        ) : (
          <p className="muted">
            {chamber.settings.max_rounds} rounds max
            {chamber.settings.max_duration_seconds !== null &&
              ` · ${chamber.settings.max_duration_seconds}s limit`}{" "}
            · {chamber.settings.max_total_tokens.toLocaleString()} tokens · rule:{" "}
            {chamber.settings.decision_rule}
            {chamber.settings.web_evidence &&
              (webAccessEnabled === false
                ? " · web research (unavailable on this server)"
                : " · web research")}
          </p>
        )}
      </div>

      <div className="card">
        <h2>Participants</h2>
        {chamber.participants.length === 0 && <p className="muted">None yet.</p>}
        {chamber.participants.map((p) => (
          <div key={p.id} className="row">
            <span
              className="dot"
              style={{ background: participantColor(chamber.participants, p.id) }}
              aria-hidden="true"
            />
            <strong>{p.display_name}</strong>
            <span className={`stance ${p.stance}`}>{stanceLabel(p.stance)}</span>
            <span className="muted">
              {p.provider} / {p.model}
            </span>
          </div>
        ))}

        {chamber.status === "draft" && (
          <form className="row" onSubmit={onAddParticipant} style={{ marginTop: "0.75rem" }}>
            <input
              aria-label="participant name"
              placeholder="Name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
            <select
              aria-label="provider"
              value={provider}
              onChange={(e) => setProvider(e.target.value as Provider)}
            >
              {PROVIDERS.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
            {availableModels ? (
              <select
                aria-label="model"
                value={model}
                onChange={(e) => setModel(e.target.value)}
              >
                {availableModels.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            ) : (
              <input
                aria-label="model"
                placeholder="Model"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                required
              />
            )}
            <select
              aria-label="stance"
              value={stance}
              onChange={(e) => setStance(e.target.value as Stance)}
            >
              {STANCES.map((s) => (
                <option key={s} value={s}>
                  {stanceLabel(s)}
                </option>
              ))}
            </select>
            <button type="submit">Add</button>
          </form>
        )}
      </div>

      <div className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2>Debate</h2>
          <div className="row">
            <button className="primary" onClick={onStart} disabled={!runnable || streaming}>
              Start
            </button>
            <button onClick={onStop} disabled={!streaming}>
              Stop
            </button>
          </div>
        </div>
        {!runnable && chamber.status === "draft" && (
          <p className="muted">Add at least two participants to start.</p>
        )}
        {streaming && (
          <form className="row" onSubmit={onAddNote} style={{ margin: "0.5rem 0" }}>
            <input
              aria-label="moderator note"
              placeholder="Inject a moderator note into the debate…"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              style={{ flex: 1 }}
            />
            <button type="submit" disabled={!note.trim()}>
              Send note
            </button>
          </form>
        )}
        {rounds.length === 0 ? (
          <p className="muted">No turns yet.</p>
        ) : (
          rounds.map(({ round, turns }) => (
            <div key={round}>
              <h3>Round {round + 1}</h3>
              {turns.map((t) => (
                <div
                  key={t.id}
                  className="turn"
                  style={{
                    borderLeftColor: participantColor(chamber.participants, t.participant_id),
                  }}
                >
                  <strong
                    style={{ color: participantColor(chamber.participants, t.participant_id) }}
                  >
                    {turnSpeaker(chamber.participants, t)}
                  </strong>
                  <div>{t.content}</div>
                  {Array.isArray(t.metadata.searches) && t.metadata.searches.length > 0 && (
                    <div className="muted" style={{ fontSize: "0.85rem" }}>
                      🔎 searched: {t.metadata.searches.join(" · ")}
                    </div>
                  )}
                  {t.citations && t.citations.length > 0 && (
                    <ul className="muted" style={{ fontSize: "0.85rem" }}>
                      {t.citations.map((c) => (
                        <li key={c.id}>
                          <a href={c.url} target="_blank" rel="noreferrer">
                            {c.title || c.url}
                          </a>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ))}
            </div>
          ))
        )}
      </div>

      {consensus && (
        <div className="card">
          <h2>Outcome: {outcomeLabel(consensus.outcome)}</h2>
          {consensus.winning_stance && (
            <p>
              Winning position:{" "}
              <span className={`stance ${consensus.winning_stance}`}>
                {stanceLabel(consensus.winning_stance)}
              </span>
            </p>
          )}
          <p style={{ whiteSpace: "pre-wrap" }}>{consensus.statement}</p>
        </div>
      )}

      {metrics && metrics.length > 0 && (
        <div className="card">
          <h2>Metrics</h2>
          <table>
            <thead>
              <tr>
                <th>Participant</th>
                <th>Turns</th>
                <th>Prompt tok.</th>
                <th>Completion tok.</th>
                <th>Errors</th>
              </tr>
            </thead>
            <tbody>
              {metrics.map((m) => (
                <tr key={m.participant_id}>
                  <td>{m.display_name}</td>
                  <td>{m.turns}</td>
                  <td>{m.prompt_tokens}</td>
                  <td>{m.completion_tokens}</td>
                  <td>{m.errors}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="row">
        <a href={api.exportUrl(chamberId, "json")} target="_blank" rel="noreferrer">
          Export JSON
        </a>
        <a href={api.exportUrl(chamberId, "markdown")} target="_blank" rel="noreferrer">
          Export Markdown
        </a>
      </div>
    </div>
  );
}
