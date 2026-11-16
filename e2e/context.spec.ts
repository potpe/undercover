import { expect, test } from "@playwright/test";
import { signIn } from "./auth";
import { agentHeaders, recordContextTestSteps } from "./api";

test.beforeEach(async ({ page }) => {
  await signIn(page);
});

test("the dashboard shows dispatch claims, record summary and context used", async ({
  page,
  request,
}) => {
  const run = {
    run_id: 7,
    kind: "dispatch",
    concurrency: 3,
    configuration_version: 1,
    round_id: null,
    round_purpose: null,
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
      left_for_handler: ["CLM-1002"],
      in_progress: ["CLM-1004"],
      error: null,
    },
    created_at: "2026-11-05T12:00:00Z",
    completed_at: "2026-11-05T12:05:00Z",
    stopped_at: null,
    model_cost: 0.125,
    prompt_tokens: 1000,
    completion_tokens: 100,
    total_tokens: 1100,
    active_seconds: 12.5,
    wall_seconds: 30,
    claims: [
      {
        claim_id: "CLM-1001",
        position: 1,
        status: "done",
        attempts: 0,
        result: "Approve $420.00",
        error: null,
        started_at: "2026-11-05T12:00:00Z",
        finished_at: "2026-11-05T12:00:10Z",
        model_cost: 0.05,
        active_seconds: 10,
      },
      {
        claim_id: "CLM-1002",
        position: 2,
        status: "left_for_handler",
        attempts: 2,
        result: "Provider failed",
        error: "Provider failed",
        started_at: "2026-11-05T12:00:00Z",
        finished_at: "2026-11-05T12:00:20Z",
        model_cost: 0.05,
        active_seconds: 20,
      },
      {
        claim_id: "CLM-1004",
        position: 3,
        status: "in_progress",
        attempts: 0,
        result: null,
        error: "Rate limited",
        started_at: "2026-11-05T12:00:00Z",
        finished_at: null,
        model_cost: 0.025,
        active_seconds: 5,
      },
    ],
  };
  await page.route("**/api/dispatches/current", (route) =>
    route.fulfill({ json: run }),
  );
  const retiredSection = await request.post(
    "http://127.0.0.1:8000/api/claims/CLM-1001/document-section",
    {
      data: { document: "claims_manual", section_id: "10" },
      headers: agentHeaders,
    },
  );
  const retiredSearch = await request.post(
    "http://127.0.0.1:8000/api/claims/CLM-1001/document-search",
    { data: {}, headers: agentHeaders },
  );
  expect(retiredSection.status()).toBe(404);
  expect(retiredSearch.status()).toBe(404);
  const steps = await recordContextTestSteps(request, "CLM-1001");
  expect(steps.map((step) => step.kind)).toEqual([
    "tool",
    "tool",
    "lookup",
    "lookup",
    "memory_search",
    "memory_save",
  ]);
  expect(
    steps.every(
      (step) =>
        step.id > 0 &&
        step.message.length > 0 &&
        Number.isFinite(Date.parse(step.created_at)),
    ),
  ).toBe(true);
  const persistedIds = steps.map((step) => step.id);
  expect(persistedIds).toEqual(
    [...persistedIds].sort((left, right) => left - right),
  );

  await page.goto("/");
  await expect(
    page.getByText("2 approved, 1 declined, 1 referred."),
  ).toBeVisible();
  await expect(page.getByText("openai/gpt-6-luna")).toBeVisible();
  await expect(page.getByText("Model cost: $0.125")).toBeVisible();
  await expect(page.locator(".dispatch-list li")).toHaveCount(3);
  await expect(
    page
      .locator(".dispatch-status")
      .getByText("Left for handler", { exact: true }),
  ).toBeVisible();

  await page
    .getByRole("button", { name: "View claim CLM-1001" })
    .first()
    .click();
  const contextUsed = page.locator(".context-used");
  await expect(
    contextUsed.getByRole("heading", { name: "Context used" }),
  ).toBeVisible();
  await expect(
    contextUsed.getByRole("heading", {
      name: "claims_manual.md",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    contextUsed.getByRole("heading", {
      name: "policy_wording.md",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    contextUsed.getByText(
      "Claims operations · Head of claims operations · reviewed 2026-09-01",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(
    contextUsed.getByText(
      "Published policy documents · Legal · reviewed 2026-09-01",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(
    contextUsed.getByText("search documents", { exact: true }),
  ).toHaveCount(2);
  const activity = page.locator(".activity-panel");
  await expect(
    activity.getByText("search_documents", { exact: true }),
  ).toBeVisible();
  await expect(
    activity.getByText("undercover-context___lookup_term", { exact: true }),
  ).toBeVisible();
  await expect(
    activity.getByText("prior claim history", { exact: false }),
  ).toBeVisible();
  await expect(
    activity.getByText("CLM-0990: Trip delay claim approved for $320.00.", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    activity.getByText("Saved 2 notes.", { exact: true }),
  ).toBeVisible();
  await expect(
    activity.getByText(
      "Customer reported a flight cancellation and requested reimbursement for eligible prepaid expenses.",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(
    activity.getByText(
      "Decision: approve eligible trip cancellation expenses under the policy.",
      { exact: true },
    ),
  ).toBeVisible();
});
