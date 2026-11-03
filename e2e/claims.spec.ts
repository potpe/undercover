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
    page.getByText("You are the UnderCover Agent.", { exact: false }),
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

test("an over-limit payout is paid only after handler sign-off", async ({
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
  await page
    .getByRole("button", { name: "Sign off and record payment" })
    .click();

  await expect(
    page.locator(".decision-summary").getByText("Payment recorded: $3,750.00", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.locator(".status-decided")).toHaveText("Decided");
  await expect(
    page.getByText("Payment signed off by Dana Whitfield", { exact: true }),
  ).toBeVisible();
  const result = await request.get("http://127.0.0.1:8000/api/claims/CLM-1003");
  const body = (await result.json()) as { paid_amount: number | null };
  expect(body.paid_amount).toBe(3750);
});
