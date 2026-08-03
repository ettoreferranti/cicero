import { useCallback, useEffect, useState } from "react";
import * as api from "./api";
import {
  canResume,
  canRun,
  canStep,
  groupTurnsByRound,
  liveStatusMessage,
  mergeTurns,
  outcomeLabel,
  participantColor,
  stanceLabel,
  stanceTrajectories,
  tuningSummary,
  turnSpeaker,
} from "./format";
import { ParticipantForm, type ParticipantDraft } from "./ParticipantForm";
import { useDebateStream } from "./useDebateStream";
import type {
  Chamber,
  DebateSettings,
  DecisionRule,
  ParticipantMetrics,
  Turn,
} from "./types";

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
  // A step runs synchronously on the server: hold the controls until it returns.
  const [stepping, setStepping] = useState(false);
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

  // Id of the participant currently open for editing (D4/FR-3).
  const [editingParticipant, setEditingParticipant] = useState<string | null>(null);
  // Draft copy of the chamber's own fields while its edit form is open.
  const [chamberForm, setChamberForm] = useState<{
    topic: string;
    category: string;
    description: string;
  } | null>(null);

  const stream = useDebateStream(chamberId, streaming);

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

  async function onAddParticipant(draft: ParticipantDraft) {
    setError(null);
    try {
      setChamber(await api.addParticipant(chamberId, draft));
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to add participant");
    }
  }

  async function onEditParticipant(participantId: string, draft: ParticipantDraft) {
    setError(null);
    try {
      setChamber(await api.updateParticipant(chamberId, participantId, draft));
      setEditingParticipant(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to update participant");
    }
  }

  async function onRemoveParticipant(participantId: string, name: string) {
    if (!window.confirm(`Remove ${name} from this chamber?`)) return;
    setError(null);
    try {
      setChamber(await api.removeParticipant(chamberId, participantId));
      setEditingParticipant((current) => (current === participantId ? null : current));
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to remove participant");
    }
  }

  async function onSaveChamber(event: React.FormEvent) {
    event.preventDefault();
    if (!chamberForm) return;
    setError(null);
    try {
      setChamber(await api.updateChamber(chamberId, chamberForm));
      setChamberForm(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to update chamber");
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

  async function onResume() {
    setError(null);
    try {
      await api.resumeDebate(chamberId);
      setStreaming(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to resume debate");
    }
  }

  async function onStep() {
    setError(null);
    setStepping(true);
    try {
      const updated = await api.stepDebate(chamberId);
      setChamber(updated);
      if (updated.status === "concluded") {
        await api.getMetrics(chamberId).then(setMetrics).catch(() => undefined);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to step the debate");
    } finally {
      setStepping(false);
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

  // On resume, earlier persisted turns stay visible under the live stream.
  const liveTurns: Turn[] = streaming
    ? mergeTurns(chamber.turns, stream.liveTurns)
    : chamber.turns;
  const rounds = groupTurnsByRound(liveTurns);
  const consensus = stream.consensus ?? chamber.consensus;
  const liveStatus = streaming ? (stream.status ?? "running") : chamber.status;
  const runnable = canRun(chamber.status, chamber.participants.length);
  const resumable = canResume(chamber.status, chamber.participants.length);
  const steppable = canStep(chamber.status, chamber.participants.length);

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
      {chamberForm ? (
        <form onSubmit={onSaveChamber} aria-label="edit chamber">
          <div className="row" style={{ flexWrap: "wrap", gap: "0.75rem" }}>
            <input
              aria-label="topic"
              placeholder="Topic"
              value={chamberForm.topic}
              onChange={(e) => setChamberForm({ ...chamberForm, topic: e.target.value })}
              style={{ flex: 1, minWidth: "16rem" }}
              required
            />
            <input
              aria-label="category"
              placeholder="Category"
              value={chamberForm.category}
              onChange={(e) => setChamberForm({ ...chamberForm, category: e.target.value })}
            />
            <input
              aria-label="description"
              placeholder="Description"
              value={chamberForm.description}
              onChange={(e) =>
                setChamberForm({ ...chamberForm, description: e.target.value })
              }
              style={{ flex: 1, minWidth: "16rem" }}
            />
            <button type="submit">Save</button>
            <button type="button" onClick={() => setChamberForm(null)}>
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <div className="row" style={{ gap: "0.75rem" }}>
          <h1 style={{ margin: 0 }}>{chamber.topic}</h1>
          {chamber.status === "draft" && (
            <button
              onClick={() =>
                setChamberForm({
                  topic: chamber.topic,
                  category: chamber.category,
                  description: chamber.description,
                })
              }
              title="Edit the topic, category and description"
            >
              Edit
            </button>
          )}
        </div>
      )}
      <p className="muted">
        {chamber.category && <>Category: {chamber.category} · </>}
        Status: {liveStatus}
      </p>
      {chamber.description && <p>{chamber.description}</p>}
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
        {chamber.participants.map((p) =>
          editingParticipant === p.id ? (
            <ParticipantForm
              key={p.id}
              formLabel={`edit ${p.display_name}`}
              submitLabel="Save"
              initial={{
                display_name: p.display_name,
                provider: p.provider,
                model: p.model,
                stance: p.stance,
                tuning: p.tuning,
              }}
              onSubmit={(draft) => onEditParticipant(p.id, draft)}
              onCancel={() => setEditingParticipant(null)}
            />
          ) : (
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
              {tuningSummary(p.tuning) && (
                <span
                  className="muted"
                  style={{ fontSize: "0.85rem" }}
                  title={
                    [p.tuning.persona, p.tuning.style].filter(Boolean).join(" — ") ||
                    undefined
                  }
                >
                  ⚙ {tuningSummary(p.tuning)}
                </span>
              )}
              {chamber.status === "draft" && (
                <>
                  <button
                    onClick={() => setEditingParticipant(p.id)}
                    aria-label={`edit ${p.display_name}`}
                  >
                    Edit
                  </button>
                  <button
                    className="danger"
                    onClick={() => onRemoveParticipant(p.id, p.display_name)}
                    aria-label={`remove ${p.display_name}`}
                  >
                    Remove
                  </button>
                </>
              )}
            </div>
          ),
        )}

        {chamber.status === "draft" && (
          <div style={{ marginTop: "0.75rem" }}>
            <ParticipantForm
              formLabel="add participant"
              submitLabel="Add"
              onSubmit={onAddParticipant}
            />
          </div>
        )}
      </div>

      <div className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2>Debate</h2>
          <div className="row">
            {resumable ? (
              <button className="primary" onClick={onResume} disabled={streaming || stepping}>
                Resume
              </button>
            ) : (
              <button
                className="primary"
                onClick={onStart}
                disabled={!runnable || streaming || stepping}
              >
                Start
              </button>
            )}
            <button
              onClick={onStep}
              disabled={!steppable || streaming || stepping}
              title="Run a single turn, then pause"
            >
              {stepping ? "Stepping…" : "Step"}
            </button>
            <button onClick={onStop} disabled={!streaming} title="Pauses the debate; resumable">
              Pause
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
        {/* Announces progress without reading whole turns aloud (NFR-U-2). */}
        <p aria-live="polite" className="visually-hidden">
          {liveStatusMessage(chamber.participants, liveTurns, liveStatus)}
        </p>
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

      {chamber.stance_history.length > 0 && (
        <div className="card">
          <h2>Stance history</h2>
          <p className="muted" style={{ fontSize: "0.85rem" }} id="stance-history-hint">
            Where each debater stood after each round, and whether they moved.
          </p>
          <div style={{ overflowX: "auto" }}>
            <table aria-describedby="stance-history-hint">
              <thead>
                <tr>
                  <th scope="col">Debater</th>
                  {chamber.stance_history.map((poll) => (
                    <th scope="col" key={poll.round_index}>
                      Round {poll.round_index + 1}
                    </th>
                  ))}
                  <th scope="col">Moved</th>
                </tr>
              </thead>
              <tbody>
                {stanceTrajectories(chamber.participants, chamber.stance_history).map(
                  ({ participant, stances, moved }) => (
                    <tr key={participant.id}>
                      <th scope="row" style={{ fontWeight: "normal" }}>
                        <span
                          className="dot"
                          style={{
                            background: participantColor(
                              chamber.participants,
                              participant.id,
                            ),
                          }}
                          aria-hidden="true"
                        />{" "}
                        {participant.display_name}
                      </th>
                      {stances.map((stance, index) => (
                        <td key={chamber.stance_history[index].round_index}>
                          <span className={`stance ${stance}`}>{stanceLabel(stance)}</span>
                        </td>
                      ))}
                      {/* Spelled out rather than a tick: a bare ✓ reads as
                          "check mark" and its absence reads as nothing. */}
                      <td>{moved ? "Yes" : "No"}</td>
                    </tr>
                  ),
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {metrics && metrics.length > 0 && (
        <div className="card">
          <h2>Metrics</h2>
          <table>
            <thead>
              <tr>
                <th scope="col">Participant</th>
                <th scope="col">Turns</th>
                <th scope="col">Prompt tok.</th>
                <th scope="col">Completion tok.</th>
                <th scope="col">Errors</th>
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
