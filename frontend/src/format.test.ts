import { describe, expect, it } from "vitest";
import { canRun, groupTurnsByRound, isRunning, speakerName, stanceLabel } from "./format";
import type { Participant, Turn } from "./types";

function turn(id: string, round: number, content: string, participantId = "p"): Turn {
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

describe("canRun", () => {
  it("is true only for a draft with >= 2 participants", () => {
    expect(canRun("draft", 2)).toBe(true);
    expect(canRun("draft", 1)).toBe(false);
    expect(canRun("running", 2)).toBe(false);
    expect(canRun("concluded", 3)).toBe(false);
  });
});

describe("isRunning", () => {
  it("detects the running status", () => {
    expect(isRunning("running")).toBe(true);
    expect(isRunning("draft")).toBe(false);
  });
});
