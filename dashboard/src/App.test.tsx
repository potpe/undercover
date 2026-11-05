import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "./App";
import type { AgentRun, ClaimSummary } from "./api";

const claims: ClaimSummary[] = [
  {
    claim_id: "CLM-1001",
    customer_name: "Maya Patel",
    claim_type: "trip_delay",
    summary: "Flight home canceled by the airline",
    amount_claimed: 420,
    status: "waiting",
    decision: null,
    payout: null,
  },
  {
    claim_id: "CLM-1002",
    customer_name: "Tom Becker",
    claim_type: "baggage",
    summary: "Suitcase lost on a connecting flight",
    amount_claimed: 1250,
    status: "in_progress",
    decision: null,
    payout: null,
  },
  {
    claim_id: "CLM-1003",
    customer_name: "Aiko Tanaka",
    claim_type: "emergency_medical",
    summary: "Hospital visit after a fall",
    amount_claimed: 3800,
    status: "waiting_for_sign_off",
    decision: "approve",
    payout: 3750,
  },
  {
    claim_id: "CLM-1004",
    customer_name: "Danielle Okafor",
    claim_type: "baggage_delay",
    summary: "Checked bag arrived late",
    amount_claimed: 186,
    status: "decided",
    decision: "approve",
    payout: 186,
  },
];

const completedRun: AgentRun = {
  run_id: 1,
  thread_id: "thread",
  claim_cap: 4,
  target_claim_id: null,
  model_name: "deepseek/deepseek-v4.1-flash",
  status: "completed",
  summary: "4 claims handled: 3 done, 1 left for handler.",
  created_at: "2026-11-05T12:00:00Z",
  completed_at: "2026-11-05T12:01:00Z",
  todos: [
    {
      claim_id: "CLM-1001",
      position: 1,
      status: "to_do",
      attempts: 0,
      result: null,
      error: null,
    },
    {
      claim_id: "CLM-1002",
      position: 2,
      status: "in_progress",
      attempts: 1,
      result: null,
      error: null,
    },
    {
      claim_id: "CLM-1003",
      position: 3,
      status: "done",
      attempts: 0,
      result: "Approve $3,750.00",
      error: null,
    },
    {
      claim_id: "CLM-1004",
      position: 4,
      status: "left_for_handler",
      attempts: 2,
      result: "Provider failed",
      error: "Provider failed",
    },
  ],
};

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  return input instanceof URL ? input.href : input.url;
}

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockApi(currentRun: AgentRun | null = null) {
  const queue = "Plan the queue and keep the to-do list current.";
  const claim = "Read the policy wording and claims manual.";
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = requestUrl(input);
    if (url === "/api/claims") return Promise.resolve(response(claims));
    if (url === "/api/runs/current")
      return Promise.resolve(response(currentRun));
    if (url === "/api/agent/instructions") {
      return Promise.resolve(response({ queue, claim }));
    }
    return Promise.resolve(response({ detail: "Not found" }, 404));
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, queue, claim };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows the claims queue with money and every existing status label", async () => {
  mockApi();
  render(<App />);

  expect(
    await screen.findByRole("heading", { name: "Claims queue" }),
  ).toBeDefined();
  expect(screen.getByText("Maya Patel")).toBeDefined();
  expect(
    screen.getByText("Suitcase lost on a connecting flight"),
  ).toBeDefined();
  expect(screen.getByText("$1,250.00")).toBeDefined();
  expect(screen.getByText("Waiting", { selector: ".status" })).toBeDefined();
  expect(screen.getByText("In progress")).toBeDefined();
  expect(screen.getByText("Waiting for sign-off")).toBeDefined();
  expect(screen.getByText("Decided")).toBeDefined();
});

it("does not refetch the queue on an unrelated rerender", async () => {
  const { fetchMock } = mockApi();
  const { rerender } = render(<App />);
  await screen.findByText("Maya Patel");

  rerender(<App />);

  expect(
    fetchMock.mock.calls.filter(
      ([input]) => requestUrl(input) === "/api/claims",
    ),
  ).toHaveLength(1);
});

it("shows a queue loading failure", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
  render(<App />);

  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "We could not load the claims queue. Check that the backend is running.",
  );
});

it("shows both agent instruction sets as read-only text", async () => {
  const { queue, claim } = mockApi();
  render(<App />);
  await screen.findByText("Maya Patel");

  fireEvent.click(screen.getByRole("button", { name: "Agent instructions" }));

  expect(await screen.findByText(queue)).toBeDefined();
  expect(screen.getByText(claim)).toBeDefined();
  expect(screen.getByRole("heading", { name: "Queue agent" })).toBeDefined();
  expect(screen.getByRole("heading", { name: "Claim agent" })).toBeDefined();
  expect(screen.queryByRole("textbox")).toBeNull();
});

it("shows an instructions loading failure", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === "/api/claims") return Promise.resolve(response(claims));
      if (url === "/api/runs/current") return Promise.resolve(response(null));
      return Promise.resolve(response({ detail: "Unavailable" }, 503));
    }),
  );
  render(<App />);
  await screen.findByText("Maya Patel");

  fireEvent.click(screen.getByRole("button", { name: "Agent instructions" }));

  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "We could not load the agent instructions.",
  );
});

it("shows the current run, model, summary and all to-do states", async () => {
  mockApi(completedRun);
  render(<App />);

  expect(
    await screen.findByText("4 claims handled: 3 done, 1 left for handler."),
  ).toBeDefined();
  expect(screen.getByText("deepseek/deepseek-v4.1-flash")).toBeDefined();
  expect(screen.getByText("To do", { selector: ".todo-status" })).toBeDefined();
  expect(
    screen.getByText("In progress", { selector: ".todo-status" }),
  ).toBeDefined();
  expect(screen.getByText("Done", { selector: ".todo-status" })).toBeDefined();
  expect(
    screen.getByText("Left for handler", { selector: ".todo-status" }),
  ).toBeDefined();
  expect(screen.getByText("Provider failed")).toBeDefined();
});

it("validates the claim cap", async () => {
  mockApi();
  render(<App />);
  await screen.findByText("Maya Patel");

  fireEvent.change(screen.getByLabelText("Claim cap"), {
    target: { value: "0" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Run queue" }));

  expect(screen.getByRole("alert")).toHaveProperty(
    "textContent",
    "Claim cap must be a whole number from 1 to 25.",
  );
});

it("starts one capped run and stops polling after completion", async () => {
  const running = {
    ...completedRun,
    status: "in_progress" as const,
    summary: null,
    completed_at: null,
  };
  let started = false;
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = requestUrl(input);
    if (url === "/api/claims") return Promise.resolve(response(claims));
    if (url === "/api/runs" && init?.method === "POST") {
      started = true;
      return Promise.resolve(response(running, 202));
    }
    if (url === "/api/runs/current") {
      return Promise.resolve(response(started ? completedRun : null));
    }
    return Promise.resolve(response({ detail: "Not found" }, 404));
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);
  await screen.findByText("Maya Patel");

  fireEvent.click(screen.getByRole("button", { name: "Run queue" }));

  expect(
    await screen.findByText("4 claims handled: 3 done, 1 left for handler."),
  ).toBeDefined();
  const post = fetchMock.mock.calls.find(
    ([input]) => requestUrl(input) === "/api/runs",
  );
  const body = post?.[1]?.body;
  if (typeof body !== "string") throw new Error("Missing run request body");
  const payload = JSON.parse(body) as {
    thread_id: unknown;
    claim_cap: unknown;
  };
  expect(typeof payload.thread_id).toBe("string");
  expect(payload.claim_cap).toBe(5);
  await waitFor(() => {
    expect(
      screen.getByText("4 claims handled: 3 done, 1 left for handler."),
    ).toBeDefined();
  });
  const reads = fetchMock.mock.calls.filter(
    ([input]) => requestUrl(input) === "/api/runs/current",
  ).length;
  await new Promise((resolve) => setTimeout(resolve, 600));
  expect(
    fetchMock.mock.calls.filter(
      ([input]) => requestUrl(input) === "/api/runs/current",
    ),
  ).toHaveLength(reads);
}, 5000);
