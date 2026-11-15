import type { APIRequestContext, APIResponse, Page } from "@playwright/test";

export const AGENT_API_KEY = "undercover-e2e-agent-key-32-characters";
export const agentHeaders = { "X-API-Key": AGENT_API_KEY };

export async function mockClaimDocumentReads(
  page: Page,
  request: APIRequestContext,
): Promise<[APIResponse, APIResponse]> {
  const response = await request.get(
    "http://127.0.0.1:8000/api/claims/CLM-1001",
    { headers: agentHeaders },
  );
  if (!response.ok()) throw new Error("Could not load the test claim bundle");
  const claimBundle = (await response.json()) as Record<string, unknown>;
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
  await page.route("**/api/claims/CLM-1001", (route) =>
    route.fulfill({
      json: {
        ...claimBundle,
        documents_read: [
          {
            document: "claims_manual",
            section_id: "10",
            title: "Meals without receipts",
          },
          {
            document: "policy_wording",
            section_id: "3",
            title: "Trip Delay",
          },
        ],
      },
    }),
  );
  return [retiredSection, retiredSearch];
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
