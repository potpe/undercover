import type { ImprovementRound } from "./improvement-api";
import type { VersionScores } from "./api";
import {
  formatMoney as formatCurrency,
  formatModelCost,
  formatPercent,
} from "./format";

import { stageLabels } from "./round-stage";

export function RoundScores({
  before,
  after,
}: Readonly<{ before: VersionScores | null; after: VersionScores | null }>) {
  return (
    <table>
      <caption>Held-out scores before and after</caption>
      <thead>
        <tr>
          <th>Measure</th>
          <th className="money">Before</th>
          <th className="money">After</th>
        </tr>
      </thead>
      <tbody>
        {(
          [
            ["Accuracy", "accuracy"],
            ["Grade (0–3)", "grade"],
            ["Model cost / claim (USD)", "cost_per_claim"],
            ["Active time / claim (s)", "seconds_per_claim"],
          ] as const
        ).map(([label, key]) => (
          <tr key={key}>
            <th>{label}</th>
            <td className="money mono">{scoreValue(before, key)}</td>
            <td className="money mono">{scoreValue(after, key)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function scoreValue(scores: VersionScores | null, key: keyof VersionScores) {
  const value = scores?.[key];
  if (value === null || value === undefined) return "Not available";
  if (key === "accuracy") return formatPercent(value);
  if (key === "cost_per_claim") return formatModelCost(value);
  return value.toFixed(2);
}

export function RoundHistory({ round }: Readonly<{ round: ImprovementRound }>) {
  return (
    <section className="panel detail-section">
      <div className="round-heading">
        <h2>Round {round.round_id}</h2>
        <span className={`status round-${round.status}`}>
          {stageLabels[round.stage]}
        </span>
      </div>
      <p>
        Recipe {round.recipe_version} · Starting version{" "}
        {round.starting_version} ·{" "}
        {round.candidate_version === null
          ? "No version tried"
          : `Tried version ${String(round.candidate_version)}`}
      </p>
      <p>
        Spent <span className="mono">{formatModelCost(round.cost)}</span> of{" "}
        {formatCurrency(round.settings.spending_limit)}
      </p>
      {round.verdict ? (
        <p>
          <strong>{round.verdict === "kept" ? "Kept" : "Rolled back"}</strong>:{" "}
          {round.verdict_reason}
        </p>
      ) : (
        <p className="muted">No verdict recorded.</p>
      )}
      {round.finish_reason ? <p>Finished: {round.finish_reason}</p> : null}
      <RoundScores
        before={round.held_out_before}
        after={round.held_out_after}
      />
      <details>
        <summary>Round settings and decisions</summary>
        <p>
          Started by {round.created_by} at{" "}
          {new Date(round.created_at).toLocaleString()}. Evaluations use{" "}
          {round.settings.evaluation_concurrency} agents at once.
        </p>
        <pre className="configuration-text">
          {JSON.stringify(round.settings, null, 2)}
        </pre>
        {round.proposals.length ? (
          round.proposals.map((proposal) => (
            <article key={proposal.proposal_id}>
              <h3>
                {proposal.kind === "configuration" ? "Configuration" : "Recipe"}{" "}
                proposal {proposal.proposal_id}: {proposal.status}
              </h3>
              <p>{proposal.reason}</p>
              <p>
                {proposal.decided_by ?? "No handler decision"}{" "}
                {proposal.decided_at
                  ? new Date(proposal.decided_at).toLocaleString()
                  : ""}
              </p>
              <p>Proposed {new Date(proposal.proposed_at).toLocaleString()}</p>
              <p>{proposal.decision_reason}</p>
            </article>
          ))
        ) : (
          <p>No proposals recorded.</p>
        )}
      </details>
    </section>
  );
}
