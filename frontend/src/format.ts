// Pure presentation helpers — no I/O, no React. These are the frontend's
// mutation-testing targets (see stryker.config.json).
import { DEFAULT_TUNING } from "./types";
import type {
  Outcome,
  Participant,
  ParticipantTuning,
  Stance,
  StancePoll,
  Turn,
} from "./types";

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

/**
 * Compact summary of the tuning a debater deviates from the defaults with
 * (FR-11) — e.g. `temp 0.9 · 1200 tok · persona · style`. Empty when the
 * participant is fully default, so the roster stays quiet in the common case.
 */
export function tuningSummary(tuning: ParticipantTuning | undefined): string {
  if (!tuning) return "";
  const parts: string[] = [];
  if (tuning.temperature !== DEFAULT_TUNING.temperature) {
    parts.push(`temp ${tuning.temperature}`);
  }
  if (tuning.max_tokens !== DEFAULT_TUNING.max_tokens) {
    parts.push(`${tuning.max_tokens} tok`);
  }
  if (tuning.persona.trim() !== "") parts.push("persona");
  if (tuning.style.trim() !== "") parts.push("style");
  return parts.join(" · ");
}

/**
 * A one-line spoken summary of where a live debate has got to (NFR-U-2).
 *
 * Read out by an `aria-live` region: announcing whole turns would be
 * overwhelming, but announcing *nothing* leaves a screen-reader user with no
 * idea the debate is progressing. Naming the latest speaker and round is the
 * useful middle.
 */
export function liveStatusMessage(
  participants: Participant[],
  turns: Turn[],
  status: string | null,
): string {
  const spoken = turns.filter((turn) => turn.content.trim() !== "");
  const latest = spoken[spoken.length - 1];
  if (!latest) {
    return status ? `Debate ${status}.` : "";
  }
  const speaker = turnSpeaker(participants, latest);
  const position = `${speaker} spoke in round ${latest.round_index + 1}`;
  return status ? `${position}. Debate ${status}.` : `${position}.`;
}

/**
 * Each participant's stance across the recorded polls, plus whether they ever
 * moved (FR-25). A participant missing from a poll falls back to their declared
 * stance, which is what the engine itself does.
 */
export function stanceTrajectories(
  participants: Participant[],
  history: StancePoll[],
): { participant: Participant; stances: Stance[]; moved: boolean }[] {
  return participants.map((participant) => {
    const stances = history.map((poll) => poll.stances[participant.id] ?? participant.stance);
    return {
      participant,
      stances,
      moved: stances.some((stance) => stance !== stances[0]),
    };
  });
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

/** Stepping one turn works from either end of the start/resume pair. */
export function canStep(status: string, participantCount: number): boolean {
  return canRun(status, participantCount) || canResume(status, participantCount);
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
