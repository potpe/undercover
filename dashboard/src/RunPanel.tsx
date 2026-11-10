import type { DispatchRun, RunClaim } from "./api";
import { formatModelCost, formatMoney, formatSeconds } from "./format";

const claimLabels: Record<RunClaim["status"], string> = {
  waiting: "Waiting",
  in_progress: "In progress",
  done: "Done",
  left_for_handler: "Left for handler",
};

function ClaimLinks({
  claimIds,
  onSelect,
}: Readonly<{ claimIds: string[]; onSelect: (claimId: string) => void }>) {
  return (
    <ul>
      {claimIds.map((claimId) => (
        <li key={claimId}>
          <button
            type="button"
            className="claim-link mono"
            aria-label={`View claim ${claimId}`}
            onClick={() => {
              onSelect(claimId);
            }}
          >
            {claimId}
          </button>
        </li>
      ))}
    </ul>
  );
}

function DispatchClaims({
  claims,
  onSelect,
}: Readonly<{ claims: RunClaim[]; onSelect: (claimId: string) => void }>) {
  if (!claims.length) return <p className="muted">No eligible claims.</p>;
  return (
    <ol className="dispatch-list">
      {claims.map((claim) => (
        <li key={claim.claim_id}>
          <span className="dispatch-position mono">{claim.position}</span>
          <div className="dispatch-content">
            <button
              type="button"
              className="claim-link mono"
              aria-label={`View claim ${claim.claim_id}`}
              onClick={() => {
                onSelect(claim.claim_id);
              }}
            >
              {claim.claim_id}
            </button>
            {claim.result && !claim.error ? <span>{claim.result}</span> : null}
            {claim.error ? (
              <span className="dispatch-error">{claim.error}</span>
            ) : null}
          </div>
          <div className="dispatch-meta">
            {claim.attempts ? <span>{claim.attempts} attempts</span> : null}
            <span className={`dispatch-status dispatch-${claim.status}`}>
              {claimLabels[claim.status]}
            </span>
          </div>
        </li>
      ))}
    </ol>
  );
}

function DispatchRecordSummary({
  run,
  onSelect,
}: Readonly<{ run: DispatchRun; onSelect: (claimId: string) => void }>) {
  const summary = run.summary;
  if (!summary) return null;
  return (
    <div className="run-summary">
      <p>
        {summary.approved} approved, {summary.declined} declined,{" "}
        {summary.referred} referred.
      </p>
      {summary.waiting_for_sign_off.length ? (
        <div>
          <h3>Waiting for sign-off</h3>
          <ClaimLinks
            claimIds={summary.waiting_for_sign_off.map(
              (claim) => claim.claim_id,
            )}
            onSelect={onSelect}
          />
          <ul>
            {summary.waiting_for_sign_off.map((claim) => (
              <li key={`${claim.claim_id}-payout`} className="mono">
                {claim.claim_id}: {formatMoney(claim.payout)}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {summary.left_for_handler.length ? (
        <div>
          <h3>Left for a handler</h3>
          <ClaimLinks claimIds={summary.left_for_handler} onSelect={onSelect} />
        </div>
      ) : null}
      {summary.in_progress.length ? (
        <div>
          <h3>Still in progress for the next dispatch</h3>
          <ClaimLinks claimIds={summary.in_progress} onSelect={onSelect} />
        </div>
      ) : null}
      {summary.error ? <p role="alert">{summary.error}</p> : null}
    </div>
  );
}

export function RunPanel({
  run,
  concurrency,
  busy,
  error,
  onConcurrencyChange,
  onRun,
  onSelect,
}: Readonly<{
  run: DispatchRun | null;
  concurrency: string;
  busy: boolean;
  error: string;
  onConcurrencyChange: (concurrency: string) => void;
  onRun: () => Promise<void>;
  onSelect: (claimId: string) => void;
}>) {
  const active = run?.status === "in_progress";
  const done =
    run?.claims.filter((claim) => claim.status === "done").length ?? 0;

  return (
    <section className="panel run-panel" aria-labelledby="run-title">
      <div className="detail-heading">
        <div>
          <p className="eyebrow">Autonomous work</p>
          <h2 id="run-title">Current dispatch</h2>
        </div>
        {run ? (
          <span className={`run-state run-${run.status}`}>
            {run.status.replaceAll("_", " ")}
          </span>
        ) : null}
      </div>
      <div className="run-controls">
        <label htmlFor="concurrency">Agents at once</label>
        <input
          id="concurrency"
          type="number"
          min={1}
          max={25}
          step={1}
          value={concurrency}
          disabled={busy || active}
          onChange={(event) => {
            onConcurrencyChange(event.target.value);
          }}
        />
        <button
          type="button"
          className="primary-action"
          disabled={busy || active}
          onClick={() => {
            void onRun();
          }}
        >
          {busy ? "Starting…" : "Start dispatch"}
        </button>
      </div>
      {error ? <p role="alert">{error}</p> : null}
      {run ? (
        <div className="run-details">
          <div className="run-meta">
            <span className="mono">Dispatch {run.run_id}</span>
            <span>
              {done} of {run.claims.length} done
            </span>
            <span>{run.concurrency} agents at once</span>
            <span className="mono">{run.model_name}</span>
            <span>Reasoning: {run.reasoning_effort ?? "none"}</span>
          </div>
          <div className="run-metrics">
            <span>Model cost: {formatModelCost(run.model_cost)}</span>
            <span>Active work: {formatSeconds(run.active_seconds)}</span>
            <span>Run time: {formatSeconds(run.wall_seconds)}</span>
          </div>
          <DispatchRecordSummary run={run} onSelect={onSelect} />
          <DispatchClaims claims={run.claims} onSelect={onSelect} />
        </div>
      ) : (
        <p className="muted">
          No dispatch yet. Choose the number of agents and start one.
        </p>
      )}
    </section>
  );
}
