import { useCallback, useEffect, useState } from "react";
import {
  getEvaluation,
  listEvaluations,
  startEvaluation,
  type EvaluationClaim,
  type EvaluationRun,
} from "./api";
import {
  formatModelCost,
  formatMoney,
  formatPercent,
  formatSeconds,
} from "./format";

function comparisonResult(claim: EvaluationClaim): string {
  if (claim.right) return "Match";
  return claim.decision_match ? "Payout miss" : "Decision miss";
}

function Measures({ evaluation }: Readonly<{ evaluation: EvaluationRun }>) {
  const measures = evaluation.measures;
  return (
    <div className="evaluation-measures">
      <span>
        Decision accuracy: {formatPercent(measures.decision_accuracy)}
      </span>
      <span>Accuracy: {formatPercent(measures.accuracy)}</span>
      <span>Cost of mistakes: {formatMoney(measures.cost_of_mistakes)}</span>
      <span>Straight through: {formatPercent(measures.straight_through)}</span>
      <span>Model cost: {formatModelCost(measures.model_cost)}</span>
      <span>
        Model cost per claim: {formatModelCost(measures.model_cost_per_claim)}
      </span>
      <span>Time per claim: {formatSeconds(measures.seconds_per_claim)}</span>
      <span>Slowest claim: {formatSeconds(measures.slowest_claim)}</span>
      <span>Whole run: {formatSeconds(measures.seconds)}</span>
    </div>
  );
}

function EvaluationDetail({
  evaluation,
  onSelectClaim,
}: Readonly<{
  evaluation: EvaluationRun;
  onSelectClaim: (claimId: string) => void;
}>) {
  return (
    <section className="panel" aria-labelledby="evaluation-detail-title">
      <p className="eyebrow">Handler comparison</p>
      <h2 id="evaluation-detail-title">
        Evaluation {evaluation.run_id} claims
      </h2>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th scope="col">Claim</th>
              <th scope="col">Handler</th>
              <th scope="col">Agent</th>
              <th scope="col">Reason</th>
              <th scope="col">Cost and time</th>
              <th scope="col">Result</th>
            </tr>
          </thead>
          <tbody>
            {evaluation.claim_comparisons.map((claim) => (
              <tr
                key={claim.claim_id}
                className={claim.right ? "evaluation-match" : "evaluation-miss"}
              >
                <td data-label="Claim">
                  <button
                    type="button"
                    className="claim-link mono"
                    aria-label={`View claim ${claim.claim_id}`}
                    onClick={() => {
                      onSelectClaim(claim.claim_id);
                    }}
                  >
                    {claim.claim_id}
                  </button>
                </td>
                <td data-label="Handler">
                  {claim.handler_decision}: {formatMoney(claim.handler_payout)}
                </td>
                <td data-label="Agent">
                  {claim.agent_decision}: {formatMoney(claim.agent_payout)}
                </td>
                <td data-label="Reason">{claim.agent_reason}</td>
                <td data-label="Cost and time" className="mono">
                  {formatModelCost(claim.model_cost)};{" "}
                  {formatSeconds(claim.active_seconds)}
                </td>
                <td data-label="Result">{comparisonResult(claim)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function Scoreboard({
  onSelectClaim,
}: Readonly<{ onSelectClaim: (claimId: string) => void }>) {
  const [evaluations, setEvaluations] = useState<EvaluationRun[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [selected, setSelected] = useState<EvaluationRun | null>(null);
  const [concurrency, setConcurrency] = useState("3");
  const [evaluationSet, setEvaluationSet] = useState<"evaluation" | "held_out">(
    "evaluation",
  );
  const [tiny, setTiny] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    const data = await listEvaluations();
    setEvaluations(data);
    if (selectedId !== null) setSelected(await getEvaluation(selectedId));
    setLoading(false);
  }, [selectedId]);

  useEffect(() => {
    let active = true;
    void Promise.resolve()
      .then(refresh)
      .catch((cause: unknown) => {
        if (active) {
          setError(
            cause instanceof Error
              ? cause.message
              : "Could not load evaluations.",
          );
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [refresh]);

  const running = evaluations.some(
    (evaluation) => evaluation.status === "in_progress",
  );
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => {
      refresh().catch((cause: unknown) => {
        setError(
          cause instanceof Error
            ? cause.message
            : "Could not refresh evaluations.",
        );
      });
    }, 500);
    return () => {
      window.clearInterval(timer);
    };
  }, [refresh, running]);

  async function handleStart() {
    const agents = Number(concurrency);
    if (!Number.isInteger(agents) || agents < 1 || agents > 25) {
      setError("Agents at once must be a whole number from 1 to 25.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await startEvaluation(agents, evaluationSet, tiny);
      setSelectedId(null);
      setSelected(null);
      await refresh();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Could not start evaluation.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function selectEvaluation(runId: number) {
    setError("");
    try {
      setSelected(await getEvaluation(runId));
      setSelectedId(runId);
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Could not load evaluation.",
      );
    }
  }

  return (
    <div className="scoreboard-layout">
      <section className="panel" aria-labelledby="scoreboard-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Quality, cost and time</p>
            <h1 id="scoreboard-title">Scoreboard</h1>
            <p>Evaluate the claim agent against the handlers' decisions.</p>
          </div>
        </div>
        <div className="scoreboard-controls">
          <label htmlFor="evaluation-set">Evaluation set</label>
          <select
            id="evaluation-set"
            value={evaluationSet}
            disabled={busy || running}
            onChange={(event) => {
              setEvaluationSet(event.target.value as "evaluation" | "held_out");
            }}
          >
            <option value="evaluation">Evaluation set</option>
            <option value="held_out">Held-out set</option>
          </select>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={tiny}
              disabled={busy || running}
              onChange={(event) => {
                setTiny(event.target.checked);
              }}
            />
            Only the five tiny claims
          </label>
          <label htmlFor="evaluation-concurrency">Agents at once</label>
          <input
            id="evaluation-concurrency"
            type="number"
            min={1}
            max={25}
            step={1}
            value={concurrency}
            disabled={busy || running}
            onChange={(event) => {
              setConcurrency(event.target.value);
            }}
          />
          <button
            type="button"
            className="primary-action"
            disabled={busy || running}
            onClick={() => {
              void handleStart();
            }}
          >
            {busy ? "Starting…" : "Start evaluation"}
          </button>
        </div>
        {error ? <p role="alert">{error}</p> : null}
      </section>
      {loading ? <p role="status">Loading evaluations…</p> : null}
      {!loading && !evaluations.length ? (
        <p className="muted">No evaluations yet.</p>
      ) : null}
      {evaluations.map((evaluation) => (
        <section
          className="panel evaluation-card"
          key={evaluation.run_id}
          aria-labelledby={`evaluation-${String(evaluation.run_id)}`}
        >
          <div className="detail-heading">
            <div>
              <h2 id={`evaluation-${String(evaluation.run_id)}`}>
                Evaluation {evaluation.run_id}
              </h2>
              <p>
                {evaluation.evaluation_set === "held_out"
                  ? "Held-out set"
                  : "Evaluation set"}
                {evaluation.tiny ? ", tiny" : ", whole"};{" "}
                {evaluation.concurrency} agents at once
              </p>
            </div>
            <span className={`run-state run-${evaluation.status}`}>
              {evaluation.status.replaceAll("_", " ")}
            </span>
          </div>
          <p className="mono">
            {evaluation.model_name}; reasoning:{" "}
            {evaluation.reasoning_effort ?? "none"}
          </p>
          <Measures evaluation={evaluation} />
          <button
            type="button"
            className="secondary-action"
            onClick={() => {
              void selectEvaluation(evaluation.run_id);
            }}
          >
            View evaluation {evaluation.run_id}
          </button>
        </section>
      ))}
      {selected ? (
        <EvaluationDetail evaluation={selected} onSelectClaim={onSelectClaim} />
      ) : null}
    </div>
  );
}
