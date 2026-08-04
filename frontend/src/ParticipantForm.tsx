import { useEffect, useRef, useState } from "react";
import * as api from "./api";
import { stanceLabel, tuningSummary } from "./format";
import { DEFAULT_TUNING } from "./types";
import type { ParticipantTuning, Provider, Stance } from "./types";

const PROVIDERS: Provider[] = ["mock", "ollama", "anthropic"];
const STANCES: Stance[] = ["neutral", "pro", "con"];

export interface ParticipantDraft {
  display_name: string;
  provider: Provider;
  model: string;
  stance: Stance;
  tuning: ParticipantTuning;
}

const BLANK: ParticipantDraft = {
  display_name: "",
  provider: "mock",
  model: "mock-small",
  stance: "neutral",
  tuning: DEFAULT_TUNING,
};

/**
 * The name/provider/model/stance controls, shared by the add and edit flows
 * (D4/FR-3). Owns the provider → available-models lookup, so both get the live
 * model selector and the same free-text fallback when a provider is unreachable.
 */
export function ParticipantForm({
  formLabel,
  submitLabel,
  initial,
  accent,
  onSubmit,
  onCancel,
}: {
  formLabel: string;
  submitLabel: string;
  /** Present when editing; absent means "add", which clears the name on submit. */
  initial?: ParticipantDraft;
  /** This debater's transcript colour, tying the form to its roster row. */
  accent?: string;
  onSubmit: (draft: ParticipantDraft) => Promise<void>;
  onCancel?: () => void;
}) {
  const [draft, setDraft] = useState<ParticipantDraft>(initial ?? BLANK);
  const [busy, setBusy] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);
  const editing = initial !== undefined;

  // An edit form replaces a roster row in place; without moving focus into it,
  // a keyboard or screen-reader user is left where the row used to be (NFR-U-2).
  useEffect(() => {
    if (editing) nameRef.current?.focus();
  }, [editing]);
  // Models available from the selected provider (e.g. loaded in Ollama).
  // null = lookup failed/unavailable -> fall back to a free-text field.
  const [availableModels, setAvailableModels] = useState<string[] | null>(null);

  // Refresh the model choices whenever the provider changes.
  useEffect(() => {
    let cancelled = false;
    setAvailableModels(null);
    api
      .listModels(draft.provider)
      .then((models) => {
        if (cancelled || models.length === 0) return;
        setAvailableModels(models);
        setDraft((current) =>
          models.includes(current.model) ? current : { ...current, model: models[0] },
        );
      })
      .catch(() => {
        // Provider unreachable (e.g. Ollama not running, no API key):
        // keep the free-text input so the user can still type a model.
      });
    return () => {
      cancelled = true;
    };
  }, [draft.provider]);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await onSubmit(draft);
      // Adding stays put on the same provider/model so a roster is quick to
      // build; only the name is cleared, and focus returns there for the next
      // debater. Editing leaves the form as-is (the row closes over it).
      if (!editing) {
        setDraft((current) => ({ ...current, display_name: "" }));
        nameRef.current?.focus();
      }
    } finally {
      setBusy(false);
    }
  }

  function setTuning(patch: Partial<ParticipantTuning>) {
    setDraft({ ...draft, tuning: { ...draft.tuning, ...patch } });
  }

  const summary = tuningSummary(draft.tuning);
  // Everything in this form configures exactly one debater. Saying whose makes
  // the "Tuning" panel below unmistakably per-debater rather than chamber-wide.
  const who = draft.display_name.trim() || "this debater";

  return (
    <form
      aria-label={formLabel}
      onSubmit={handleSubmit}
      className="scoped"
      style={accent ? { borderLeftColor: accent } : undefined}
    >
      <div className="row">
      <input
        ref={nameRef}
        aria-label="participant name"
        placeholder="Name"
        value={draft.display_name}
        onChange={(e) => setDraft({ ...draft, display_name: e.target.value })}
        required
      />
      <select
        aria-label="provider"
        value={draft.provider}
        onChange={(e) => setDraft({ ...draft, provider: e.target.value as Provider })}
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
          value={draft.model}
          onChange={(e) => setDraft({ ...draft, model: e.target.value })}
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
          value={draft.model}
          onChange={(e) => setDraft({ ...draft, model: e.target.value })}
          required
        />
      )}
      <select
        aria-label="stance"
        value={draft.stance}
        onChange={(e) => setDraft({ ...draft, stance: e.target.value as Stance })}
      >
        {STANCES.map((s) => (
          <option key={s} value={s}>
            {stanceLabel(s)}
          </option>
        ))}
      </select>
      <button type="submit" disabled={busy}>
        {submitLabel}
      </button>
      {onCancel && (
        <button type="button" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      )}
      </div>

      {/* Tuning is opt-in detail (FR-11): collapsed unless it differs from
          the defaults, so the common case stays a single compact row. Named
          after the debater — an unqualified "Tuning" reads as chamber-wide. */}
      <details open={summary !== ""} style={{ marginTop: "0.4rem" }}>
        <summary className="muted" style={{ cursor: "pointer", fontSize: "0.85rem" }}>
          Tuning for {who}
          {summary && ` — ${summary}`}
        </summary>
        <div className="row" style={{ marginTop: "0.4rem" }}>
          <label className="muted" style={{ fontSize: "0.85rem" }}>
            Temperature{" "}
            <input
              type="number"
              min={0}
              max={2}
              step={0.1}
              style={{ width: "5rem" }}
              value={draft.tuning.temperature}
              onChange={(e) => setTuning({ temperature: Number(e.target.value) })}
            />
          </label>
          <label className="muted" style={{ fontSize: "0.85rem" }}>
            Max tokens{" "}
            <input
              type="number"
              min={1}
              max={32768}
              style={{ width: "6rem" }}
              value={draft.tuning.max_tokens}
              onChange={(e) => setTuning({ max_tokens: Number(e.target.value) })}
            />
          </label>
          <input
            aria-label="persona"
            placeholder="Persona (e.g. a cautious economist)"
            value={draft.tuning.persona}
            onChange={(e) => setTuning({ persona: e.target.value })}
            style={{ flex: 1, minWidth: "14rem" }}
          />
          <input
            aria-label="instructions"
            placeholder="Instructions (e.g. be extra polite, speak in rhyme)"
            title="How this debater should argue. Applies to its debate turns only — not to the stance poll or the final statement."
            value={draft.tuning.instructions}
            onChange={(e) => setTuning({ instructions: e.target.value })}
            style={{ flex: 1, minWidth: "12rem" }}
          />
        </div>
      </details>
    </form>
  );
}
