import { expect, test } from "@playwright/test";
import { signIn } from "./auth";
import { agentHeaders } from "./api";

test("a handler reviews a proposal through the service boundary and sees its applied version", async ({
  page,
  request,
}) => {
  const created = await request.post(
    "http://127.0.0.1:8000/api/internal/rounds",
    { data: { handler_id: "HND-01" }, headers: agentHeaders },
  );
  expect(created.ok()).toBe(true);
  const round = (await created.json()) as { round_id: number };
  const prefix = `http://127.0.0.1:8000/api/internal/rounds/${String(round.round_id)}`;
  const proposed = await request.post(`${prefix}/proposal`, {
    data: {
      payload: {
        instructions: "Use the policy and check all coverage before deciding.",
        model: "openai/gpt-6-luna",
        reasoning_effort: "medium",
        reason: "Check all coverage",
      },
    },
    headers: agentHeaders,
  });
  expect(proposed.ok()).toBe(true);
  const waiting = (await proposed.json()) as {
    proposals: { proposal_id: number }[];
  };
  expect(waiting.proposals).toHaveLength(1);
  await signIn(page);
  await page.getByRole("button", { name: "Improvement", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Proposed configuration" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Approve proposal" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "Confirm approval" }).click();
  await expect
    .poll(async () => {
      const response = await request.get("http://127.0.0.1:8003/requests");
      const calls = (await response.json()) as { url: string; body: unknown }[];
      return calls.find(
        (call) => call.url === `/rounds/${String(round.round_id)}/decision`,
      )?.body;
    })
    .toEqual({
      proposal_id: waiting.proposals[0].proposal_id,
      decision: "approve",
      reason: "",
      handler_id: "HND-01",
    });
  // Play the accepted service write, without calling any model.
  expect(
    (
      await request.post(`${prefix}/decision`, {
        data: {
          proposal_id: waiting.proposals[0].proposal_id,
          decision: "approve",
          reason: "",
          handler_id: "HND-01",
        },
        headers: agentHeaders,
      })
    ).ok(),
  ).toBe(true);
  expect(
    (
      await request.post(`${prefix}/apply/configuration`, {
        headers: agentHeaders,
      })
    ).ok(),
  ).toBe(true);
  expect(
    (
      await request.post(`${prefix}/finish`, {
        data: { error: "Stub candidate not evaluated" },
        headers: agentHeaders,
      })
    ).ok(),
  ).toBe(true);
  await expect(
    page.getByText("Tried version 2", { exact: false }),
  ).toBeVisible();
  await expect(page.getByText("Rolled back", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "Configuration", exact: true })
    .click();
  await expect(page.getByRole("heading", { name: "Version 2" })).toBeVisible();
  await expect(
    page.getByText("Current version: 1", { exact: true }),
  ).toBeVisible();
});

test("a non-senior handler can review rounds but cannot change them", async ({
  page,
}) => {
  await signIn(page, "marcus.oyelaran@undercover.example");
  await page.getByRole("button", { name: "Improvement", exact: true }).click();
  await expect(page.getByRole("note")).toContainText("start or stop rounds");
  await expect(page.getByRole("button", { name: "Start round" })).toHaveCount(
    0,
  );
  await expect(
    page.getByRole("button", { name: "Approve proposal" }),
  ).toHaveCount(0);
});
