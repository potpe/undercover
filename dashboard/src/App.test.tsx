import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "./App";
import type { ClaimSummary } from "./api";

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

function mockApi() {
  const instructions =
    "You are the UnderCover Agent.\nRead the policy wording.";
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = requestUrl(input);
    if (url === "/api/claims") return Promise.resolve(response(claims));
    if (url === "/api/agent/instructions") {
      return Promise.resolve(response({ content: instructions }));
    }
    return Promise.resolve(response({ detail: "Not found" }, 404));
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, instructions };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows the claims queue with money and every status label", async () => {
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

it("shows the agent instructions as read-only text", async () => {
  const { instructions } = mockApi();
  render(<App />);
  await screen.findByText("Maya Patel");

  screen.getByRole("button", { name: "Agent instructions" }).click();

  expect(
    await screen.findByText(
      (_, element) => element?.textContent === instructions,
    ),
  ).toBeDefined();
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(
    screen.getByRole("heading", { name: "Agent instructions" }),
  ).toBeDefined();
});

it("shows an instructions loading failure", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      if (requestUrl(input) === "/api/claims")
        return Promise.resolve(response(claims));
      return Promise.resolve(response({ detail: "Unavailable" }, 503));
    }),
  );
  render(<App />);
  await screen.findByText("Maya Patel");

  screen.getByRole("button", { name: "Agent instructions" }).click();

  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "We could not load the agent instructions.",
  );
});
