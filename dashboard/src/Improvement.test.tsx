import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Improvement } from "./Improvement";
import * as api from "./improvement-api";
import { testHandler } from "./testHandler";
import type { ImprovementRound } from "./improvement-api";
import { roundProgress } from "./round-stage";

vi.mock("./improvement-api");

function round(): ImprovementRound {
  return {
    round_id: 1,
    thread_id: "improvement-stub",
    status: "open",
    stage: "configuration_approval",
    recipe_version: 1,
    starting_version: 1,
    candidate_version: null,
    held_out_before: null,
    held_out_after: null,
    verdict: null,
    verdict_reason: null,
    finish_reason: null,
    progress: 3,
    cost: 0.01,
    created_by: "HND-01",
    created_at: "2026-11-12T12:00:00Z",
    finished_at: null,
    stopped_at: null,
    settings: {
      approval_limit: 1000,
      objectives: { accuracy: 0.6, grade: 1 },
      noise: {
        accuracy: 0.1,
        grade: 0.15,
        cost_per_claim: 0.15,
        seconds_per_claim: 0.15,
      },
      tolerance: {
        accuracy: 0.1,
        grade: 0.15,
        cost_per_claim: 1,
        seconds_per_claim: 0.25,
      },
      spending_limit: 0.5,
      evaluation_concurrency: 25,
      models_allowed: [],
    },
    proposals: [
      {
        proposal_id: 1,
        round_id: 1,
        kind: "configuration",
        payload: {
          instructions: "Check all coverage",
          model: "openai/gpt-6-luna",
          reasoning_effort: "medium",
          reason: "Improve accuracy",
        },
        reason: "Improve accuracy",
        diff: [{ kind: "added", text: "Check all coverage" }],
        proposed_at: "2026-11-12T12:00:00Z",
        status: "pending",
        decided_by: null,
        decided_at: null,
        decision_reason: null,
      },
    ],
  };
}

beforeEach(() => {
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.setAttribute("open", "");
    },
  });
  vi.mocked(api.listRounds).mockResolvedValue([]);
  vi.mocked(api.getRecipes).mockResolvedValue({
    current_version: 1,
    versions: [],
  });
  vi.mocked(api.getConfiguration).mockResolvedValue({
    current_version: 1,
    versions: [],
  });
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

it("shows empty history and confirms starting a round", async () => {
  vi.mocked(api.startRound).mockResolvedValue(round());
  render(<Improvement handler={testHandler} />);
  expect(await screen.findByText("No rounds yet.")).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Start round" }));
  expect(api.startRound).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Confirm start" }));
  await waitFor(() => {
    expect(api.startRound).toHaveBeenCalledOnce();
  });
});

it("confirms approval and keeps the pending diff visible on a busy refusal", async () => {
  vi.mocked(api.listRounds).mockResolvedValue([round()]);
  vi.mocked(api.decideProposal).mockRejectedValue(
    new Error("dispatch 7 is running"),
  );
  render(<Improvement handler={testHandler} />);
  expect(await screen.findByText("+Check all coverage")).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Approve proposal" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm approval" }));
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "dispatch 7 is running",
  );
  expect(screen.getByText("+Check all coverage")).toBeDefined();
});

it("requires a rejection reason and sends it with the proposal", async () => {
  vi.mocked(api.listRounds).mockResolvedValue([round()]);
  vi.mocked(api.decideProposal).mockResolvedValue(round());
  render(<Improvement handler={testHandler} />);
  await screen.findByRole("button", { name: "Reject proposal" });
  fireEvent.change(screen.getByLabelText("Rejection reason"), {
    target: { value: "Use a general rule" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Reject proposal" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm rejection" }));
  await waitFor(() => {
    expect(api.decideProposal).toHaveBeenCalledWith(
      1,
      1,
      "reject",
      "Use a general rule",
    );
  });
});

it("explains senior-only controls without offering mutations", async () => {
  vi.mocked(api.listRounds).mockResolvedValue([round()]);
  render(
    <Improvement handler={{ ...testHandler, can_approve_over_limit: false }} />,
  );
  expect(await screen.findByRole("note")).toHaveProperty(
    "textContent",
    expect.stringContaining("start or stop rounds"),
  );
  expect(screen.queryByRole("button", { name: "Approve proposal" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Stop round" })).toBeNull();
});

it("shows stopped rounds and accessible held-out chart data", async () => {
  const stopped = {
    ...round(),
    status: "stopped" as const,
    stage: "stopped" as const,
    candidate_version: 2,
    held_out_after: {
      accuracy: 0.8,
      grade: 2,
      cost_per_claim: 0.01,
      seconds_per_claim: 5,
    },
    proposals: [],
  };
  vi.mocked(api.listRounds).mockResolvedValue([stopped]);
  render(<Improvement handler={testHandler} />);
  expect(
    await screen.findByRole("img", {
      name: "Held-out accuracy and grade by version",
    }),
  ).toBeDefined();
  expect(
    screen.getByRole("table", { name: "Held-out version scores" }),
  ).toBeDefined();
  expect(screen.getAllByText("Stopped").length).toBeGreaterThan(0);
});

it("shows a finished round with no candidate and its finish reason", async () => {
  vi.mocked(api.listRounds).mockResolvedValue([
    {
      ...round(),
      status: "finished",
      stage: "finished",
      candidate_version: null,
      finish_reason: "No proposal was made",
      proposals: [],
    },
  ]);
  render(<Improvement handler={testHandler} />);
  const heading = await screen.findByRole("heading", { name: "Round 1" });
  const roundPanel = heading.closest("section");
  expect(roundPanel?.textContent).toContain("No version tried");
  expect(roundPanel?.textContent).toContain("Finished: No proposal was made");
});

it("labels baseline and candidate evaluation progress", () => {
  const openRound = round();
  expect(
    roundProgress({ ...openRound, stage: "baseline", progress: 2 }),
  ).toContain("First evaluations · 2 of 3");
  expect(
    roundProgress({ ...openRound, stage: "evaluating", progress: 1 }),
  ).toContain("Candidate evaluations · 1 of 3");
});
