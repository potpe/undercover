import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Session } from "@supabase/supabase-js";
import { App } from "./App";
import type { ClaimSummary, DispatchRun, Handler } from "./api";

const auth = vi.hoisted(() => ({
  getSession: vi.fn(),
  onAuthStateChange: vi.fn(),
  signInWithPassword: vi.fn(),
  signOut: vi.fn(),
}));

vi.mock("./supabase", () => ({
  supabase: { auth },
}));

const session = {
  access_token: "dana-token",
  user: { email: "dana.whitfield@undercover.example" },
} as unknown as Session;

const currentHandler: Handler = {
  handler_id: "HND-01",
  name: "Dana Whitfield",
  role: "Senior Claims Handler",
  email: "dana.whitfield@undercover.example",
  can_approve_over_limit: true,
};

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

const completedRun: DispatchRun = {
  run_id: 1,
  kind: "dispatch",
  concurrency: 3,
  model_name: "openai/gpt-6-luna",
  reasoning_effort: "low",
  status: "completed",
  evaluation_set: null,
  tiny: null,
  summary: {
    approved: 2,
    declined: 1,
    referred: 1,
    waiting_for_sign_off: [{ claim_id: "CLM-1003", payout: 3750 }],
    left_for_handler: ["CLM-1004"],
    in_progress: ["CLM-1002"],
    error: null,
  },
  created_at: "2026-11-05T12:00:00Z",
  completed_at: "2026-11-05T12:01:00Z",
  stopped_at: null,
  model_cost: 0.125,
  prompt_tokens: 1000,
  completion_tokens: 100,
  total_tokens: 1100,
  active_seconds: 12.5,
  wall_seconds: 30.25,
  claims: [
    {
      claim_id: "CLM-1001",
      position: 1,
      status: "waiting",
      attempts: 0,
      result: null,
      error: null,
      started_at: null,
      finished_at: null,
      model_cost: 0,
      active_seconds: 0,
    },
    {
      claim_id: "CLM-1002",
      position: 2,
      status: "in_progress",
      attempts: 1,
      result: null,
      error: "Timed out",
      started_at: "2026-11-05T12:00:00Z",
      finished_at: null,
      model_cost: 0.025,
      active_seconds: 5,
    },
    {
      claim_id: "CLM-1003",
      position: 3,
      status: "done",
      attempts: 0,
      result: "Approve $3,750.00",
      error: null,
      started_at: "2026-11-05T12:00:00Z",
      finished_at: "2026-11-05T12:00:10Z",
      model_cost: 0.05,
      active_seconds: 10,
    },
    {
      claim_id: "CLM-1004",
      position: 4,
      status: "left_for_handler",
      attempts: 2,
      result: "Provider failed",
      error: "Provider failed",
      started_at: "2026-11-05T12:00:00Z",
      finished_at: "2026-11-05T12:00:20Z",
      model_cost: 0.05,
      active_seconds: 20,
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

function mockApi(currentRun: DispatchRun | null = null) {
  const claim = "Read the policy wording and claims manual.";
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = requestUrl(input);
    if (url === "/api/claims") return Promise.resolve(response(claims));
    if (url === "/api/dispatches/current")
      return Promise.resolve(response(currentRun));
    if (url === "/api/me") return Promise.resolve(response(currentHandler));
    if (url === "/api/agent/instructions")
      return Promise.resolve(response({ claim }));
    return Promise.resolve(response({ detail: "Not found" }, 404));
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, claim };
}

beforeEach(() => {
  auth.getSession.mockResolvedValue({ data: { session } });
  auth.onAuthStateChange.mockReturnValue({
    data: { subscription: { unsubscribe: vi.fn() } },
  });
  auth.signOut.mockResolvedValue({ error: null });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
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
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      if (requestUrl(input) === "/api/me")
        return Promise.resolve(response(currentHandler));
      return Promise.reject(new Error("offline"));
    }),
  );
  render(<App />);

  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "We could not load the claims queue. Check that the backend is running.",
  );
});

it("shows only the claim agent instructions as read-only text", async () => {
  const { claim } = mockApi();
  render(<App />);
  await screen.findByText("Maya Patel");

  fireEvent.click(screen.getByRole("button", { name: "Agent instructions" }));

  expect(await screen.findByText(claim)).toBeDefined();
  expect(screen.getByRole("heading", { name: "Claim agent" })).toBeDefined();
  expect(screen.queryByRole("heading", { name: "Queue agent" })).toBeNull();
  expect(screen.queryByRole("textbox")).toBeNull();
});

it("shows an instructions loading failure", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === "/api/me") return Promise.resolve(response(currentHandler));
      if (url === "/api/claims") return Promise.resolve(response(claims));
      if (url === "/api/dispatches/current")
        return Promise.resolve(response(null));
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

it("shows dispatch states, record-built summary, model and usage totals", async () => {
  mockApi(completedRun);
  render(<App />);

  expect(
    await screen.findByText("2 approved, 1 declined, 1 referred."),
  ).toBeDefined();
  expect(screen.getByText("CLM-1003: $3,750.00")).toBeDefined();
  expect(
    screen.getByText("Still in progress for the next dispatch"),
  ).toBeDefined();
  expect(screen.getByText("openai/gpt-6-luna")).toBeDefined();
  expect(screen.getByText("Reasoning: low")).toBeDefined();
  expect(screen.getByText("Model cost: $0.125")).toBeDefined();
  expect(screen.getByText("Active work: 12.5 s")).toBeDefined();
  expect(screen.getByText("Run time: 30.3 s")).toBeDefined();
  expect(
    screen.getByText("Waiting", { selector: ".dispatch-status" }),
  ).toBeDefined();
  expect(
    screen.getByText("In progress", { selector: ".dispatch-status" }),
  ).toBeDefined();
  expect(
    screen.getByText("Done", { selector: ".dispatch-status" }),
  ).toBeDefined();
  expect(
    screen.getByText("Left for handler", { selector: ".dispatch-status" }),
  ).toBeDefined();
  expect(screen.getByText("Provider failed")).toBeDefined();
});

it("validates the agents-at-once boundaries", async () => {
  mockApi();
  render(<App />);
  await screen.findByText("Maya Patel");
  const input = screen.getByLabelText("Agents at once");

  for (const value of ["0", "26", "1.5"]) {
    fireEvent.change(input, { target: { value } });
    fireEvent.click(screen.getByRole("button", { name: "Start dispatch" }));
    expect(screen.getByRole("alert")).toHaveProperty(
      "textContent",
      "Agents at once must be a whole number from 1 to 25.",
    );
  }
});

it("starts one dispatch with N and stops polling after completion", async () => {
  const running = {
    ...completedRun,
    status: "in_progress" as const,
    summary: null,
    completed_at: null,
  };
  let started = false;
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = requestUrl(input);
    if (url === "/api/me") return Promise.resolve(response(currentHandler));
    if (url === "/api/claims") return Promise.resolve(response(claims));
    if (url === "/api/dispatches" && init?.method === "POST") {
      started = true;
      return Promise.resolve(response(running, 202));
    }
    if (url === "/api/dispatches/current")
      return Promise.resolve(response(started ? completedRun : null));
    return Promise.resolve(response({ detail: "Not found" }, 404));
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);
  await screen.findByText("Maya Patel");

  fireEvent.change(screen.getByLabelText("Agents at once"), {
    target: { value: "5" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Start dispatch" }));

  expect(
    await screen.findByText("2 approved, 1 declined, 1 referred."),
  ).toBeDefined();
  const post = fetchMock.mock.calls.find(
    ([input]) => requestUrl(input) === "/api/dispatches",
  );
  const body = post?.[1]?.body;
  if (typeof body !== "string")
    throw new Error("Missing dispatch request body");
  expect(JSON.parse(body) as { concurrency: unknown }).toEqual({
    concurrency: 5,
  });
  expect(new Headers(post?.[1]?.headers).get("Authorization")).toBe(
    "Bearer dana-token",
  );
  await waitFor(() => {
    expect(
      screen.getByText("2 approved, 1 declined, 1 referred."),
    ).toBeDefined();
  });
  const reads = fetchMock.mock.calls.filter(
    ([input]) => requestUrl(input) === "/api/dispatches/current",
  ).length;
  await new Promise((resolve) => setTimeout(resolve, 600));
  expect(
    fetchMock.mock.calls.filter(
      ([input]) => requestUrl(input) === "/api/dispatches/current",
    ),
  ).toHaveLength(reads);
}, 5000);

it("shows an offline agent service without creating a dispatch", async () => {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = requestUrl(input);
    if (url === "/api/me") return Promise.resolve(response(currentHandler));
    if (url === "/api/claims") return Promise.resolve(response(claims));
    if (url === "/api/dispatches/current")
      return Promise.resolve(response(null));
    if (url === "/api/dispatches" && init?.method === "POST")
      return Promise.reject(new Error("The agent service is not running"));
    return Promise.resolve(response({ detail: "Not found" }, 404));
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);
  await screen.findByText("Maya Patel");

  fireEvent.click(screen.getByRole("button", { name: "Start dispatch" }));

  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "The agent service is not running",
  );
  expect(screen.queryByText("Dispatch 1")).toBeNull();
});

it("shows a second-start conflict", async () => {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = requestUrl(input);
    if (url === "/api/me") return Promise.resolve(response(currentHandler));
    if (url === "/api/claims") return Promise.resolve(response(claims));
    if (url === "/api/dispatches/current")
      return Promise.resolve(response(null));
    if (url === "/api/dispatches" && init?.method === "POST")
      return Promise.resolve(
        response({ detail: "Another dispatch or evaluation is running" }, 409),
      );
    return Promise.resolve(response({ detail: "Not found" }, 404));
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);
  await screen.findByText("Maya Patel");

  fireEvent.click(screen.getByRole("button", { name: "Start dispatch" }));

  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "Another dispatch or evaluation is running",
  );
});

it("disables starting while a dispatch is active", async () => {
  mockApi({ ...completedRun, status: "in_progress" });
  render(<App />);

  expect(
    await screen.findByRole("button", { name: "Start dispatch" }),
  ).toHaveProperty("disabled", true);
});

it("signs in with email and password without offering sign-up", async () => {
  let listener: ((event: string, value: Session | null) => void) | undefined;
  auth.getSession.mockResolvedValue({ data: { session: null } });
  auth.onAuthStateChange.mockImplementation((callback) => {
    listener = callback as typeof listener;
    return { data: { subscription: { unsubscribe: vi.fn() } } };
  });
  auth.signInWithPassword.mockImplementation(() => {
    listener?.("SIGNED_IN", session);
    return Promise.resolve({ data: { session }, error: null });
  });
  mockApi();
  render(<App />);

  expect(
    await screen.findByRole("heading", { name: "Sign in to UnderCover" }),
  ).toBeDefined();
  expect(screen.queryByRole("button", { name: "Sign up" })).toBeNull();
  fireEvent.change(screen.getByLabelText("Email address"), {
    target: { value: "dana.whitfield@undercover.example" },
  });
  fireEvent.change(screen.getByLabelText("Password"), {
    target: { value: "not-a-real-password" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

  expect(await screen.findByText("Maya Patel")).toBeDefined();
  expect(auth.signInWithPassword).toHaveBeenCalledWith({
    email: "dana.whitfield@undercover.example",
    password: "not-a-real-password",
  });
  expect(screen.getByText("Dana Whitfield")).toBeDefined();
});

it("signs out and returns to the sign-in page", async () => {
  let listener: ((event: string, value: Session | null) => void) | undefined;
  auth.onAuthStateChange.mockImplementation((callback) => {
    listener = callback as typeof listener;
    return { data: { subscription: { unsubscribe: vi.fn() } } };
  });
  auth.signOut.mockImplementation(() => {
    listener?.("SIGNED_OUT", null);
    return Promise.resolve({ error: null });
  });
  mockApi();
  render(<App />);
  await screen.findByText("Maya Patel");

  fireEvent.click(screen.getByRole("button", { name: "Sign out" }));

  expect(
    await screen.findByRole("heading", { name: "Sign in to UnderCover" }),
  ).toBeDefined();
});

it("shows a sign-in failure without loading protected pages", async () => {
  auth.getSession.mockResolvedValue({ data: { session: null } });
  auth.signInWithPassword.mockResolvedValue({
    data: { session: null },
    error: new Error("Invalid email or password"),
  });
  render(<App />);

  fireEvent.change(await screen.findByLabelText("Email address"), {
    target: { value: "dana.whitfield@undercover.example" },
  });
  fireEvent.change(screen.getByLabelText("Password"), {
    target: { value: "wrong" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "Invalid email or password",
  );
  expect(screen.queryByRole("navigation")).toBeNull();
});
