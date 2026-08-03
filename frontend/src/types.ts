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
  style: string;
}

export const DEFAULT_TUNING: ParticipantTuning = {
  temperature: 0.7,
  max_tokens: 800,
  persona: "",
  style: "",
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
  created_at: string;
}

export interface ConsensusResult {
  outcome: Outcome;
  statement: string;
  winning_stance: Stance | null;
  final_stances: Record<string, Stance>;
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
