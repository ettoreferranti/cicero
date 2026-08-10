import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ChamberDetail } from "./ChamberDetail";
import { DEFAULT_TUNING } from "./types";
import type { Chamber, Moderator } from "./types";

vi.mock("./useDebateStream", () => ({
  useDebateStream: () => ({ liveTurns: [], status: null, consensus: null, done: false }),
}));

const getChamber = vi.fn();
const updateModerator = vi.fn();

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()),
  getChamber: (...a: unknown[]) => getChamber(...a),
  updateModerator: (...a: unknown[]) => updateModerator(...a),
  getServerConfig: () => Promise.resolve({ web_access_enabled: false }),
  getMetrics: () => Promise.resolve([]),
  listModels: () => Promise.resolve([]),
}));

function participant(name: string, model: string) {
  return {
    id: `id-${name}`,
    display_name: name,
    provider: "ollama" as const,
    model,
    stance: "neutral" as const,
    tuning: DEFAULT_TUNING,
    muted: false,
  };
}

function chamber(moderator: Moderator | null, status: Chamber["status"] = "draft"): Chamber {
  return {
    id: "c1",
    topic: "Should we teach programming?",
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
      measure_compliance: false,
      stop_on_repetition: true,
      repetition_threshold: 0.95,
    },
    config: {},
    participants: [participant("Alice", "qwen3:30b"), participant("Bob", "command-r:latest")],
    turns: [],
    stance_history: [],
    consensus: null,
    moderator,
    created_at: "2026-08-06T09:15:55Z",
  };
}

/** The Moderator card only — the model string also appears in the roster and the
 *  select, so an unscoped query matches three elements and proves nothing about
 *  where it rendered. */
async function moderatorCard(): Promise<HTMLElement> {
  const heading = await screen.findByRole("heading", { name: /Moderator/ });
  return heading.closest(".card") as HTMLElement;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("moderator", () => {
  it("names the first debater as the fallback arbiter when none is set", async () => {
    getChamber.mockResolvedValue(chamber(null));
    render(<ChamberDetail chamberId="c1" onBack={vi.fn()} />);

    // The whole point of surfacing this: a debater silently judging its own
    // debate should be visible rather than inferred from roster order.
    const card = await moderatorCard();
    expect(within(card).getByText(/Defaults to the first debater/)).toBeInTheDocument();
    // selector: the same string is also a <option> in the change control below,
    // so match the rendered name rather than any occurrence.
    expect(within(card).getByText("ollama/qwen3:30b", { selector: "strong" })).toBeInTheDocument();
  });

  it("shows the configured moderator and its budget once set", async () => {
    getChamber.mockResolvedValue(
      chamber({
        provider: "ollama",
        model: "command-r:latest",
        max_tokens: 4096,
        temperature: 0.3,
      }),
    );
    render(<ChamberDetail chamberId="c1" onBack={vi.fn()} />);

    const card = await moderatorCard();
    expect(
      within(card).getByText("ollama/command-r:latest", { selector: "strong" }),
    ).toBeInTheDocument();
    // The budget is on screen because 2048 is what made the judge fail to name a
    // winner on 5 of 9 measured calls — it is not incidental tuning.
    expect(within(card).getByText(/4096 tok/)).toBeInTheDocument();
    expect(within(card).queryByText(/Defaults to the first debater/)).not.toBeInTheDocument();
  });

  it("sets the moderator from a debater's model while the chamber is a draft", async () => {
    const user = userEvent.setup();
    getChamber.mockResolvedValue(chamber(null));
    updateModerator.mockResolvedValue(
      chamber({
        provider: "ollama",
        model: "command-r:latest",
        max_tokens: 4096,
        temperature: 0.3,
      }),
    );
    render(<ChamberDetail chamberId="c1" onBack={vi.fn()} />);

    const select = await screen.findByLabelText("moderator model");
    await user.selectOptions(select, "ollama/command-r:latest");
    await user.click(screen.getByRole("button", { name: "Set moderator" }));

    expect(updateModerator).toHaveBeenCalledWith("c1", {
      provider: "ollama",
      model: "command-r:latest",
    });
  });

  it("offers no moderator control once the debate has run", async () => {
    getChamber.mockResolvedValue(chamber(null, "concluded"));
    render(<ChamberDetail chamberId="c1" onBack={vi.fn()} />);

    // Frozen with the roster: who judged is part of the record of a finished run.
    await screen.findByText(/Defaults to the first debater/);
    expect(screen.queryByLabelText("moderator model")).not.toBeInTheDocument();
  });
});
