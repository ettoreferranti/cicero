import { useEffect, useState } from "react";
import * as api from "./api";
import { stanceLabel } from "./format";
import type { Provider, Stance } from "./types";

const PROVIDERS: Provider[] = ["mock", "ollama", "anthropic"];
const STANCES: Stance[] = ["neutral", "pro", "con"];

export interface ParticipantDraft {
  display_name: string;
  provider: Provider;
  model: string;
  stance: Stance;
}

const BLANK: ParticipantDraft = {
  display_name: "",
  provider: "mock",
  model: "mock-small",
  stance: "neutral",
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
  onSubmit,
  onCancel,
}: {
  formLabel: string;
  submitLabel: string;
  /** Present when editing; absent means "add", which clears the name on submit. */
  initial?: ParticipantDraft;
  onSubmit: (draft: ParticipantDraft) => Promise<void>;
  onCancel?: () => void;
}) {
  const [draft, setDraft] = useState<ParticipantDraft>(initial ?? BLANK);
  const [busy, setBusy] = useState(false);
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
      // build; only the name is cleared. Editing leaves the form as-is.
      if (!initial) setDraft((current) => ({ ...current, display_name: "" }));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="row" aria-label={formLabel} onSubmit={handleSubmit}>
      <input
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
    </form>
  );
}
