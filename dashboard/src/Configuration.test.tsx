import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Configuration } from "./Configuration";

const history = {
  current_version: 2,
  versions: [
    {
      version: 1,
      instructions: "Read the policy.",
      model_name: "openai/gpt-6-luna",
      reasoning_effort: "low",
      reason: "Initial configuration",
      proposed_by: "UnderCover",
      approved_by: null,
      proposed_at: "2026-11-11T12:00:00Z",
      approved_at: null,
      diff: [],
      held_out_scores: null,
    },
    {
      version: 2,
      instructions: "Read the policy carefully.",
      model_name: "openai/gpt-6-luna",
      reasoning_effort: "medium",
      reason: "Improve evidence review",
      proposed_by: "improver",
      approved_by: "HND-01",
      proposed_at: "2026-11-11T12:01:00Z",
      approved_at: "2026-11-11T12:02:00Z",
      diff: [
        { kind: "removed", text: "Read the policy." },
        { kind: "added", text: "Read the policy carefully." },
      ],
      held_out_scores: {
        accuracy: 0.8,
        grade: 1.5,
        cost_per_claim: 0.01,
        seconds_per_claim: 2.5,
      },
    },
  ],
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows the current version, held-out scores and a readable diff", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify(history), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );
  render(<Configuration />);

  expect(await screen.findByText("Version 2")).toBeDefined();
  expect(screen.getByText("Current version: 2")).toBeDefined();
  expect(screen.getByText("Improve evidence review")).toBeDefined();
  expect(screen.getByText("80%")).toBeDefined();
  expect(screen.getByText("1.50 / 3")).toBeDefined();
  expect(screen.getByText("Read the policy carefully.")).toBeDefined();
  expect(
    screen.getByText("Version 1 has no earlier version to compare."),
  ).toBeDefined();
});

it("shows an actionable loading failure", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
  render(<Configuration />);
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "We could not load configuration versions. Try again.",
  );
});
