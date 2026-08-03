// Pure presentation helpers — no I/O, no React. These are the frontend's
// mutation-testing targets (see stryker.config.json).
import type { Outcome, Participant, Stance, Turn } from "./types";

const STANCE_LABEL: Record<Stance, string> = {
  pro: "Pro",
  con: "Con",
  neutral: "Neutral",
};

export function stanceLabel(stance: Stance): string {
  return STANCE_LABEL[stance];
}

const OUTCOME_LABEL: Record<Outcome, string> = {
  consensus: "Consensus",
  majority: "Majority decision",
  verdict: "Judge's verdict",
  disagreement: "No agreement",
};

export function outcomeLabel(outcome: Outcome): string {
  return OUTCOME_LABEL[outcome] ?? outcome;
}

const SYSTEM_SPEAKER_LABEL: Record<string, string> = {
  moderator_note: "Moderator note",
  evidence: "Research (web evidence)",
};

// Distinct accent colours assigned to debaters by their position in the
// chamber (stable across rounds); system turns fall back to grey.
export const PARTICIPANT_COLORS = [
  "#60a5fa", // blue
  "#f59e0b", // amber
  "#34d399", // emerald
  "#f472b6", // pink
  "#a78bfa", // violet
  "#f87171", // red
  "#2dd4bf", // teal
  "#facc15", // yellow
] as const;

export const SYSTEM_TURN_COLOR = "#6b7280";

/** The accent colour for a participant's turns (grey for system/unknown). */
export function participantColor(
  participants: Participant[],
  participantId: string | null,
): string {
  const index = participants.findIndex((p) => p.id === participantId);
  if (index === -1) return SYSTEM_TURN_COLOR;
  return PARTICIPANT_COLORS[index % PARTICIPANT_COLORS.length];
}

/** Speaker label for any turn, including system-authored ones. */
export function turnSpeaker(participants: Participant[], turn: Turn): string {
  if (turn.participant_id === null) {
    const kind = typeof turn.metadata.kind === "string" ? turn.metadata.kind : "";
    return SYSTEM_SPEAKER_LABEL[kind] ?? "System";
  }
  return speakerName(participants, turn.participant_id);
}

/** Turns grouped by round index, ascending, skipping empty (error) turns. */
export function groupTurnsByRound(turns: Turn[]): { round: number; turns: Turn[] }[] {
  const byRound = new Map<number, Turn[]>();
  for (const turn of turns) {
    if (turn.content.trim() === "") continue;
    const existing = byRound.get(turn.round_index);
    if (existing) existing.push(turn);
    else byRound.set(turn.round_index, [turn]);
  }
  return [...byRound.keys()]
    .sort((a, b) => a - b)
    .map((round) => ({ round, turns: byRound.get(round)! }));
}

export function speakerName(participants: Participant[], participantId: string): string {
  const found = participants.find((p) => p.id === participantId);
  return found ? found.display_name : "Unknown";
}

/** A debate can be started only from the draft state with >= 2 participants. */
export function canRun(status: string, participantCount: number): boolean {
  return status === "draft" && participantCount >= 2;
}

/** A paused debate (stopped, or interrupted by a restart) can be resumed. */
export function canResume(status: string, participantCount: number): boolean {
  return status === "paused" && participantCount >= 2;
}

/**
 * Persisted turns followed by any live-streamed turns not yet persisted
 * (deduplicated by id) — so resuming a debate keeps its earlier transcript.
 */
export function mergeTurns(persisted: Turn[], live: Turn[]): Turn[] {
  const seen = new Set(persisted.map((t) => t.id));
  return [...persisted, ...live.filter((t) => !seen.has(t.id))];
}

export function isRunning(status: string): boolean {
  return status === "running";
}
