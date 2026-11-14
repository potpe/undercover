import { afterEach, expect, it, vi } from "vitest";
import { request, setAccessToken } from "./api";

afterEach(() => {
  setAccessToken(null);
  vi.unstubAllGlobals();
});

it("sends the current bearer token on parameterless GET requests", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValue(new Response(JSON.stringify({ status: "ok" })));
  vi.stubGlobal("fetch", fetch);
  setAccessToken("current-handler-token");

  await request("/api/claims");

  expect(fetch).toHaveBeenCalledWith(
    "/api/claims",
    expect.objectContaining({
      headers: new Headers({ Authorization: "Bearer current-handler-token" }),
    }),
  );
});

it("sends the bearer token on actions and preserves supplied request headers", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValue(new Response(JSON.stringify({ status: "ok" })));
  vi.stubGlobal("fetch", fetch);
  setAccessToken("current-handler-token");

  await request("/api/action", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Request-Id": "request-123",
    },
    body: "{}",
  });

  expect(fetch).toHaveBeenCalledWith(
    "/api/action",
    expect.objectContaining({
      method: "POST",
      headers: new Headers({
        Authorization: "Bearer current-handler-token",
        "Content-Type": "application/json",
        "X-Request-Id": "request-123",
      }),
    }),
  );
});

it("uses the latest token and removes authorization after the token is cleared", async () => {
  const fetch = vi.fn<typeof globalThis.fetch>(() =>
    Promise.resolve(new Response(JSON.stringify({ status: "ok" }))),
  );
  vi.stubGlobal("fetch", fetch);

  setAccessToken("initial-handler-token");
  await request("/api/first");
  setAccessToken("refreshed-handler-token");
  await request("/api/second");
  setAccessToken(null);
  await request("/api/third");

  const headersForRequest = fetch.mock.calls.map(([, init]) =>
    new Headers(init?.headers).get("Authorization"),
  );
  expect(headersForRequest).toEqual([
    "Bearer initial-handler-token",
    "Bearer refreshed-handler-token",
    null,
  ]);
});
