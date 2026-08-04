import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ChamberDetail } from "./ChamberDetail";
import { DEFAULT_TUNING } from "./types";
import type { Chamber } from "./types";

vi.mock("./useDebateStream", () => ({
  useDebateStream: () => ({ liveTurns: [], status: null, consensus: null, done: false }),
}));

const getChamber = vi.fn();
const setParticipantMuted = vi.fn();

// Partial mock: only the calls this test drives are stubbed, so the component
// keeps using the real module for everything else and the test does not have to
// track its full surface.
vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()),
  getChamber: (...a: unknown[]) => getChamber(...a),
  setParticipantMuted: (...a: unknown[]) => setParticipantMuted(...a),
  getServerConfig: () => Promise.resolve({ web_access_enabled: false }),
  getMetrics: () => Promise.resolve([]),
  listModels: () => Promise.resolve([]),
}));

function participant(name: string, muted = false) {
  return {
    id: `id-${name}`,
    display_name: name,
    provider: "mock" as const,
    model: "mock-small",
    stance: "neutral" as const,
    tuning: DEFAULT_TUNING,
    muted,
  };
}

function chamber(status: Chamber["status"], muted: string[] = []): Chamber {
  return {
    id: "c1",
    topic: "office temperature should be kept under 26C",
    category: "",
    description: "",
    status,
    settings: {
      max_rounds: 8,
      max_total_tokens: 200000,
      max_duration_seconds: null,
      min_rounds: 1,
      decision_rule: "judge",
      convergence_rounds: 2,
      web_evidence: false,
      stop_on_repetition: true,
      repetition_threshold: 0.95,
    },
    config: {},
    participants: ["Alice", "Bob", "Eve"].map((n) => participant(n, muted.includes(n))),
    turns: [],
    stance_history: [],
    consensus: null,
    created_at: "2026-08-04T09:15:55Z",
  } as unknown as Chamber;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("muting a debater from the roster", () => {
  it("shows the muted badge and offers Unmute after muting a draft chamber", async () => {
    const user = userEvent.setup();
    // The server applies it immediately off-run, so the refetch returns it muted.
    getChamber
      .mockResolvedValueOnce(chamber("draft"))
      .mockResolvedValue(chamber("draft", ["Alice"]));
    setParticipantMuted.mockResolvedValue({ status: "muted" });

    render(<ChamberDetail chamberId="c1" onBack={vi.fn()} />);
    await screen.findByRole("button", { name: "mute Alice" });

    await user.click(screen.getByRole("button", { name: "mute Alice" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "unmute Alice" })).toBeInTheDocument(),
    );
    expect(screen.getByText(/muted — not counted in the vote/)).toBeInTheDocument();
  });

  it("keeps Unmute reachable once the debate has concluded", async () => {
    // A debater muted mid-debate is still muted when it ends. If the control
    // disappears with the run, that state can never be undone from the UI.
    getChamber.mockResolvedValue(chamber("concluded", ["Alice"]));

    render(<ChamberDetail chamberId="c1" onBack={vi.fn()} />);
    await screen.findByText("Alice");

    expect(screen.getByRole("button", { name: "unmute Alice" })).toBeInTheDocument();
  });

  it("marks a queued mute on the row it applies to, not only at the top", async () => {
    // Mid-run the server queues the change to the next round boundary and the
    // roster is NOT refetched, so the row is the only place a user can learn
    // that their click did anything.
    const user = userEvent.setup();
    getChamber.mockResolvedValue(chamber("running"));
    setParticipantMuted.mockResolvedValue({ status: "queued" });

    render(<ChamberDetail chamberId="c1" onBack={vi.fn()} />);
    await screen.findByRole("button", { name: "mute Alice" });

    await user.click(screen.getByRole("button", { name: "mute Alice" }));

    await waitFor(() => expect(screen.getByText(/muting at next round/i)).toBeInTheDocument());
  });
});
