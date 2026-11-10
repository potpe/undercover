import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ClaimBundle, ClaimStatus, DecisionKind } from "./api";
import { ClaimWorkspace } from "./ClaimWorkspace";

const activity = [
  { id: 1, message: "Agent run started", created_at: "2026-11-03T12:00:00Z" },
];

function makeBundle(
  status: ClaimStatus = "waiting",
  decisionKind: DecisionKind | null = null,
  payout: number | null = null,
): ClaimBundle {
  return {
    claim: {
      claim_id: "CLM-1001",
      policy_id: "POL-2001",
      customer_id: "CUS-3001",
      claim_type: "trip_delay",
      submitted_on: "2026-06-24",
      incident_date: "2026-06-18",
      incident_location: "Rome, Italy",
      summary: "Flight home canceled by the airline",
      story:
        "My flight home from Rome was canceled. I booked a hotel and took taxis.",
      items: [
        {
          description: "Airport hotel, one night",
          category: "accommodation",
          amount: 245,
        },
        { description: "Dinner and breakfast", category: "meals", amount: 95 },
        { description: "Airport taxis", category: "transport", amount: 80 },
      ],
      amount_claimed: 420,
      documents: [
        {
          name: "Meridian Air cancellation notice",
          type: "carrier_report",
          content: "Flight MA 611 was canceled because of crew availability.",
        },
        {
          name: "Hotel receipt",
          type: "receipt",
          content: "Hotel Aurelia Fiumicino. Total paid: $245.00.",
        },
      ],
      status,
    },
    policy: {
      policy_id: "POL-2001",
      customer_id: "CUS-3001",
      plan: "premium",
      add_ons: [],
      purchased_on: "2026-04-22",
      sold_through: "Meridian Air checkout",
      trip: {
        destination: "Rome, Italy",
        departs_on: "2026-06-10",
        returns_on: "2026-06-18",
        trip_cost: 3100,
      },
      travelers: ["Maya Patel"],
      status: "active",
    },
    customer: {
      customer_id: "CUS-3001",
      name: "Maya Patel",
      email: "maya.patel@example.com",
      phone: "+1 201 555 0143",
      date_of_birth: "1988-03-14",
      address: {
        street: "48 Wayne Street, Apt 5C",
        city: "Jersey City",
        state: "NJ",
        zip: "07302",
      },
      customer_since: "2023-04-11",
      previous_claims: [
        {
          claim_id: "CLM-0412",
          filed_on: "2024-08-19",
          incident_date: "2024-08-09",
          claim_type: "baggage_delay",
          summary: "Checked bag arrived 30 hours late in Lisbon",
          outcome: "approved",
          amount_paid: 140,
        },
      ],
    },
    plan: {
      plan: "premium",
      name: "UnderCover Premium",
      trip_cancellation_max: 5000,
      trip_delay_waiting_hours: 6,
      trip_delay_max: 800,
      baggage_delay_waiting_hours: 12,
      baggage_delay_max: 300,
      baggage_max: 1500,
      per_item_limit: 300,
      passport_max: 250,
      emergency_medical_max: 100000,
      deductible: 50,
    },
    add_ons: [],
    decision: decisionKind
      ? {
          decision: decisionKind,
          payout: payout ?? 0,
          reason:
            "The delay is covered. The documented expenses total the payout.",
          policy_section:
            "Section 3: Trip Delay; Section 10: How we work out your payment",
        }
      : null,
    paid_amount: null,
    signed_off_by: null,
    signed_off_by_name: null,
    signed_off_at: null,
    paid_at: null,
    agent_thread_id: null,
    work_run_id: null,
    failed_attempts: 0,
    evaluation_run_id: null,
    activity,
    documents_read: [
      {
        document: "claims_manual",
        section_id: "10",
        title: "Meals without receipts",
      },
      {
        document: "policy_wording",
        section_id: "3",
        title: "Trip Delay",
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

function mockDetail(initial: ClaimBundle) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = requestUrl(input);
    if (url === "/api/claims/CLM-1001" && !init) {
      return Promise.resolve(response(initial));
    }
    if (url === "/api/handlers") {
      return Promise.resolve(
        response([
          {
            handler_id: "HND-01",
            name: "Dana Whitfield",
            role: "Senior Claims Handler",
            email: "dana@example.com",
            can_approve_over_limit: true,
          },
        ]),
      );
    }
    return Promise.resolve(response({ detail: "Unexpected request" }, 500));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it("shows the full claim, policy, customer history and activity", async () => {
  mockDetail(makeBundle());
  const onBack = vi.fn();
  render(<ClaimWorkspace claimId="CLM-1001" onBack={onBack} />);

  expect(
    await screen.findByRole("heading", {
      name: "Flight home canceled by the airline",
    }),
  ).toBeDefined();
  expect(
    screen.getByText(
      "My flight home from Rome was canceled. I booked a hotel and took taxis.",
    ),
  ).toBeDefined();
  expect(screen.getByText("Airport hotel, one night")).toBeDefined();
  expect(
    screen.getByText(
      "Flight MA 611 was canceled because of crew availability.",
    ),
  ).toBeDefined();
  expect(screen.getByText("UnderCover Premium")).toBeDefined();
  expect(
    screen.getByText("Checked bag arrived 30 hours late in Lisbon"),
  ).toBeDefined();
  expect(screen.getByText("Agent run started")).toBeDefined();
  expect(screen.getByText("Sections read")).toBeDefined();
  expect(
    screen.getByText("Claims manual 10: Meals without receipts"),
  ).toBeDefined();
  expect(screen.getByText("Policy wording 3: Trip Delay")).toBeDefined();
  expect(screen.getByRole("button", { name: "Run agent" })).toBeDefined();

  fireEvent.click(screen.getByRole("button", { name: "Back to queue" }));
  expect(onBack).toHaveBeenCalledOnce();
});

it("starts one run and polls until the decision is visible", async () => {
  const waiting = makeBundle();
  const running = makeBundle("in_progress");
  const decided = makeBundle("decided", "approve", 420);
  decided.paid_amount = 420;
  let reads = 0;
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = requestUrl(input);
    if (url === "/api/claims/CLM-1001" && !init) {
      reads += 1;
      let current = decided;
      if (reads === 1) current = waiting;
      else if (reads === 2) current = running;
      return Promise.resolve(response(current));
    }
    if (url === "/api/claims/CLM-1001/run" && init?.method === "POST") {
      return Promise.resolve(
        response({ claim_id: "CLM-1001", status: "in_progress" }, 202),
      );
    }
    return Promise.resolve(response({ detail: "Unexpected request" }, 500));
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<ClaimWorkspace claimId="CLM-1001" onBack={vi.fn()} />);
  const runButton = await screen.findByRole("button", { name: "Run agent" });

  fireEvent.click(runButton);

  expect(await screen.findByText("Agent decision")).toBeDefined();
  expect(
    screen.getByText(
      "The delay is covered. The documented expenses total the payout.",
    ),
  ).toBeDefined();
  expect(screen.getByText("Payment recorded: $420.00")).toBeDefined();
  const runs = fetchMock.mock.calls.filter(
    ([input]) => requestUrl(input) === "/api/claims/CLM-1001/run",
  );
  expect(runs).toHaveLength(1);
  expect(reads).toBeGreaterThanOrEqual(3);
}, 5000);

it.each(["decline", "refer"] as const)(
  "shows a %s decision without a payment action",
  async (kind) => {
    mockDetail(makeBundle("decided", kind, 0));
    render(<ClaimWorkspace claimId="CLM-1001" onBack={vi.fn()} />);

    expect(await screen.findByText("Agent decision")).toBeDefined();
    expect(
      screen.getByText(kind === "decline" ? "Decline" : "Refer"),
    ).toBeDefined();
    expect(screen.queryByRole("button", { name: "Run agent" })).toBeNull();
    expect(screen.queryByRole("button", { name: /payment/i })).toBeNull();
  },
);

it("signs off and polls until the agent records an over-limit payment", async () => {
  const waiting = makeBundle("waiting_for_sign_off", "approve", 3750);
  const signed = {
    ...waiting,
    claim: { ...waiting.claim, status: "in_progress" as const },
    signed_off_by: "HND-01",
    signed_off_by_name: "Dana Whitfield",
    signed_off_at: "2026-11-09T12:00:00Z",
  };
  const paid = {
    ...signed,
    claim: { ...signed.claim, status: "decided" as const },
    paid_amount: 3750,
    paid_at: "2026-11-09T12:00:02Z",
  };
  let reads = 0;
  const fetchMock = mockDetail(waiting);
  fetchMock.mockImplementation(
    (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === "/api/claims/CLM-1001" && !init) {
        reads += 1;
        let current: ClaimBundle = paid;
        if (reads === 1) current = waiting;
        return Promise.resolve(response(current));
      }
      if (url === "/api/handlers") {
        return Promise.resolve(
          response([
            {
              handler_id: "HND-01",
              name: "Dana Whitfield",
              role: "Senior Claims Handler",
              email: "dana@example.com",
              can_approve_over_limit: true,
            },
          ]),
        );
      }
      if (url === "/api/claims/CLM-1001/sign-off")
        return Promise.resolve(response(signed));
      if (url === "/api/claims/CLM-1001/payment")
        return Promise.resolve(response(paid));
      return Promise.resolve(response({ detail: "Unexpected request" }, 500));
    },
  );
  render(<ClaimWorkspace claimId="CLM-1001" onBack={vi.fn()} />);

  const selector = await screen.findByLabelText("Signing handler");
  await screen.findByRole("option", { name: "Dana Whitfield" });
  act(() => {
    fireEvent.change(selector, { target: { value: "HND-01" } });
  });
  expect(selector).toHaveProperty("value", "HND-01");
  const signOffButton = screen.getByRole("button", {
    name: "Sign off payout",
  });
  expect(signOffButton).toHaveProperty("disabled", false);
  fireEvent.click(signOffButton);

  expect(await screen.findByText("Payment recorded: $3,750.00")).toBeDefined();
  expect(screen.getByText(/Signed off by Dana Whitfield at/)).toBeDefined();
  expect(screen.getByText(/Paid at/)).toBeDefined();
  const urls = fetchMock.mock.calls.map(([input]) => requestUrl(input));
  expect(
    urls.filter((url) => url === "/api/claims/CLM-1001/sign-off"),
  ).toHaveLength(1);
  expect(urls).not.toContain("/api/claims/CLM-1001/payment");
  expect(screen.queryByRole("button", { name: "Sign off payout" })).toBeNull();
});

it("shows a rejected sign-off and keeps the decision visible", async () => {
  const waiting = makeBundle("waiting_for_sign_off", "approve", 3750);
  const fetchMock = mockDetail(waiting);
  fetchMock.mockImplementation(
    (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === "/api/claims/CLM-1001" && !init)
        return Promise.resolve(response(waiting));
      if (url === "/api/handlers")
        return Promise.resolve(
          response([
            {
              handler_id: "HND-01",
              name: "Dana Whitfield",
              role: "Senior Claims Handler",
              email: "dana@example.com",
              can_approve_over_limit: true,
            },
          ]),
        );
      if (url === "/api/claims/CLM-1001/sign-off")
        return Promise.resolve(
          response(
            { detail: "Payout over $1,000.00 requires handler sign-off" },
            409,
          ),
        );
      return Promise.resolve(response({ detail: "Unexpected request" }, 500));
    },
  );
  render(<ClaimWorkspace claimId="CLM-1001" onBack={vi.fn()} />);

  const selector = await screen.findByLabelText("Signing handler");
  await screen.findByRole("option", { name: "Dana Whitfield" });
  act(() => {
    fireEvent.change(selector, { target: { value: "HND-01" } });
  });
  fireEvent.click(screen.getByRole("button", { name: "Sign off payout" }));

  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "Payout over $1,000.00 requires handler sign-off",
  );
  expect(
    screen.getByText(
      "The delay is covered. The documented expenses total the payout.",
    ),
  ).toBeDefined();
  expect(screen.queryByText("Payment recorded: $3,750.00")).toBeNull();
});

it("shows a claim loading failure", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
  render(<ClaimWorkspace claimId="CLM-1001" onBack={vi.fn()} />);

  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "We could not load this claim.",
  );
});
