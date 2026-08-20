import { describe, expect, it } from "vitest";
import {
  PARTICIPANT_COLORS,
  SYSTEM_TURN_COLOR,
  canResume,
  canRun,
  canStep,
  groupTurnsByRound,
  isRunning,
  liveStatusMessage,
  mergeTurns,
  outcomeLabel,
  participantColor,
  speakerName,
  stanceLabel,
  stanceTrajectories,
  tuningSummary,
  turnSpeaker,
} from "./format";
import { DEFAULT_TUNING } from "./types";
import type { Participant, ParticipantTuning, Stance, StancePoll, Turn } from "./types";

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
    {
      id: "p1",
      display_name: "Ada",
      provider: "mock",
      model: "m",
      stance: "pro",
      tuning: DEFAULT_TUNING,
      muted: false,
    },
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
    {
      id: "p1",
      display_name: "Ada",
      provider: "mock",
      model: "m",
      stance: "pro",
      tuning: DEFAULT_TUNING,
      muted: false,
    },
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
    tuning: DEFAULT_TUNING,
    muted: false,
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

describe("liveStatusMessage", () => {
  const ada: Participant = {
    id: "p",
    display_name: "Ada",
    provider: "mock",
    model: "m",
    stance: "pro",
    tuning: DEFAULT_TUNING,
    muted: false,
  };

  it("names the latest speaker and round", () => {
    const turns = [turn("t1", 0, "first"), turn("t2", 1, "second")];
    expect(liveStatusMessage([ada], turns, "running")).toBe(
      "Ada spoke in round 2. Debate running.",
    );
  });

  it("skips empty (failed) turns when picking the latest", () => {
    const turns = [turn("t1", 0, "first"), turn("t2", 1, "   ")];
    expect(liveStatusMessage([ada], turns, null)).toBe("Ada spoke in round 1.");
  });

  it("falls back to the status alone before anyone has spoken", () => {
    expect(liveStatusMessage([ada], [], "running")).toBe("Debate running.");
    expect(liveStatusMessage([ada], [], null)).toBe("");
  });

  it("labels system turns rather than naming a debater", () => {
    const note = { ...turn("n", 0, "note", null), metadata: { kind: "moderator_note" } };
    expect(liveStatusMessage([ada], [note], "running")).toBe(
      "Moderator note spoke in round 1. Debate running.",
    );
  });
});

describe("stanceTrajectories", () => {
  const ada: Participant = {
    id: "p1",
    display_name: "Ada",
    provider: "mock",
    model: "m",
    stance: "pro",
    tuning: DEFAULT_TUNING,
    muted: false,
  };
  const zeno: Participant = { ...ada, id: "p2", display_name: "Zeno", stance: "con" };
  const poll = (
    round: number,
    stances: Record<string, Stance>,
    unparsed: string[] = [],
  ): StancePoll => ({
    round_index: round,
    stances,
    unparsed,
    created_at: "2026-01-01T00:00:00Z",
  });

  it("tracks each debater across the polls and flags who moved", () => {
    const history = [
      poll(0, { p1: "pro", p2: "con" }),
      poll(1, { p1: "pro", p2: "neutral" }),
    ];
    const rows = stanceTrajectories([ada, zeno], history);
    expect(rows[0]).toEqual({
      participant: ada,
      stances: ["pro", "pro"],
      unread: [false, false],
      moved: false,
    });
    expect(rows[1]).toEqual({
      participant: zeno,
      stances: ["con", "neutral"],
      unread: [false, false],
      moved: true,
    });
  });

  it("does not treat a carried-over value as evidence of holding firm", () => {
    // p1 looks steady across both polls, but the second was never measured —
    // calling that "did not move" would be inventing a finding.
    const history = [poll(0, { p1: "pro" }), poll(1, { p1: "pro" }, ["p1"])];
    const [row] = stanceTrajectories([ada], history);
    expect(row.unread).toEqual([false, true]);
    expect(row.moved).toBe(false);
    expect(row.stances).toEqual(["pro", "pro"]);
  });

  it("ignores unreadable polls when deciding whether someone moved", () => {
    // Measured pro then con: that is movement, whatever the unreadable middle
    // poll carried forward.
    const history = [
      poll(0, { p1: "pro" }),
      poll(1, { p1: "pro" }, ["p1"]),
      poll(2, { p1: "con" }),
    ];
    expect(stanceTrajectories([ada], history)[0].moved).toBe(true);
  });

  it("falls back to the declared stance when a poll omits someone", () => {
    // A debater added after a poll has no entry in it.
    const rows = stanceTrajectories([ada, zeno], [poll(0, { p1: "neutral" })]);
    expect(rows[0].stances).toEqual(["neutral"]);
    expect(rows[1].stances).toEqual(["con"]);
    expect(rows[1].moved).toBe(false);
  });

  it("returns an empty trajectory when nothing was polled", () => {
    expect(stanceTrajectories([ada], [])).toEqual([
      { participant: ada, stances: [], unread: [], moved: false },
    ]);
  });

  it("counts a return to the starting stance as movement", () => {
    const history = [
      poll(0, { p1: "pro" }),
      poll(1, { p1: "con" }),
      poll(2, { p1: "pro" }),
    ];
    expect(stanceTrajectories([ada], history)[0].moved).toBe(true);
  });
});

describe("tuningSummary", () => {
  it("is empty when every setting is at its default", () => {
    expect(tuningSummary(DEFAULT_TUNING)).toBe("");
    expect(tuningSummary(undefined)).toBe("");
  });

  it("names only the settings that deviate, in a stable order", () => {
    expect(tuningSummary({ ...DEFAULT_TUNING, temperature: 0.9 })).toBe("temp 0.9");
    expect(tuningSummary({ ...DEFAULT_TUNING, max_tokens: 1200 })).toBe("1200 tok");
    expect(tuningSummary({ ...DEFAULT_TUNING, persona: "an economist" })).toBe("persona");
    expect(tuningSummary({ ...DEFAULT_TUNING, instructions: "terse" })).toBe("instructions");
    expect(tuningSummary({ ...DEFAULT_TUNING, allow_reasoning: false })).toBe("no reasoning");
    expect(
      tuningSummary({
        temperature: 0.9,
        max_tokens: 1200,
        persona: "p",
        instructions: "i",
        allow_reasoning: true,
      }),
    ).toBe("temp 0.9 · 1200 tok · persona · instructions");
  });

  it("does not read a missing allow_reasoning as reasoning being off", () => {
    // `!undefined` is true, so a payload that simply omits the key would be
    // labelled "no reasoning" — claiming a setting the user never made. The
    // cast is the point: the current API always sends the field, so the type
    // says this cannot happen, but a stale cache or an older server can still
    // put it in front of this function and a wrong label is worse than none.
    const legacy = { temperature: 0.7, max_tokens: 2048, persona: "", instructions: "" };
    expect(tuningSummary(legacy as ParticipantTuning)).toBe("");
  });

  it("treats blank persona/instructions as unset", () => {
    expect(tuningSummary({ ...DEFAULT_TUNING, persona: "   ", instructions: "\t" })).toBe("");
  });

  it("reports a temperature of zero, which is a real deviation", () => {
    expect(tuningSummary({ ...DEFAULT_TUNING, temperature: 0 })).toBe("temp 0");
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
