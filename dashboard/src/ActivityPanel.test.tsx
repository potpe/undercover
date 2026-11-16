import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import type { Activity } from "./api";
import { ActivityPanel } from "./ActivityPanel";

afterEach(cleanup);

const entries: Activity[] = [
  {
    id: 1,
    created_at: "2026-11-03T12:00:00Z",
    message: "Started",
    kind: "activity",
    details: null,
  },
  {
    id: 2,
    created_at: "2026-11-03T12:01:00Z",
    message: "Tool call",
    kind: "tool",
    details: { name: "lookup_term", input: { term: "deductible" } },
  },
  {
    id: 3,
    created_at: "2026-11-03T12:02:00Z",
    message: "Lookup",
    kind: "lookup",
    details: {
      operation: "lookup_term",
      input: { term: "deductible" },
      sources: [
        {
          file: "plans.yaml",
          system: "Policy",
          owner: "Claims",
          reviewed: "2026-01-01",
        },
      ],
    },
  },
  {
    id: 4,
    created_at: "2026-11-03T12:03:00Z",
    message: "Memory search",
    kind: "memory_search",
    details: { query: "past claims", results: ["Earlier baggage claim"] },
  },
  {
    id: 5,
    created_at: "2026-11-03T12:04:00Z",
    message: "Memory save",
    kind: "memory_save",
    details: { messages: ["Claim notes saved"] },
  },
  {
    id: 6,
    created_at: "2026-11-03T12:05:00Z",
    message: "Refused",
    kind: "refusal",
    details: {
      source: "gateway",
      reason: "Payment request refused",
      operation: "pay_claim",
    },
  },
];

it("shows every typed step with accessible refusal labeling and live state", () => {
  const { container } = render(
    <ActivityPanel activity={entries} status="in_progress" />,
  );
  expect(screen.getByText("lookup_term")).toBeDefined();
  expect(screen.getByText("deductible")).toBeDefined();
  expect(screen.getByText("plans.yaml")).toBeDefined();
  expect(
    screen.getByText("Policy · Claims · reviewed 2026-01-01"),
  ).toBeDefined();
  expect(screen.getByText("Earlier baggage claim")).toBeDefined();
  expect(screen.getByText("Claim notes saved")).toBeDefined();
  expect(screen.getByText("Gateway refused")).toBeDefined();
  expect(screen.getByText("Payment request refused")).toBeDefined();
  expect(screen.getByRole("status").textContent).toContain("updates live");
  expect(container.querySelectorAll(".activity-panel > ol > li")).toHaveLength(
    6,
  );
});

it("shows a clear empty state without live status when work is complete", () => {
  render(<ActivityPanel activity={[]} status="decided" />);
  expect(screen.getByText("No agent activity yet.")).toBeDefined();
  expect(screen.queryByRole("status")).toBeNull();
});
