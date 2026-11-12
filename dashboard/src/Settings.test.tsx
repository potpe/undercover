import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Handler, PlatformSettings } from "./api";
import { Settings } from "./Settings";

const settings: PlatformSettings = {
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
  models_allowed: [
    {
      model_name: "openai/gpt-6-luna",
      reasoning_efforts: ["none", "low", "medium"],
      input_price: 0.1,
      output_price: 0.5,
    },
  ],
};
const handler: Handler = {
  handler_id: "HND-01",
  name: "Dana Whitfield",
  role: "Senior Claims Handler",
  email: "dana.whitfield@undercover.example",
  can_approve_over_limit: true,
};

function mockFetch() {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    let url: string;
    if (typeof input === "string") url = input;
    else url = input instanceof URL ? input.href : input.url;
    if (url === "/api/settings" && init?.method !== "PUT")
      return Promise.resolve(
        new Response(JSON.stringify(settings), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    if (url === "/api/settings" && init?.method === "PUT")
      return Promise.resolve(
        new Response(JSON.stringify({ ...settings, approval_limit: 1100 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    if (url === "/api/settings/changes")
      return Promise.resolve(
        new Response(
          JSON.stringify([
            {
              key: "approval_limit",
              old_value: 1000,
              new_value: 1100,
              changed_by: "HND-01",
              changed_at: "2026-11-12T12:00:00Z",
            },
          ]),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
    return Promise.resolve(new Response("{}", { status: 404 }));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.setAttribute("open", "");
    },
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("confirms and saves a complete settings change", async () => {
  const fetchMock = mockFetch();
  render(
    <Settings
      handler={handler}
      appearance="system"
      onAppearanceChange={vi.fn()}
    />,
  );
  const input = await screen.findByLabelText("Approval limit (USD)");
  fireEvent.change(input, { target: { value: "1100" } });
  fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "Confirm settings change" }),
  );

  expect(await screen.findByText("Settings saved.")).toBeDefined();
  const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
  const body = put?.[1]?.body;
  if (typeof body !== "string") throw new Error("Missing settings request");
  expect(JSON.parse(body) as unknown).toMatchObject({
    approval_limit: 1100,
    evaluation_concurrency: 25,
  });
  expect(put?.[1]?.body).not.toContain("models_allowed");
});

it("lets every handler read settings but prevents unauthorized changes", async () => {
  mockFetch();
  render(
    <Settings
      handler={{
        ...handler,
        name: "Marcus Oyelaran",
        can_approve_over_limit: false,
      }}
      appearance="system"
      onAppearanceChange={vi.fn()}
    />,
  );

  expect(
    (await screen.findByLabelText("Approval limit (USD)")).matches(":disabled"),
  ).toBe(true);
  expect(screen.getByRole("note")).toHaveProperty(
    "textContent",
    "You can view settings, but only a handler who can approve over-limit payouts can change them.",
  );
  expect(screen.queryByRole("button", { name: "Save settings" })).toBeNull();
});

it("changes the browser-only appearance", async () => {
  mockFetch();
  const change = vi.fn();
  render(
    <Settings
      handler={handler}
      appearance="system"
      onAppearanceChange={change}
    />,
  );
  fireEvent.change(await screen.findByLabelText("Appearance"), {
    target: { value: "dark" },
  });
  expect(change).toHaveBeenCalledWith("dark");
});
