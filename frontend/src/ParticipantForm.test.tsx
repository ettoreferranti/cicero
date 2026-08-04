import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ParticipantForm } from "./ParticipantForm";
import { DEFAULT_TUNING } from "./types";

// The form probes the provider for its model list on mount; these tests are
// about scope labelling, so keep that lookup out of the way.
vi.mock("./api", () => ({
  listModels: vi.fn(() => Promise.resolve([])),
}));

beforeEach(() => {
  vi.clearAllMocks();
});

/**
 * Every control in this form configures exactly ONE debater, while the
 * "Debate settings" card next to it configures the whole chamber. The two
 * looked identical, and a bare "Tuning" heading inside a collapsed panel read
 * as chamber-wide. These tests pin the cues that say otherwise.
 */
describe("per-debater scope cues", () => {
  it("names the debater in the tuning panel when editing", () => {
    render(
      <ParticipantForm
        formLabel="edit Ada"
        submitLabel="Save"
        initial={{
          display_name: "Ada",
          provider: "mock",
          model: "mock-small",
          stance: "pro",
          tuning: DEFAULT_TUNING,
        }}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByText(/Tuning for Ada/)).toBeInTheDocument();
  });

  it("falls back to 'this debater' while the name is still blank", () => {
    render(
      <ParticipantForm formLabel="add participant" submitLabel="Add" onSubmit={vi.fn()} />,
    );

    expect(screen.getByText(/Tuning for this debater/)).toBeInTheDocument();
  });

  it("follows the name as it is typed, so the scope is never stale", async () => {
    const user = userEvent.setup();
    render(
      <ParticipantForm formLabel="add participant" submitLabel="Add" onSubmit={vi.fn()} />,
    );

    await user.type(screen.getByLabelText("participant name"), "Grace");

    expect(screen.getByText(/Tuning for Grace/)).toBeInTheDocument();
    expect(screen.queryByText(/Tuning for this debater/)).not.toBeInTheDocument();
  });

  it("keeps the tuning summary alongside the debater's name", () => {
    render(
      <ParticipantForm
        formLabel="edit Ada"
        submitLabel="Save"
        initial={{
          display_name: "Ada",
          provider: "mock",
          model: "mock-small",
          stance: "pro",
          tuning: { ...DEFAULT_TUNING, temperature: 1.2 },
        }}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByText(/Tuning for Ada — temp 1\.2/)).toBeInTheDocument();
  });

  it("marks the form as scoped and tints it with the debater's accent", () => {
    render(
      <ParticipantForm
        formLabel="edit Ada"
        submitLabel="Save"
        accent="#f87171"
        initial={{
          display_name: "Ada",
          provider: "mock",
          model: "mock-small",
          stance: "con",
          tuning: DEFAULT_TUNING,
        }}
        onSubmit={vi.fn()}
      />,
    );

    const form = screen.getByRole("form", { name: "edit Ada" });
    expect(form).toHaveClass("scoped");
    // The same colour as this debater's roster dot and transcript turns.
    expect(form).toHaveStyle({ borderLeftColor: "#f87171" });
  });

  it("leaves the accent to the stylesheet when there is no debater yet", () => {
    render(
      <ParticipantForm formLabel="add participant" submitLabel="Add" onSubmit={vi.fn()} />,
    );

    const form = screen.getByRole("form", { name: "add participant" });
    expect(form).toHaveClass("scoped");
    expect(form.style.borderLeftColor).toBe("");
  });
});
