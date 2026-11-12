import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Session } from "@supabase/supabase-js";
import { App } from "./App";
import { testHandler } from "./testHandler";

const auth = vi.hoisted(() => ({
  getSession: vi.fn(),
  onAuthStateChange: vi.fn(),
  signOut: vi.fn(),
}));
vi.mock("./supabase", () => ({ supabase: { auth } }));

const session = {
  access_token: "dana-token",
  user: { id: "dana-user", email: testHandler.email },
} as unknown as Session;
let listener: (event: string, value: Session | null) => void;

function response(body: unknown) {
  return new Response(JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
  });
}

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  return input instanceof URL ? input.href : input.url;
}

beforeEach(() => {
  auth.getSession.mockResolvedValue({ data: { session } });
  auth.onAuthStateChange.mockImplementation((callback: typeof listener) => {
    listener = callback;
    return { data: { subscription: { unsubscribe: vi.fn() } } };
  });
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      switch (requestUrl(input)) {
        case "/api/me":
          return Promise.resolve(response(testHandler));
        case "/api/claims":
          return Promise.resolve(response([]));
        case "/api/settings":
          return Promise.resolve(
            response({
              approval_limit: 1000,
              spending_limit: 0.5,
              evaluation_concurrency: 25,
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
              models_allowed: [],
            }),
          );
        case "/api/settings/changes":
          return Promise.resolve(response([]));
        default:
          return Promise.resolve(response(null));
      }
    }),
  );
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.setAttribute("open", "");
    },
  });
});

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
  vi.unstubAllGlobals();
});

it.each(["SIGNED_IN", "TOKEN_REFRESHED"])(
  "preserves unsaved settings and the confirmation dialog on same-user %s",
  async (event) => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
    const input = await screen.findByLabelText("Approval limit (USD)");
    fireEvent.change(input, { target: { value: "1200" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    const dialog = screen.getByRole("dialog");

    act(() => {
      listener(event, { ...session, access_token: "refreshed-token" });
    });

    expect(screen.getByLabelText("Approval limit (USD)")).toBe(input);
    expect(input).toHaveProperty("value", "1200");
    expect(screen.getByRole("dialog")).toBe(dialog);
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm settings change" }),
    );
    await screen.findByText("Settings saved.");
    const request = vi
      .mocked(fetch)
      .mock.calls.find(
        ([input, init]) =>
          requestUrl(input) === "/api/settings" && init?.method === "PUT",
      );
    expect(new Headers(request?.[1]?.headers).get("Authorization")).toBe(
      "Bearer refreshed-token",
    );
  },
);

it("clears the previous handler and form when the signed-in identity changes", async () => {
  render(<App />);
  const input = await screen.findByLabelText("Agents at once");
  fireEvent.change(input, { target: { value: "7" } });
  let resolveHandler: ((value: Response) => void) | undefined;
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      if (requestUrl(input) === "/api/me")
        return new Promise<Response>((resolve) => {
          resolveHandler = resolve;
        });
      if (requestUrl(input) === "/api/claims")
        return Promise.resolve(response([]));
      return Promise.resolve(response(null));
    }),
  );

  act(() => {
    listener("SIGNED_IN", {
      ...session,
      access_token: "marcus-token",
      user: {
        ...session.user,
        id: "marcus-user",
        email: "marcus.oyelaran@undercover.example",
      },
    });
  });
  expect(screen.queryByText("Dana Whitfield")).toBeNull();
  expect(screen.queryByLabelText("Agents at once")).toBeNull();
  await waitFor(() => {
    expect(resolveHandler).toBeDefined();
  });
  act(() => {
    resolveHandler?.(
      response({
        ...testHandler,
        name: "Marcus Oyelaran",
        email: "marcus.oyelaran@undercover.example",
        can_approve_over_limit: false,
      }),
    );
  });
  expect(await screen.findByText("Marcus Oyelaran")).toBeDefined();
  expect(screen.getByLabelText("Agents at once")).toHaveProperty("value", "3");
});
