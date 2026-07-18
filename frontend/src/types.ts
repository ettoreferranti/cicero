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

export interface Participant {
  id: string;
  display_name: string;
  provider: string;
  model: string;
  stance: Stance;
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
