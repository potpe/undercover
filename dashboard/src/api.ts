export type ClaimStatus =
  | "waiting"
  | "in_progress"
  | "waiting_for_sign_off"
  | "decided"
  | "left_for_handler";
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

export interface ClaimItem {
  description: string;
  category: string;
  amount: number;
}

export interface ClaimDocument {
  name: string;
  type: string;
  content: string;
}

export interface Claim {
  claim_id: string;
  policy_id: string;
  customer_id: string;
  claim_type: string;
  submitted_on: string;
  incident_date: string;
  incident_location: string;
  summary: string;
  story: string;
  items: ClaimItem[];
  amount_claimed: number;
  documents: ClaimDocument[];
  status: ClaimStatus;
}

export interface Policy {
  policy_id: string;
  customer_id: string;
  plan: string;
  add_ons: string[];
  purchased_on: string;
  sold_through: string;
  trip: {
    destination: string;
    departs_on: string;
    returns_on: string;
    trip_cost: number;
  };
  travelers: string[];
  status: string;
}

export interface Plan {
  plan: string;
  name: string;
  trip_cancellation_max: number;
  trip_delay_waiting_hours: number;
  trip_delay_max: number;
  baggage_delay_waiting_hours: number;
  baggage_delay_max: number;
  baggage_max: number;
  per_item_limit: number;
  passport_max: number;
  emergency_medical_max: number;
  deductible: number;
}

export interface PreviousClaim {
  claim_id: string;
  filed_on: string;
  incident_date: string;
  claim_type: string;
  summary: string;
  outcome: string;
  amount_paid: number;
}

export interface Customer {
  customer_id: string;
  name: string;
  email: string;
  phone: string;
  date_of_birth: string;
  address: { street: string; city: string; state: string; zip: string };
  customer_since: string;
  previous_claims: PreviousClaim[];
}

export interface Decision {
  decision: DecisionKind;
  payout: number;
  reason: string;
  policy_section: string;
}

export interface Activity {
  id: number;
  message: string;
  created_at: string;
}

export interface DocumentRead {
  document: "policy_wording" | "claims_manual";
  section_id: string;
  title: string;
}

export interface RunClaim {
  claim_id: string;
  position: number;
  status: "waiting" | "in_progress" | "done" | "left_for_handler";
  attempts: number;
  result: string | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  model_cost: number;
  active_seconds: number;
}

export interface DispatchSummary {
  approved: number;
  declined: number;
  referred: number;
  waiting_for_sign_off: { claim_id: string; payout: number }[];
  left_for_handler: string[];
  in_progress: string[];
  error: string | null;
}

export interface DispatchRun {
  run_id: number;
  kind: "dispatch" | "evaluation";
  concurrency: number;
  status: "in_progress" | "completed" | "stopped";
  model_name: string;
  reasoning_effort: string | null;
  evaluation_set: string | null;
  tiny: boolean | null;
  summary: DispatchSummary | null;
  created_at: string;
  completed_at: string | null;
  stopped_at: string | null;
  claims: RunClaim[];
  model_cost: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  active_seconds: number;
  wall_seconds: number;
}

export interface ClaimBundle {
  claim: Claim;
  policy: Policy;
  customer: Customer;
  plan: Plan;
  add_ons: { add_on: string; name: string; effect: string }[];
  decision: Decision | null;
  paid_amount: number | null;
  signed_off_by: string | null;
  signed_off_by_name: string | null;
  signed_off_at: string | null;
  paid_at: string | null;
  agent_thread_id: string | null;
  work_run_id: number | null;
  failed_attempts: number;
  evaluation_run_id: number | null;
  activity: Activity[];
  documents_read: DocumentRead[];
}

export interface Handler {
  handler_id: string;
  name: string;
  role: string;
  email: string;
  can_approve_over_limit: boolean;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    const body = (await response.json()) as { detail?: unknown };
    const detail = typeof body.detail === "string" ? body.detail : null;
    throw new Error(
      detail ?? `Request failed with status ${String(response.status)}`,
    );
  }
  return (await response.json()) as T;
}

function postJson<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function listClaims(): Promise<ClaimSummary[]> {
  return request<ClaimSummary[]>("/api/claims");
}

export function getClaim(claimId: string): Promise<ClaimBundle> {
  return request<ClaimBundle>(`/api/claims/${claimId}`);
}

export function getInstructions(): Promise<{ claim: string }> {
  return request<{ claim: string }>("/api/agent/instructions");
}

export function getCurrentDispatch(): Promise<DispatchRun | null> {
  return request<DispatchRun | null>("/api/dispatches/current");
}

export function startDispatch(concurrency: number): Promise<DispatchRun> {
  return postJson("/api/dispatches", { concurrency });
}

export function listHandlers(): Promise<Handler[]> {
  return request<Handler[]>("/api/handlers");
}

export function runClaim(
  claimId: string,
  threadId: string,
): Promise<{ claim_id: string; status: "in_progress" }> {
  return postJson(`/api/claims/${claimId}/run`, { thread_id: threadId });
}

export function signOffClaim(
  claimId: string,
  handlerId: string,
): Promise<ClaimBundle> {
  return postJson(`/api/claims/${claimId}/sign-off`, { handler_id: handlerId });
}
