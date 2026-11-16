import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import type { Activity } from "./api";
import { ContextUsed } from "./ContextUsed";

afterEach(cleanup);

it("groups and deduplicates lookup inputs under each source file", () => {
  const activity: Activity[] = [
    {
      id: 1,
      created_at: "2026-11-03T12:00:00Z",
      message: "Search",
      kind: "lookup",
      details: {
        operation: "search_documents",
        input: { query: "trip delay", filters: { section: 3, type: "policy" } },
        sources: [
          {
            file: "policy.md",
            system: "Policy",
            owner: "Claims",
            reviewed: "2026-01-01",
          },
          {
            file: "manual.md",
            system: "Claims",
            owner: "Operations",
            reviewed: "2026-02-01",
          },
        ],
      },
    },
    {
      id: 2,
      created_at: "2026-11-03T12:01:00Z",
      message: "Same search",
      kind: "lookup",
      details: {
        operation: "search_documents",
        input: { filters: { type: "policy", section: 3 }, query: "trip delay" },
        sources: [
          {
            file: "policy.md",
            system: "Policy",
            owner: "Claims",
            reviewed: "2026-01-01",
          },
        ],
      },
    },
    {
      id: 3,
      created_at: "2026-11-03T12:02:00Z",
      message: "Different search",
      kind: "lookup",
      details: {
        operation: "search_documents",
        input: { query: "baggage" },
        sources: [
          {
            file: "policy.md",
            system: "Policy",
            owner: "Claims",
            reviewed: "2026-01-01",
          },
        ],
      },
    },
  ];
  render(<ContextUsed activity={activity} />);
  expect(screen.getByText("Context used")).toBeDefined();
  expect(screen.getByText("policy.md")).toBeDefined();
  expect(screen.getByText("manual.md")).toBeDefined();
  expect(
    screen.getByText("Claims · Operations · reviewed 2026-02-01"),
  ).toBeDefined();
  expect(screen.getAllByText("search documents")).toHaveLength(3);
  expect(screen.getByText("baggage")).toBeDefined();
});

it("shows an empty context state", () => {
  render(<ContextUsed activity={[]} />);
  expect(
    screen.getByText("No reference material has been used yet."),
  ).toBeDefined();
});
