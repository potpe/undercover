import {
  request,
  type DiffLine,
  type PlatformSettings,
  type RecipeHistory,
  type VersionScores,
} from "./api";
export { getConfiguration } from "./api";

interface ProposalAudit {
  proposal_id: number;
  round_id: number;
  reason: string;
  diff: DiffLine[];
  proposed_at: string;
  status: "pending" | "approved" | "rejected" | "applied";
  decided_by: string | null;
  decided_at: string | null;
  decision_reason: string | null;
}

export type ImprovementProposal = ProposalAudit &
  (
    | {
        kind: "configuration";
        payload: {
          instructions: string;
          model: string;
          reasoning_effort: string;
          reason: string;
        };
      }
    | { kind: "recipe"; payload: { recipe: string; reason: string } }
  );

export interface ImprovementRound {
  round_id: number;
  thread_id: string;
  status: "open" | "finished" | "stopped";
  stage:
    | "baseline"
    | "working"
    | "configuration_approval"
    | "evaluating"
    | "verdict"
    | "recipe_approval"
    | "finished"
    | "stopped";
  recipe_version: number;
  settings: PlatformSettings;
  starting_version: number;
  candidate_version: number | null;
  held_out_before: VersionScores | null;
  held_out_after: VersionScores | null;
  verdict: "kept" | "rolled_back" | null;
  verdict_reason: string | null;
  finish_reason: string | null;
  progress: number;
  cost: number;
  created_by: string;
  created_at: string;
  finished_at: string | null;
  stopped_at: string | null;
  proposals: ImprovementProposal[];
}

export function listRounds(): Promise<ImprovementRound[]> {
  return request("/api/rounds", { headers: {} });
}
export function getRecipes(): Promise<RecipeHistory> {
  return request("/api/recipes", { headers: {} });
}
function postRound(path: string, body: unknown): Promise<ImprovementRound> {
  return request(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
export function startRound(): Promise<ImprovementRound> {
  return postRound("/api/rounds", {});
}
export function stopRound(roundId: number): Promise<ImprovementRound> {
  return postRound(`/api/rounds/${String(roundId)}/stop`, {});
}
export function decideProposal(
  roundId: number,
  proposalId: number,
  decision: "approve" | "reject",
  reason: string,
): Promise<ImprovementRound> {
  return postRound(`/api/rounds/${String(roundId)}/decision`, {
    proposal_id: proposalId,
    decision,
    reason,
  });
}
