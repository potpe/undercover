import type { ClaimBundle } from "./api";
import { formatMoney } from "./format";
import { StatusBadge } from "./StatusBadge";

export function ClaimOverview({
  bundle,
  busy,
  actionError,
  onRun,
}: Readonly<{
  bundle: ClaimBundle;
  busy: boolean;
  actionError: string;
  onRun: () => Promise<void>;
}>) {
  const status = bundle.claim.status;

  return (
    <section className="panel claim-overview">
      <div className="claim-title-row">
        <div>
          <p className="eyebrow mono">{bundle.claim.claim_id}</p>
          <h1>{bundle.claim.summary}</h1>
          <p>
            {bundle.customer.name} · {formatMoney(bundle.claim.amount_claimed)}{" "}
            claimed
          </p>
        </div>
        <StatusBadge status={status} />
      </div>
      {bundle.evaluation_run_id !== null ? (
        <p className="evaluation-copy">
          Evaluation copy · Evaluation {bundle.evaluation_run_id}
        </p>
      ) : null}
      {status === "waiting" && bundle.evaluation_run_id === null ? (
        <button
          type="button"
          className="primary-action"
          disabled={busy}
          onClick={() => {
            void onRun();
          }}
        >
          {busy ? "Starting agent…" : "Run agent"}
        </button>
      ) : null}
      {status === "in_progress" ? (
        <p role="status" className="working-message">
          The agent is working. Activity updates automatically.
        </p>
      ) : null}
      {actionError ? <p role="alert">{actionError}</p> : null}
    </section>
  );
}
