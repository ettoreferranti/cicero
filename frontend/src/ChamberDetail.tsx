import { useCallback, useEffect, useState } from "react";
import * as api from "./api";
import { canRun, groupTurnsByRound, speakerName, stanceLabel } from "./format";
import { useDebateStream } from "./useDebateStream";
import type { Chamber, ParticipantMetrics, Provider, Stance, Turn } from "./types";

const PROVIDERS: Provider[] = ["mock", "ollama", "anthropic"];
const STANCES: Stance[] = ["neutral", "pro", "con"];

export function ChamberDetail({
  chamberId,
  onBack,
}: {
  chamberId: string;
  onBack: () => void;
}) {
  const [chamber, setChamber] = useState<Chamber | null>(null);
  const [metrics, setMetrics] = useState<ParticipantMetrics[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);

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
      setChamber(await api.getChamber(chamberId));
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
      <button onClick={onBack}>← Back</button>
      <h1>{chamber.topic}</h1>
      <p className="muted">
        {chamber.category && <>Category: {chamber.category} · </>}
        Status: {streaming ? (stream.status ?? "running") : chamber.status}
      </p>
      {error && <p className="error">{error}</p>}

      <div className="card">
        <h2>Participants</h2>
        {chamber.participants.length === 0 && <p className="muted">None yet.</p>}
        {chamber.participants.map((p) => (
          <div key={p.id} className="row">
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
        {rounds.length === 0 ? (
          <p className="muted">No turns yet.</p>
        ) : (
          rounds.map(({ round, turns }) => (
            <div key={round}>
              <h3>Round {round + 1}</h3>
              {turns.map((t) => (
                <div key={t.id} className="turn">
                  <strong>{speakerName(chamber.participants, t.participant_id)}</strong>
                  <div>{t.content}</div>
                </div>
              ))}
            </div>
          ))
        )}
      </div>

      {consensus && (
        <div className="card">
          <h2>Outcome: {consensus.outcome}</h2>
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
