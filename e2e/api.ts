import type { APIRequestContext } from "@playwright/test";

export const AGENT_API_KEY = "undercover-e2e-agent-key-32-characters";
export const agentHeaders = { "X-API-Key": AGENT_API_KEY };

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
