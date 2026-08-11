export type Stance = "pro" | "con" | "neutral";
export type Provider = "mock" | "ollama" | "anthropic";
export type ChamberStatus =
  | "draft"
  | "running"
  | "paused"
  | "concluded"
  | "archived";
export type Outcome = "consensus" | "majority" | "verdict" | "disagreement";
export type DecisionRule = "unanimous" | "majority" | "judge";

export interface DebateSettings {
  max_rounds: number;
  max_total_tokens: number;
  max_duration_seconds: number | null;
  min_rounds: number;
  decision_rule: DecisionRule;
  convergence_rounds: number;
  web_evidence: boolean;
  // Judge each turn for which side it argues — one extra model call per turn.
  measure_compliance: boolean;
  // End the debate once every active debater is only restating themselves.
  stop_on_repetition: boolean;
  // How alike two turns must be to count as a repeat; 1.0 = byte-identical.
  repetition_threshold: number;
}

export interface Citation {
  id: string;
  url: string;
  title: string;
  excerpt: string;
}

/** Per-participant generation settings (FR-11); mirrors ParticipantTuning. */
export interface ParticipantTuning {
  temperature: number;
  max_tokens: number;
  persona: string;
  /** How this debater should argue — "be extra polite", "speak in rhyme". */
  instructions: string;
}

// Must match ParticipantTuning's defaults in the backend: tuningSummary() marks
// anyone who differs, so a stale value here labels every participant "tuned".
export const DEFAULT_TUNING: ParticipantTuning = {
  temperature: 0.7,
  max_tokens: 2048,
  persona: "",
  instructions: "",
};

export interface Participant {
  id: string;
  display_name: string;
  // The API validates this against the ProviderType enum, so it is never a
  // free-form string — the edit form relies on that.
  provider: Provider;
  model: string;
  stance: Stance;
  tuning: ParticipantTuning;
  // A muted debater stops taking turns and stops counting toward the decision
  // rule, but stays in the chamber and is still polled for its stance (FR-13).
  muted: boolean;
}

export interface Turn {
  id: string;
  // null = system-authored (moderator note or web evidence; see metadata.kind)
  participant_id: string | null;
  round_index: number;
  content: string;
  citations?: Citation[];
  metadata: Record<string, unknown>;
  created_at: string;
}

/** Every participant's stance as measured after one round (FR-25). */
export interface StancePoll {
  round_index: number;
  stances: Record<string, Stance>;
  // Ids whose reply could not be read: their stance here was carried over from
  // the previous poll, not measured, so it is not evidence of a position.
  unparsed: string[];
  created_at: string;
}

export interface ConsensusResult {
  outcome: Outcome;
  statement: string;
  // One declarative sentence stating what the chamber concluded. Empty when the
  // moderator produced none that was usable — the card then falls back.
  headline: string;
  winning_stance: Stance | null;
  final_stances: Record<string, Stance>;
  // Ids whose final position could not be read. null means "not recorded" — a
  // chamber concluded before this field existed — not "none failed".
  unparsed: string[] | null;
}

/** Derived outcome facts, served by GET /chambers/{id}/outcome. */
export interface OutcomeSummary {
  headline: string;
  support: string;
  decided_by: string;
  movements: string[];
  // Qualifies the stance labels above when they could be read as a
  // characterisation of a debater rather than as what the poll recorded.
  // Empty when no label on display can mislead.
  caveat: string;
  // Fires when the winning position was never argued against, though a debater
  // was assigned to. Distinct from `caveat`, which qualifies the stance labels.
  compliance_caveat: string;
  // One line per debater judged to have argued against its assigned side.
  noncompliance: string[];
}

/** Who writes the outcome. Not a debater: no stance, no turns, no vote. */
export interface Moderator {
  provider: Provider;
  model: string;
  max_tokens: number;
  temperature: number;
}

export interface Chamber {
  id: string;
  topic: string;
  category: string;
  description: string;
  status: ChamberStatus;
  settings: DebateSettings;
  participants: Participant[];
  turns: Turn[];
  stance_history: StancePoll[];
  consensus: ConsensusResult | null;
  // null means "the first participant", which is what the engine did before this
  // field existed.
  moderator: Moderator | null;
  config: Record<string, unknown>;
  created_at: string;
}

export interface ParticipantMetrics {
  participant_id: string;
  display_name: string;
  provider: string;
  turns: number;
  prompt_tokens: number;
  completion_tokens: number;
  errors: number;
}

export type DebateEventType =
  | "status"
  | "turn"
  | "consensus"
  | "error"
  | "done";

export interface DebateEvent {
  type: DebateEventType;
  payload: Record<string, unknown>;
}
