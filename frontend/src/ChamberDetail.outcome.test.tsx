import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import { ChamberDetail } from "./ChamberDetail";
import { DEFAULT_TUNING } from "./types";
import type { Chamber, ConsensusResult, OutcomeSummary } from "./types";

vi.mock("./useDebateStream", () => ({
  useDebateStream: () => ({ liveTurns: [], status: null, consensus: null, done: false }),
}));

const getChamber = vi.fn();
const getOutcome = vi.fn();

// Partial mock: only the calls this test drives are stubbed, so the component
// keeps using the real module for everything else and the test does not have to
// track its full surface.
vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()),
  getChamber: (...a: unknown[]) => getChamber(...a),
  getOutcome: (...a: unknown[]) => getOutcome(...a),
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

function chamber(consensus: ConsensusResult | null): Chamber {
  return {
    id: "c1",
    topic: "office temperature should be kept under 26C",
    category: "",
    description: "",
    status: "concluded",
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
    participants: ["Alice", "Bob"].map((n) => participant(n)),
    turns: [],
    stance_history: [],
    consensus,
    created_at: "2026-08-04T09:15:55Z",
  } as unknown as Chamber;
}

function renderChamber({
  consensus,
  outcome,
}: {
  consensus: ConsensusResult;
  outcome: OutcomeSummary;
}) {
  getChamber.mockResolvedValue(chamber(consensus));
  getOutcome.mockResolvedValue(outcome);
  render(<ChamberDetail chamberId="c1" onBack={vi.fn()} />);
}

describe("outcome card", () => {
  it("leads with the headline and shows the derived facts", async () => {
    renderChamber({
      consensus: {
        outcome: "majority",
        statement: "The majority prevailed.",
        headline: "Mars should wait for cheaper launch costs.",
        winning_stance: "neutral",
        final_stances: {},
        unparsed: [],
      },
      outcome: {
        headline: "Mars should wait for cheaper launch costs.",
        support: "contested — 2 of 3 debaters settled on neutral, 1 dissent",
        decided_by: "majority of final positions",
        movements: ["Ada (pro→neutral)"],
      },
    });

    expect(
      await screen.findByText("Mars should wait for cheaper launch costs."),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/contested — 2 of 3 debaters settled on neutral/),
    ).toBeInTheDocument();
    expect(screen.getByText(/majority of final positions/)).toBeInTheDocument();
    expect(screen.getByText(/Ada \(pro→neutral\)/)).toBeInTheDocument();
    // The stance word is evidence in the support line, not the headline.
    expect(screen.queryByText("Winning position:")).not.toBeInTheDocument();
  });

  it("falls back to the outcome label when there is no headline", async () => {
    renderChamber({
      consensus: {
        outcome: "majority",
        statement: "The majority prevailed.",
        headline: "",
        winning_stance: "neutral",
        final_stances: {},
        unparsed: null,
      },
      outcome: {
        headline: "",
        support: "contested — 2 of 3 debaters settled on neutral, 1 dissent",
        decided_by: "majority of final positions",
        movements: [],
      },
    });

    expect(await screen.findByText(/Majority decision/)).toBeInTheDocument();
    expect(screen.getByText(/Winning position/)).toBeInTheDocument();
  });

  it("omits the movement line when nobody moved", async () => {
    renderChamber({
      consensus: {
        outcome: "consensus",
        statement: "Agreed.",
        headline: "Mars should wait.",
        winning_stance: "neutral",
        final_stances: {},
        unparsed: [],
      },
      outcome: {
        headline: "Mars should wait.",
        support: "unanimous — all 2 debaters",
        decided_by: "all debaters converged",
        movements: [],
      },
    });

    await screen.findByText("Mars should wait.");
    // Positive proof the derived-facts block did render — otherwise the absence
    // of "Positions moved" below is indistinguishable from the whole block
    // never rendering.
    expect(screen.getByText("unanimous — all 2 debaters")).toBeInTheDocument();
    expect(screen.getByText("all debaters converged")).toBeInTheDocument();
    expect(screen.queryByText(/Positions moved/)).not.toBeInTheDocument();
  });

  it("still shows the headline from consensus when the outcome fetch 404s", async () => {
    // The stream can announce consensus before the chamber (and its derived
    // outcome facts) are readable — getOutcome legitimately 404s in that
    // window, and the card must still render from `consensus` alone.
    getChamber.mockResolvedValue(
      chamber({
        outcome: "consensus",
        statement: "Agreed.",
        headline: "Mars should wait.",
        winning_stance: "neutral",
        final_stances: {},
        unparsed: [],
      }),
    );
    getOutcome.mockRejectedValue(new ApiError(404, "not found"));

    render(<ChamberDetail chamberId="c1" onBack={vi.fn()} />);

    expect(await screen.findByText("Mars should wait.")).toBeInTheDocument();
    expect(screen.queryByText(/failed to/i)).not.toBeInTheDocument();
  });
});
