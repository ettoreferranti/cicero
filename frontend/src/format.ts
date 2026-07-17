// Pure presentation helpers — no I/O, no React. These are the frontend's
// mutation-testing targets (see stryker.config.json).
import type { Participant, Stance, Turn } from "./types";

const STANCE_LABEL: Record<Stance, string> = {
  pro: "Pro",
  con: "Con",
  neutral: "Neutral",
};

export function stanceLabel(stance: Stance): string {
  return STANCE_LABEL[stance];
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

export function isRunning(status: string): boolean {
  return status === "running";
}
