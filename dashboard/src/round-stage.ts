import type { ImprovementRound } from "./improvement-api";
import { formatMoney, formatModelCost } from "./format";

export const stageLabels = {
  baseline: "First evaluations",
  working: "Improver working",
  configuration_approval: "Configuration waiting for approval",
  evaluating: "Candidate evaluations",
  verdict: "Verdict",
  recipe_approval: "Recipe waiting for approval",
  finished: "Finished",
  stopped: "Stopped",
};

export function roundProgress(round: ImprovementRound | undefined) {
  if (!round) return "No round is open.";
  let stage = stageLabels[round.stage];
  if (round.stage === "baseline" || round.stage === "evaluating")
    stage += ` · ${String(round.progress)} of 3`;
  return `${stage} · Spent ${formatModelCost(round.cost)} of ${formatMoney(round.settings.spending_limit)}`;
}
