import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { EvaluationRun } from "./api";
import { Scoreboard } from "./Scoreboard";

function makeEvaluation(runId = 1): EvaluationRun {
  return {
    run_id: runId,
    kind: "evaluation",
    concurrency: 5,
    status: "completed",
    model_name: "openai/gpt-6-luna",
    reasoning_effort: "low",
    evaluation_set: "evaluation",
    tiny: true,
    summary: null,
    created_at: "2026-11-10T12:00:00Z",
    completed_at: "2026-11-10T12:01:00Z",
    stopped_at: null,
    model_cost: 0.1,
    prompt_tokens: 10,
    completion_tokens: 5,
    total_tokens: 15,
    active_seconds: 2,
    wall_seconds: 60,
    claims: [],
    measures: {
      decision_accuracy: 0.8,
      accuracy: 0.6,
      cost_of_mistakes: 1501,
      straight_through: 0.75,
      model_cost: 0.1,
      model_cost_per_claim: 0.02,
      seconds_per_claim: 0.4,
      slowest_claim: 2,
      seconds: 60,
    },
    claim_comparisons: [
      {
        claim_id: "EVAL-1-01",
        position: 1,
        status: "done",
        attempts: 0,
        result: "Done",
        error: null,
        started_at: "2026-11-10T12:00:00Z",
        finished_at: "2026-11-10T12:00:02Z",
        model_cost: 0.02,
        active_seconds: 2,
        source_claim_id: "CLM-1001",
        handler_decision: "approve",
        handler_payout: 420,
        agent_decision: "decline",
        agent_payout: 0,
        agent_reason: "The agent declined the claim.",
        decision_match: false,
        right: false,
        mistake_cost: 1500,
      },
    ],
  };
}

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  return input instanceof URL ? input.href : input.url;
}

function mockApi(evaluations: EvaluationRun[] = [makeEvaluation()]) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = requestUrl(input);
    if (url === "/api/evaluations" && init?.method === "POST")
      return Promise.resolve(response({}, 202));
    if (url === "/api/evaluations")
      return Promise.resolve(response(evaluations));
    const detail = /^\/api\/evaluations\/(\d+)$/.exec(url);
    if (detail)
      return Promise.resolve(
        response(evaluations[Number(detail[1]) - 1] ?? makeEvaluation(1)),
      );
    return Promise.resolve(response({ detail: "Not found" }, 404));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("starts an evaluation with N, set and tiny scope", async () => {
  const fetchMock = mockApi();
  render(<Scoreboard onSelectClaim={vi.fn()} />);
  await screen.findByText("Evaluation 1");
  expect(screen.getByLabelText("Agents at once")).toHaveProperty("value", "3");

  fireEvent.change(screen.getByLabelText("Evaluation set"), {
    target: { value: "held_out" },
  });
  fireEvent.click(screen.getByLabelText("Only the five tiny claims"));
  fireEvent.change(screen.getByLabelText("Agents at once"), {
    target: { value: "5" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Start evaluation" }));

  const post = fetchMock.mock.calls.find(
    ([input, init]) =>
      requestUrl(input) === "/api/evaluations" && init?.method === "POST",
  );
  const body = post?.[1]?.body;
  if (typeof body !== "string") throw new Error("Missing evaluation request");
  expect(JSON.parse(body)).toEqual({
    concurrency: 5,
    evaluation_set: "held_out",
    tiny: true,
  });
});

it("lists every measure and opens claim comparisons", async () => {
  const onSelect = vi.fn();
  mockApi([makeEvaluation(2), makeEvaluation(1)]);
  render(<Scoreboard onSelectClaim={onSelect} />);

  expect(await screen.findAllByText("Decision accuracy: 80%")).toHaveLength(2);
  expect(screen.getAllByText("Accuracy: 60%")).toHaveLength(2);
  expect(screen.getAllByText("Cost of mistakes: $1,501.00")).toHaveLength(2);
  expect(screen.getAllByText("Straight through: 75%")).toHaveLength(2);
  expect(screen.getAllByText("Model cost per claim: $0.02")).toHaveLength(2);
  expect(screen.getAllByText("Time per claim: 0.4 s")).toHaveLength(2);
  expect(screen.getAllByText("Whole run: 60.0 s")).toHaveLength(2);
  const headings = document.querySelectorAll(".evaluation-card h2");
  expect(headings[0].textContent).toBe("Evaluation 2");
  expect(headings[1].textContent).toBe("Evaluation 1");

  fireEvent.click(screen.getByRole("button", { name: "View evaluation 1" }));

  expect(await screen.findByText("approve: $420.00")).toBeDefined();
  expect(screen.getByText("decline: $0.00")).toBeDefined();
  expect(screen.getByText("The agent declined the claim.")).toBeDefined();
  expect(screen.getByText("Decision miss")).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "View claim EVAL-1-01" }));
  expect(onSelect).toHaveBeenCalledWith("EVAL-1-01");
});

it("shows stopped evaluations and their undecided referrals", async () => {
  const stopped = {
    ...makeEvaluation(),
    status: "stopped" as const,
    completed_at: null,
    stopped_at: "2026-11-10T12:01:00Z",
  };
  mockApi([stopped]);
  render(<Scoreboard onSelectClaim={vi.fn()} />);

  expect(await screen.findByText("stopped")).toBeDefined();
  expect(
    screen.getByText("Evaluation set, tiny; 5 agents at once"),
  ).toBeDefined();
});

it("disables a second start while an evaluation is active", async () => {
  mockApi([{ ...makeEvaluation(), status: "in_progress" }]);
  render(<Scoreboard onSelectClaim={vi.fn()} />);

  expect(
    await screen.findByRole("button", { name: "Start evaluation" }),
  ).toHaveProperty("disabled", true);
});

it("validates N before starting", async () => {
  const fetchMock = mockApi();
  render(<Scoreboard onSelectClaim={vi.fn()} />);
  await screen.findByText("Evaluation 1");
  fireEvent.change(screen.getByLabelText("Agents at once"), {
    target: { value: "0" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Start evaluation" }));

  expect(screen.getByRole("alert")).toHaveProperty(
    "textContent",
    "Agents at once must be a whole number from 1 to 25.",
  );
  expect(
    fetchMock.mock.calls.filter(([, init]) => init?.method === "POST"),
  ).toHaveLength(0);
});

it("shows a start conflict", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === "/api/evaluations" && init?.method === "POST")
        return Promise.resolve(
          response(
            { detail: "Another dispatch or evaluation is running" },
            409,
          ),
        );
      return Promise.resolve(response([]));
    }),
  );
  render(<Scoreboard onSelectClaim={vi.fn()} />);
  await screen.findByText("No evaluations yet.");

  fireEvent.click(screen.getByRole("button", { name: "Start evaluation" }));

  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "Another dispatch or evaluation is running",
  );
});

it("shows an unavailable agent service", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (requestUrl(input) === "/api/evaluations" && init?.method === "POST")
        return Promise.reject(new Error("The agent service is not running"));
      return Promise.resolve(response([]));
    }),
  );
  render(<Scoreboard onSelectClaim={vi.fn()} />);
  await screen.findByText("No evaluations yet.");

  fireEvent.click(screen.getByRole("button", { name: "Start evaluation" }));

  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "The agent service is not running",
  );
});
