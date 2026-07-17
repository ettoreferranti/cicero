export type Stance = "pro" | "con" | "neutral";
export type Provider = "mock" | "ollama" | "anthropic";
export type ChamberStatus =
  | "draft"
  | "running"
  | "paused"
  | "concluded"
  | "archived";
export type Outcome = "consensus" | "disagreement";

export interface Participant {
  id: string;
  display_name: string;
  provider: string;
  model: string;
  stance: Stance;
}

export interface Turn {
  id: string;
  participant_id: string;
  round_index: number;
  content: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface ConsensusResult {
  outcome: Outcome;
  statement: string;
  final_stances: Record<string, Stance>;
}

export interface Chamber {
  id: string;
  topic: string;
  category: string;
  description: string;
  status: ChamberStatus;
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
