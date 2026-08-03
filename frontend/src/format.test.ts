import { describe, expect, it } from "vitest";
import {
  PARTICIPANT_COLORS,
  SYSTEM_TURN_COLOR,
  canResume,
  canRun,
  canStep,
  groupTurnsByRound,
  isRunning,
  mergeTurns,
  outcomeLabel,
  participantColor,
  speakerName,
  stanceLabel,
  turnSpeaker,
} from "./format";
import type { Participant, Turn } from "./types";

function turn(
  id: string,
  round: number,
  content: string,
  participantId: string | null = "p",
): Turn {
  return {
    id,
    participant_id: participantId,
    round_index: round,
    content,
    metadata: {},
    created_at: "2026-01-01T00:00:00Z",
  };
}

describe("stanceLabel", () => {
  it("maps each stance to a capitalised label", () => {
    expect(stanceLabel("pro")).toBe("Pro");
    expect(stanceLabel("con")).toBe("Con");
    expect(stanceLabel("neutral")).toBe("Neutral");
  });
});

describe("groupTurnsByRound", () => {
  it("groups turns by round in ascending order", () => {
    const groups = groupTurnsByRound([
      turn("a", 1, "second round"),
      turn("b", 0, "first round"),
      turn("c", 0, "first round again"),
    ]);
    expect(groups.map((g) => g.round)).toEqual([0, 1]);
    expect(groups[0].turns).toHaveLength(2);
    expect(groups[1].turns).toHaveLength(1);
  });

  it("skips empty (error) turns", () => {
    const groups = groupTurnsByRound([turn("a", 0, "   "), turn("b", 0, "real")]);
    expect(groups).toHaveLength(1);
    expect(groups[0].turns).toHaveLength(1);
    expect(groups[0].turns[0].content).toBe("real");
  });

  it("returns nothing for no visible turns", () => {
    expect(groupTurnsByRound([turn("a", 0, "  ")])).toEqual([]);
  });
});

describe("speakerName", () => {
  const participants: Participant[] = [
    { id: "p1", display_name: "Ada", provider: "mock", model: "m", stance: "pro" },
  ];
  it("returns the display name for a known participant", () => {
    expect(speakerName(participants, "p1")).toBe("Ada");
  });
  it("falls back to Unknown for an unknown id", () => {
    expect(speakerName(participants, "nope")).toBe("Unknown");
  });
});

describe("turnSpeaker", () => {
  const participants: Participant[] = [
    { id: "p1", display_name: "Ada", provider: "mock", model: "m", stance: "pro" },
  ];
  it("uses the participant name for normal turns", () => {
    expect(turnSpeaker(participants, turn("a", 0, "x", "p1"))).toBe("Ada");
  });
  it("labels system turns by their kind", () => {
    const noteTurn = { ...turn("n", 0, "x", null), metadata: { kind: "moderator_note" } };
    const evidenceTurn = { ...turn("e", 0, "x", null), metadata: { kind: "evidence" } };
    const unknownTurn = turn("u", 0, "x", null);
    expect(turnSpeaker(participants, noteTurn)).toBe("Moderator note");
    expect(turnSpeaker(participants, evidenceTurn)).toBe("Research (web evidence)");
    expect(turnSpeaker(participants, unknownTurn)).toBe("System");
  });
});

describe("participantColor", () => {
  const participant = (id: string): Participant => ({
    id,
    display_name: id,
    provider: "mock",
    model: "m",
    stance: "neutral",
  });

  it("assigns each debater a distinct, stable palette colour", () => {
    const participants = ["p1", "p2", "p3"].map(participant);
    expect(participantColor(participants, "p1")).toBe(PARTICIPANT_COLORS[0]);
    expect(participantColor(participants, "p2")).toBe(PARTICIPANT_COLORS[1]);
    expect(participantColor(participants, "p3")).toBe(PARTICIPANT_COLORS[2]);
    // Stable: same input, same colour.
    expect(participantColor(participants, "p2")).toBe(participantColor(participants, "p2"));
  });

  it("wraps around the palette for many participants", () => {
    const many = Array.from({ length: PARTICIPANT_COLORS.length + 1 }, (_, i) =>
      participant(`p${i}`),
    );
    expect(participantColor(many, `p${PARTICIPANT_COLORS.length}`)).toBe(PARTICIPANT_COLORS[0]);
  });

  it("uses grey for system and unknown turns", () => {
    const participants = [participant("p1")];
    expect(participantColor(participants, null)).toBe(SYSTEM_TURN_COLOR);
    expect(participantColor(participants, "ghost")).toBe(SYSTEM_TURN_COLOR);
  });
});

describe("outcomeLabel", () => {
  it("maps every outcome to a readable label", () => {
    expect(outcomeLabel("consensus")).toBe("Consensus");
    expect(outcomeLabel("majority")).toBe("Majority decision");
    expect(outcomeLabel("verdict")).toBe("Judge's verdict");
    expect(outcomeLabel("disagreement")).toBe("No agreement");
  });
});

describe("canRun", () => {
  it("is true only for a draft with >= 2 participants", () => {
    expect(canRun("draft", 2)).toBe(true);
    expect(canRun("draft", 1)).toBe(false);
    expect(canRun("running", 2)).toBe(false);
    expect(canRun("concluded", 3)).toBe(false);
  });
});

describe("canResume", () => {
  it("is true only for a paused chamber with >= 2 participants", () => {
    expect(canResume("paused", 2)).toBe(true);
    expect(canResume("paused", 1)).toBe(false);
    expect(canResume("draft", 2)).toBe(false);
    expect(canResume("concluded", 2)).toBe(false);
  });
});

describe("canStep", () => {
  it("is true for a draft or paused chamber with >= 2 participants", () => {
    expect(canStep("draft", 2)).toBe(true);
    expect(canStep("paused", 2)).toBe(true);
    expect(canStep("draft", 1)).toBe(false);
    expect(canStep("paused", 1)).toBe(false);
    expect(canStep("running", 2)).toBe(false);
    expect(canStep("concluded", 2)).toBe(false);
  });
});

describe("mergeTurns", () => {
  it("appends live turns after persisted ones, deduplicating by id", () => {
    const persisted = [turn("a", 0, "old"), turn("b", 1, "kept")];
    const live = [turn("b", 1, "kept"), turn("c", 1, "new")];
    const merged = mergeTurns(persisted, live);
    expect(merged.map((t) => t.id)).toEqual(["a", "b", "c"]);
  });

  it("handles a fresh debate with no persisted turns", () => {
    const live = [turn("x", 0, "first")];
    expect(mergeTurns([], live)).toEqual(live);
    expect(mergeTurns(live, [])).toEqual(live);
  });
});

describe("isRunning", () => {
  it("detects the running status", () => {
    expect(isRunning("running")).toBe(true);
    expect(isRunning("draft")).toBe(false);
  });
});
