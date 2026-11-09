import { expect, test } from "@playwright/test";

test("the queue shows the 25 pilot claims", async ({ page }) => {
  await page.goto("/");

  await expect(
    page.getByRole("heading", { name: "Claims queue" }),
  ).toBeVisible();
  await expect(page.locator("tbody tr")).toHaveCount(25);
  await expect(page.getByText("Maya Patel", { exact: true })).toBeVisible();
  await expect(page.getByText("$420.00", { exact: true })).toBeVisible();
  await expect(page.getByText("CLM-1025", { exact: true })).toBeVisible();
});

test("the agent instructions are read only", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Agent instructions" }).click();

  await expect(
    page.getByRole("heading", { name: "Agent instructions" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Queue agent" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Claim agent" }),
  ).toBeVisible();
  await expect(
    page.getByText("You are the UnderCover queue agent.", { exact: false }),
  ).toBeVisible();
  await expect(page.getByRole("textbox")).toHaveCount(0);
});

test("the claim workspace shows the full claim and its related records", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "View claim CLM-1001" }).click();

  await expect(
    page.getByRole("heading", {
      name: "Flight home canceled by the airline, rebooked the next morning",
    }),
  ).toBeVisible();
  await expect(
    page.getByText("My flight home from Rome on June 18", { exact: false }),
  ).toBeVisible();
  await expect(page.locator(".items-table tbody tr")).toHaveCount(3);
  await expect(page.locator(".documents details")).toHaveCount(4);
  await page
    .getByText("Meridian Air cancellation notice", { exact: true })
    .click();
  await expect(
    page.getByText("Flight MA 611 Rome FCO to Newark EWR", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByText("UnderCover Premium", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Checked bag arrived 30 hours late in Lisbon", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Run agent" })).toBeVisible();
});

test("a decided claim shows its payout, reason, policy and activity", async ({
  page,
  request,
}) => {
  const decision = await request.post(
    "http://127.0.0.1:8000/api/claims/CLM-1002/decision",
    {
      data: {
        decision: "approve",
        payout: 650,
        reason:
          "The lost baggage is covered by the policy. Eligible items less carrier compensation produce a $650.00 payout.",
        policy_section:
          "Section 5: Baggage and Personal Effects; Section 10: How we work out your payment",
      },
    },
  );
  expect(decision.ok()).toBe(true);
  const payment = await request.post(
    "http://127.0.0.1:8000/api/claims/CLM-1002/payment",
    { data: { amount: 650 } },
  );
  expect(payment.ok()).toBe(true);

  await page.goto("/");
  await page.getByRole("button", { name: "View claim CLM-1002" }).click();

  await expect(
    page.getByRole("heading", { name: "Agent decision" }),
  ).toBeVisible();
  await expect(page.getByText("Approve", { exact: true })).toBeVisible();
  await expect(
    page.locator(".decision-summary").getByText("Payment recorded: $650.00", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByText("The lost baggage is covered by the policy.", {
      exact: false,
    }),
  ).toBeVisible();
  await expect(
    page.getByText("Section 5: Baggage and Personal Effects", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByText("Decision recorded: approve for $650.00", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Run agent" })).toHaveCount(0);
});

test("handler sign-off reaches the agent service without direct payment", async ({
  page,
  request,
}) => {
  const decision = await request.post(
    "http://127.0.0.1:8000/api/claims/CLM-1003/decision",
    {
      data: {
        decision: "approve",
        payout: 3750,
        reason:
          "The sudden wrist fracture required emergency treatment during the covered trip. The $3,800.00 bill less the $50.00 Premium deductible gives a $3,750.00 payout.",
        policy_section:
          "Section 7: Emergency Medical; Section 10: How we work out your payment",
      },
    },
  );
  expect(decision.ok()).toBe(true);
  const blocked = await request.post(
    "http://127.0.0.1:8000/api/claims/CLM-1003/payment",
    { data: { amount: 3750 } },
  );
  expect(blocked.status()).toBe(409);

  await page.goto("/");
  await page.getByRole("button", { name: "View claim CLM-1003" }).click();
  await expect(
    page.getByText("Waiting for sign-off", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Payment recorded: $3,750.00", { exact: true }),
  ).toHaveCount(0);
  await page.getByLabel("Signing handler").selectOption("HND-01");
  await page.getByRole("button", { name: "Sign off payout" }).click();

  await expect(page.locator(".status-in_progress")).toHaveText("In progress");
  await expect(page.getByText(/Signed off by Dana Whitfield at/)).toBeVisible();
  await expect(
    page.locator(".decision-summary").getByText("Payment recorded: $3,750.00", {
      exact: true,
    }),
  ).toHaveCount(0);
  const result = await request.get("http://127.0.0.1:8000/api/claims/CLM-1003");
  const body = (await result.json()) as {
    paid_amount: number | null;
    signed_off_by: string | null;
    signed_off_at: string | null;
  };
  expect(body.paid_amount).toBeNull();
  expect(body.signed_off_by).toBe("HND-01");
  expect(body.signed_off_at).not.toBeNull();
  const calls = await request.get("http://127.0.0.1:8003/requests");
  const requests = (await calls.json()) as { method: string; url: string }[];
  expect(
    requests.filter((entry) => entry.url === "/claims/CLM-1003/resume"),
  ).toHaveLength(1);
});

test("the dashboard shows run to-dos and the sections read", async ({
  page,
  request,
}) => {
  const run = {
    run_id: 7,
    thread_id: "browser",
    claim_cap: 5,
    target_claim_id: null,
    model_name: "deepseek/deepseek-v4.1-flash",
    status: "completed",
    summary: "5 claims handled: 4 done, 1 left for handler.",
    created_at: "2026-11-05T12:00:00Z",
    completed_at: "2026-11-05T12:05:00Z",
    todos: [
      {
        claim_id: "CLM-1001",
        position: 1,
        status: "done",
        attempts: 0,
        result: "Approve $420.00",
        error: null,
      },
      {
        claim_id: "CLM-1002",
        position: 2,
        status: "left_for_handler",
        attempts: 2,
        result: "Provider failed",
        error: "Provider failed",
      },
      {
        claim_id: "CLM-1003",
        position: 3,
        status: "to_do",
        attempts: 0,
        result: null,
        error: null,
      },
      {
        claim_id: "CLM-1004",
        position: 4,
        status: "in_progress",
        attempts: 1,
        result: null,
        error: null,
      },
      {
        claim_id: "CLM-1005",
        position: 5,
        status: "done",
        attempts: 0,
        result: "Approve $243.00",
        error: null,
      },
    ],
  };
  await page.route("**/api/runs/current", (route) =>
    route.fulfill({ json: run }),
  );
  await request.post(
    "http://127.0.0.1:8000/api/claims/CLM-1001/document-section",
    { data: { document: "claims_manual", section_id: "10" } },
  );
  await request.post(
    "http://127.0.0.1:8000/api/claims/CLM-1001/document-section",
    { data: { document: "policy_wording", section_id: "3" } },
  );

  await page.goto("/");
  await expect(
    page.getByText("5 claims handled: 4 done, 1 left for handler."),
  ).toBeVisible();
  await expect(page.getByText("deepseek/deepseek-v4.1-flash")).toBeVisible();
  await expect(page.locator(".todo-list li")).toHaveCount(5);
  await expect(
    page.locator(".todo-status").getByText("Left for handler", { exact: true }),
  ).toBeVisible();

  await page
    .getByRole("button", { name: "View claim CLM-1001" })
    .first()
    .click();
  await expect(
    page.getByRole("heading", { name: "Sections read" }),
  ).toBeVisible();
  await expect(
    page.getByText("Claims manual 10: Meals without receipts", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Policy wording 3: Trip Delay", { exact: true }),
  ).toBeVisible();
});

test("the queue cap is validated without starting a model run", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("Claim cap").fill("0");
  await page.getByRole("button", { name: "Run queue" }).click();

  await expect(page.getByRole("alert")).toHaveText(
    "Claim cap must be a whole number from 1 to 25.",
  );
});
