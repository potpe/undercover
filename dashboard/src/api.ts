export type ClaimStatus =
  "waiting" | "in_progress" | "waiting_for_sign_off" | "decided";
export type DecisionKind = "approve" | "decline" | "refer";

export interface ClaimSummary {
  claim_id: string;
  customer_name: string;
  claim_type: string;
  summary: string;
  amount_claimed: number;
  status: ClaimStatus;
  decision: DecisionKind | null;
  payout: number | null;
}

export async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok)
    throw new Error(`Request failed with status ${String(response.status)}`);
  return (await response.json()) as T;
}

export function listClaims(): Promise<ClaimSummary[]> {
  return getJson<ClaimSummary[]>("/api/claims");
}

export function getInstructions(): Promise<{ content: string }> {
  return getJson<{ content: string }>("/api/agent/instructions");
}
