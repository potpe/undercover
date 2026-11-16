import { expect, test } from "@playwright/test";
import { signIn } from "./auth";
import {
  agentHeaders,
  handlerHeaders,
  recordClaimStep,
  recordApproval,
} from "./api";

test.beforeEach(async ({ page }) => {
  await signIn(page);
});

test("the queue shows the pilot claims and security claim", async ({
  page,
}) => {
  await page.goto("/");

  await expect(
    page.getByRole("heading", { name: "Claims queue" }),
  ).toBeVisible();
  await expect(page.locator("tbody tr")).toHaveCount(26);
  await expect(page.getByText("Maya Patel", { exact: true })).toBeVisible();
  await expect(page.getByText("$420.00", { exact: true })).toBeVisible();
  await expect(page.getByText("CLM-1025", { exact: true })).toBeVisible();
  await expect(page.getByText("CLM-1901", { exact: true })).toBeVisible();
});

test("the agent instructions are read only", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Configuration" }).click();

  await expect(
    page.getByRole("heading", { name: "Configuration" }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "Version 1" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Queue agent" })).toHaveCount(
    0,
  );
  await expect(
    page.getByText("You are the UnderCover claim agent.", { exact: false }),
  ).toHaveCount(1);
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
  const refusal = await recordClaimStep(request, "CLM-1002", {
    kind: "refusal",
    message: "Context declined to answer a policy question.",
    details: {
      source: "context",
      reason: "The source material does not support that conclusion.",
      operation: "search_policy",
    },
  });
  expect(refusal.kind).toBe("refusal");
  const decision = await recordApproval(request, {
    claimId: "CLM-1002",
    payout: 650,
    reason:
      "The lost baggage is covered by the policy. Eligible items less carrier compensation produce a $650.00 payout.",
    section:
      "Section 5: Baggage and Personal Effects; Section 10: How we work out your payment",
  });
  expect(decision.ok()).toBe(true);
  const decisionBundle = (await decision.json()) as {
    decision: {
      decided_by: string;
      decided_at: string;
      manual_version: string;
      calculation: {
        lines: { step: string; amount: number }[];
        payout: number;
      };
    };
  };
  expect(decisionBundle.decision.decided_by).toBe("Test handler");
  expect(decisionBundle.decision.manual_version).toBe("2026.2");
  expect(Number.isFinite(Date.parse(decisionBundle.decision.decided_at))).toBe(
    true,
  );
  expect(decisionBundle.decision.decided_at).toMatch(/(?:Z|\+00:00)$/);
  expect(decisionBundle.decision.calculation.payout).toBe(650);
  expect(decisionBundle.decision.calculation.lines.length).toBeGreaterThan(0);
  const payment = await request.post(
    "http://127.0.0.1:8000/api/claims/CLM-1002/payment",
    { data: { amount: 650 }, headers: agentHeaders },
  );
  expect(payment.ok()).toBe(true);

  await page.goto("/");
  await page.getByRole("button", { name: "View claim CLM-1002" }).click();

  await expect(
    page.getByRole("heading", { name: "Agent decision" }),
  ).toBeVisible();
  await expect(page.getByText("Test handler", { exact: true })).toBeVisible();
  await expect(page.getByText("2026.2", { exact: true })).toBeVisible();
  const calculation = page.getByRole("region", {
    name: "Payout calculation",
  });
  await expect(calculation).toBeVisible();
  await expect(calculation.locator("li")).toHaveCount(
    decisionBundle.decision.calculation.lines.length,
  );
  for (const line of decisionBundle.decision.calculation.lines) {
    await expect(
      calculation.getByText(line.step, { exact: true }),
    ).toBeVisible();
  }
  await expect(calculation.getByText("Calculated payout")).toBeVisible();
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
    page.getByText("Context refused", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("The source material does not support that conclusion.", {
      exact: true,
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

test("handler sign-off pays immediately without resuming the agent service", async ({
  page,
  request,
}) => {
  const decision = await recordApproval(request, {
    claimId: "CLM-1003",
    payout: 3750,
    reason:
      "The sudden wrist fracture required emergency treatment during the covered trip. The $3,800.00 bill less the $50.00 Premium deductible gives a $3,750.00 payout.",
    section:
      "Section 7: Emergency Medical; Section 10: How we work out your payment",
  });
  expect(decision.ok()).toBe(true);
  const blocked = await request.post(
    "http://127.0.0.1:8000/api/claims/CLM-1003/payment",
    { data: { amount: 3750 }, headers: agentHeaders },
  );
  expect(blocked.status()).toBe(403);

  await page.goto("/");
  await page.getByRole("button", { name: "View claim CLM-1003" }).click();
  await expect(
    page.getByText("Waiting for sign-off", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Payment recorded: $3,750.00", { exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Sign off payout" }).click();

  await expect(page.locator(".status-decided")).toHaveText("Decided");
  await expect(page.getByText(/Signed off by Dana Whitfield at/)).toBeVisible();
  await expect(
    page.locator(".decision-summary").getByText("Payment recorded: $3,750.00", {
      exact: true,
    }),
  ).toHaveCount(1);
  const result = await request.get(
    "http://127.0.0.1:8000/api/claims/CLM-1003",
    {
      headers: agentHeaders,
    },
  );
  const body = (await result.json()) as {
    claim: { status: string };
    paid_amount: number | null;
    signed_off_by: string | null;
    signed_off_at: string | null;
    paid_at: string | null;
    activity: { message: string }[];
  };
  expect(result.ok()).toBe(true);
  expect(body.claim.status).toBe("decided");
  expect(body.paid_amount).toBe(3750);
  expect(body.signed_off_by).toBe("HND-01");
  expect(body.signed_off_at).not.toBeNull();
  expect(body.paid_at).not.toBeNull();
  expect(
    body.activity.filter(
      (entry) => entry.message === "Payment recorded: $3,750.00",
    ),
  ).toHaveLength(1);
  const repeatedSignOff = await request.post(
    "http://127.0.0.1:8000/api/claims/CLM-1003/sign-off",
    { headers: await handlerHeaders(request) },
  );
  expect(repeatedSignOff.status()).toBe(200);
  const unchanged = await request.get(
    "http://127.0.0.1:8000/api/claims/CLM-1003",
    { headers: agentHeaders },
  );
  const unchangedBody = (await unchanged.json()) as typeof body;
  expect(unchangedBody.claim.status).toBe("decided");
  expect(unchangedBody.paid_amount).toBe(3750);
  expect(
    unchangedBody.activity.filter(
      (entry) => entry.message === "Payment recorded: $3,750.00",
    ),
  ).toHaveLength(1);
  const calls = await request.get("http://127.0.0.1:8003/requests");
  const requests = (await calls.json()) as { method: string; url: string }[];
  expect(
    requests.filter((entry) => entry.url === "/claims/CLM-1003/resume"),
  ).toHaveLength(0);
});

test("a Gateway hold is signed off and paid only once", async ({
  page,
  request,
}) => {
  const decision = await recordApproval(request, {
    claimId: "CLM-1004",
    payout: 650,
    reason: "The eligible emergency expense supports a $650.00 payout.",
    section: "Section 7: Emergency Medical",
  });
  expect(decision.ok()).toBe(true);
  const reason = "Gateway declined this payment for the test.";
  const hold = await request.post(
    "http://127.0.0.1:8000/api/internal/claims/CLM-1004/gateway-hold",
    { data: { reason }, headers: agentHeaders },
  );
  expect(hold.ok()).toBe(true);
  const held = (await hold.json()) as {
    claim: { status: string };
    gateway_refused: boolean;
    gateway_refusal_reason: string | null;
  };
  expect(held.claim.status).toBe("waiting_for_sign_off");
  expect(held.gateway_refused).toBe(true);
  expect(held.gateway_refusal_reason).toBe(reason);

  await page.goto("/");
  await page.getByRole("button", { name: "View claim CLM-1004" }).click();
  await expect(
    page.getByText(
      "The Gateway refused payment. A handler must sign off before payment can be recorded.",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(
    page.getByText(`Gateway response: ${reason}`, { exact: false }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Sign off payout" }).click();
  await expect(
    page.locator(".decision-summary").getByText("Payment recorded: $650.00", {
      exact: true,
    }),
  ).toBeVisible();

  const signOff = async () =>
    request.post("http://127.0.0.1:8000/api/claims/CLM-1004/sign-off", {
      headers: await handlerHeaders(request),
    });
  expect((await signOff()).status()).toBe(200);
  const paid = await request.get("http://127.0.0.1:8000/api/claims/CLM-1004", {
    headers: agentHeaders,
  });
  const paidBundle = (await paid.json()) as {
    paid_amount: number | null;
    activity: { message: string }[];
  };
  expect(paid.ok()).toBe(true);
  expect(paidBundle.paid_amount).toBe(650);
  expect(
    paidBundle.activity.filter(
      (entry) => entry.message === "Payment recorded: $650.00",
    ),
  ).toHaveLength(1);
});

test("starting a dispatch sends N to the agent service", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await page.getByLabel("Agents at once").fill("5");
  const started = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/dispatches") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Start dispatch" }).click();
  expect((await started).ok()).toBe(true);

  const calls = await request.get("http://127.0.0.1:8003/requests");
  const requests = (await calls.json()) as {
    method: string;
    url: string;
    body: { concurrency?: number } | null;
  }[];
  const dispatch = requests.find((entry) => entry.url === "/dispatches");
  expect(dispatch?.body).toEqual({ concurrency: 5 });
});

test("the dispatch concurrency is validated without starting a model run", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("Agents at once").fill("0");
  await page.getByRole("button", { name: "Start dispatch" }).click();

  await expect(page.getByRole("alert")).toHaveText(
    "Agents at once must be a whole number from 1 to 25.",
  );
});

test("starting an evaluation sends N, set and scope to the agent service", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Scoreboard" }).click();
  await page.getByLabel("Evaluation set").selectOption("held_out");
  await page.getByLabel("Only the five tiny claims").check();
  await page.getByLabel("Agents at once").fill("5");
  const started = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/evaluations") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Start evaluation" }).click();
  await started;

  const calls = await request.get("http://127.0.0.1:8003/requests");
  const requests = (await calls.json()) as {
    url: string;
    body: {
      concurrency?: number;
      evaluation_set?: string;
      tiny?: boolean;
    } | null;
  }[];
  const evaluation = requests.find((entry) => entry.url === "/evaluations");
  expect(evaluation?.body).toEqual({
    concurrency: 5,
    evaluation_set: "held_out",
    tiny: true,
  });
});

test("the scoreboard shows an API-played evaluation and linked copy", async ({
  page,
  request,
}) => {
  const created = await request.post(
    "http://127.0.0.1:8000/api/internal/evaluations",
    {
      data: {
        concurrency: 5,
        configuration_version: null,
        model_name: "openai/gpt-6-luna",
        reasoning_effort: "low",
        round_id: null,
        round_purpose: null,
        evaluation_set: "evaluation",
        tiny: true,
      },
      headers: agentHeaders,
    },
  );
  expect(created.status()).toBe(201);
  const run = (await created.json()) as { run_id: number };
  const initial = await request.get(
    `http://127.0.0.1:8000/api/evaluations/${String(run.run_id)}`,
    { headers: await handlerHeaders(request) },
  );
  const evaluation = (await initial.json()) as {
    claim_comparisons: { claim_id: string }[];
  };
  const copyId = evaluation.claim_comparisons[0].claim_id;
  const decision = await recordApproval(request, {
    claimId: copyId,
    payout: 3750,
    reason: "The test plays a held approval for the evaluation copy.",
    section: "Section 7: Emergency Medical; Section 10: Payment",
  });
  expect(decision.ok()).toBe(true);
  await request.post(
    `http://127.0.0.1:8000/api/internal/runs/${String(run.run_id)}/claims/${copyId}/complete`,
    { data: { result: "Held evaluation approval" }, headers: agentHeaders },
  );
  await request.post(`http://127.0.0.1:8000/api/claims/${copyId}/model-calls`, {
    data: {
      run_id: run.run_id,
      model_name: "openai/gpt-6-luna",
      reasoning_effort: "low",
      prompt_tokens: 10,
      completion_tokens: 5,
      total_tokens: 15,
      cost: 0.1,
    },
    headers: agentHeaders,
  });
  await request.post(
    `http://127.0.0.1:8000/api/claims/${copyId}/work-periods`,
    {
      data: {
        run_id: run.run_id,
        thread_id: `thread-${copyId}`,
        started_at: "2026-11-10T12:00:00Z",
        finished_at: "2026-11-10T12:00:02Z",
        seconds: 2,
      },
      headers: agentHeaders,
    },
  );
  await request.post(
    `http://127.0.0.1:8000/api/internal/runs/${String(run.run_id)}/finish`,
    { headers: agentHeaders },
  );

  await page.goto("/");
  await page.getByRole("button", { name: "Scoreboard" }).click();

  await expect(page.getByText("Decision accuracy: 20%")).toBeVisible();
  await expect(page.getByText("Accuracy: 20%", { exact: true })).toBeVisible();
  await expect(page.getByText("Cost of mistakes: $3,900.00")).toBeVisible();
  await expect(page.getByText("Straight through: 20%")).toBeVisible();
  await expect(page.getByText("Model cost: $0.10")).toBeVisible();
  await expect(page.getByText("Judge cost: $0.001")).toBeVisible();
  await expect(page.getByText("Model cost per claim: $0.02")).toBeVisible();
  await expect(page.getByText("Time per claim: 0.4 s")).toBeVisible();
  await expect(page.getByText("Slowest claim: 2.0 s")).toBeVisible();

  await page
    .getByRole("button", { name: `View evaluation ${String(run.run_id)}` })
    .click();
  await expect(page.getByText("Decision miss").first()).toBeVisible();
  await expect(page.getByText("C (1.51 / 3)")).toBeVisible();
  await expect(page.getByText("Same grounds: Yes").first()).toBeVisible();
  await expect(page.getByText("Same section: No").first()).toBeVisible();
  await page.getByRole("button", { name: `View claim ${copyId}` }).click();

  await expect(page.locator(".evaluation-copy")).toBeVisible();
  await expect(page.getByLabel("Signing handler")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Run agent" })).toHaveCount(0);
});
