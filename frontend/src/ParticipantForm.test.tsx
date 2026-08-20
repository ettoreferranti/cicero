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

  it("offers an instructions box that edits the field the engine reads (D7)", async () => {
    // Its predecessor, `style`, was collected here and never sent to a model.
    const user = userEvent.setup();
    const onSubmit = vi.fn(() => Promise.resolve());
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
        onSubmit={onSubmit}
      />,
    );

    await user.type(screen.getByLabelText("instructions"), "speak in rhyme");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        tuning: expect.objectContaining({ instructions: "speak in rhyme" }),
      }),
    );
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

/**
 * Reasoning is charged against the same budget as the turn.
 *
 * Ollama counts a thinking model's reasoning tokens against `num_predict` but
 * returns them in a separate field, so `max_tokens` is shared between invisible
 * narration and the speech. Measured on muse-glimmer:30b-mlx at 1400 tokens,
 * three samples returned 0-2635 characters of content, every one cut
 * mid-sentence.
 *
 * The control has to live here, not only in the API: a PATCH replaces the whole
 * tuning block, so a form that does not know the field would silently reset it
 * to `true` the next time anyone edited the debater.
 */
describe("reasoning control", () => {
  it("submits the setting the engine reads", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn(() => Promise.resolve());
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
        onSubmit={onSubmit}
      />,
    );

    await user.click(screen.getByLabelText(/let this model think/i));
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        tuning: expect.objectContaining({ allow_reasoning: false }),
      }),
    );
  });

  it("is on by default, so an untouched debater behaves as before", () => {
    render(<ParticipantForm formLabel="add participant" submitLabel="Add" onSubmit={vi.fn()} />);
    expect(screen.getByLabelText(/let this model think/i)).toBeChecked();
  });

  it("preserves an existing false rather than resetting it on edit", () => {
    render(
      <ParticipantForm
        formLabel="edit Ada"
        submitLabel="Save"
        initial={{
          display_name: "Ada",
          provider: "mock",
          model: "mock-small",
          stance: "pro",
          tuning: { ...DEFAULT_TUNING, allow_reasoning: false },
        }}
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.getByLabelText(/let this model think/i)).not.toBeChecked();
  });

  it("marks a debater with reasoning off in the collapsed summary", () => {
    render(
      <ParticipantForm
        formLabel="edit Ada"
        submitLabel="Save"
        initial={{
          display_name: "Ada",
          provider: "mock",
          model: "mock-small",
          stance: "pro",
          tuning: { ...DEFAULT_TUNING, allow_reasoning: false },
        }}
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.getByText(/Tuning for Ada — no reasoning/)).toBeInTheDocument();
  });
});
