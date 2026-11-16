import {
  expect,
  type APIRequestContext,
  type APIResponse,
} from "@playwright/test";
import type {
  Activity,
  ClaimBundle,
  ClaimStepRequest,
} from "../dashboard/src/api";

export type { Activity, ClaimStepRequest } from "../dashboard/src/api";

export const AGENT_API_KEY = "undercover-e2e-agent-key-32-characters";
export const agentHeaders = { "X-API-Key": AGENT_API_KEY };

interface PayoutCalculation {
  calculation: { step: string; amount: number }[];
  payout: number;
}

type ClaimStepActivity = Exclude<Activity, { kind: "activity" }>;

export async function recordClaimStep(
  request: APIRequestContext,
  claimId: string,
  step: ClaimStepRequest,
): Promise<ClaimStepActivity> {
  const response = await request.post(
    `http://127.0.0.1:8000/api/claims/${claimId}/steps`,
    { data: step, headers: agentHeaders },
  );
  expect(response.ok()).toBe(true);
  const bundle = (await response.json()) as ClaimBundle;
  const recorded = bundle.activity
    .filter(
      (entry): entry is ClaimStepActivity =>
        entry.kind !== "activity" &&
        entry.kind === step.kind &&
        entry.message === step.message,
    )
    .at(-1);
  expect(recorded).toBeDefined();
  if (!recorded) throw new Error("The claim step was not returned in activity");
  expect(recorded.kind).toBe(step.kind);
  expect(recorded.details).toEqual(step.details);
  return recorded;
}

export async function recordContextTestSteps(
  request: APIRequestContext,
  claimId: string,
): Promise<ClaimStepActivity[]> {
  const lookup: ClaimStepRequest = {
    kind: "lookup",
    message: "Looked up the test policy context.",
    details: {
      operation: "search_documents",
      input: { query: "Trip Delay" },
      sources: [
        {
          file: "claims_manual.md",
          system: "Claims operations",
          owner: "Head of claims operations",
          reviewed: "2026-09-01",
        },
        {
          file: "policy_wording.md",
          system: "Published policy documents",
          owner: "Legal",
          reviewed: "2026-09-01",
        },
      ],
    },
  };
  const steps: ClaimStepRequest[] = [
    {
      kind: "tool",
      message: "Searched approved policy files.",
      details: {
        name: "search_documents",
        input: { query: "Trip Delay" },
      },
    },
    {
      kind: "tool",
      message: "Looked up a term in the context gateway.",
      details: {
        name: "undercover-context___lookup_term",
        input: { term: "Trip Delay" },
      },
    },
    lookup,
    lookup,
    {
      kind: "memory_search",
      message: "Searched customer memory.",
      details: {
        query: "prior claim history",
        results: ["CLM-0990: Trip delay claim approved for $320.00."],
      },
    },
    {
      kind: "memory_save",
      message: "Saved sanitized customer notes.",
      details: {
        messages: [
          "Customer reported a flight cancellation and requested reimbursement for eligible prepaid expenses.",
          "Decision: approve eligible trip cancellation expenses under the policy.",
        ],
      },
    },
  ];
  const recorded: ClaimStepActivity[] = [];
  for (const step of steps) {
    recorded.push(await recordClaimStep(request, claimId, step));
  }
  return recorded;
}

export async function recordApproval(
  request: APIRequestContext,
  {
    claimId,
    payout,
    reason,
    section,
    maker = "Test handler",
  }: {
    claimId: string;
    payout: number;
    reason: string;
    section: string;
    maker?: string;
  },
): Promise<APIResponse> {
  const url = `http://127.0.0.1:8000/api/claims/${claimId}/payout`;
  const calculate = async (amount: number) =>
    request.post(url, {
      data: {
        benefit: "emergency_medical",
        lines: [
          {
            description: "Test expense",
            amount,
            quantity: 1,
            kind: "expense",
            nights: 0,
            hours: 0,
          },
        ],
        paid_by_others: 0,
      },
      headers: agentHeaders,
    });
  const claimResponse = await request.get(
    `http://127.0.0.1:8000/api/claims/${claimId}`,
    { headers: agentHeaders },
  );
  expect(claimResponse.ok()).toBe(true);
  const claimBundle = (await claimResponse.json()) as {
    plan: { deductible: number };
  };
  const calculationResponse = await calculate(
    payout + claimBundle.plan.deductible,
  );
  expect(calculationResponse.ok()).toBe(true);
  const calculation = (await calculationResponse.json()) as PayoutCalculation;
  expect(calculation.payout).toBe(payout);

  return request.post(`http://127.0.0.1:8000/api/claims/${claimId}/decision`, {
    data: {
      decision: "approve",
      payout: calculation.payout,
      reason,
      section,
      decided_by: maker,
    },
    headers: agentHeaders,
  });
}

export async function handlerHeaders(
  request: APIRequestContext,
): Promise<{ Authorization: string }> {
  const response = await request.post(
    "http://127.0.0.1:8005/auth/v1/token?grant_type=password",
    {
      data: {
        email: "dana.whitfield@undercover.example",
        password: "test-password",
      },
    },
  );
  if (!response.ok())
    throw new Error("Could not obtain the test handler token");
  const session = (await response.json()) as { access_token: string };
  return { Authorization: `Bearer ${session.access_token}` };
}
