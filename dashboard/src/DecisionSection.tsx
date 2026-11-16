import type { ClaimBundle, Handler } from "./api";
import { formatDateTime, formatMoney } from "./format";

export function DecisionSection({
  bundle,
  handler,
  busy,
  onSignOff,
}: Readonly<{
  bundle: ClaimBundle;
  handler: Handler;
  busy: boolean;
  onSignOff: () => Promise<void>;
}>) {
  const decision = bundle.decision;
  if (!decision) return null;

  const signOffLabel = busy ? "Signing off…" : "Sign off payout";

  return (
    <section className="panel decision-panel" aria-labelledby="decision-title">
      <div className="detail-heading">
        <div>
          <p className="eyebrow">UnderCover Agent</p>
          <h2 id="decision-title">Agent decision</h2>
        </div>
        <span className={`decision-kind decision-${decision.decision}`}>
          {decision.decision.charAt(0).toUpperCase() +
            decision.decision.slice(1)}
        </span>
      </div>
      <div className="decision-summary">
        <div>
          <span>Payout</span>
          <strong className="mono">{formatMoney(decision.payout)}</strong>
        </div>
        {bundle.paid_amount !== null ? (
          <div>
            <span>Payment recorded</span>
            <strong>Payment recorded: {formatMoney(bundle.paid_amount)}</strong>
          </div>
        ) : null}
      </div>
      {bundle.signed_off_by_name && bundle.signed_off_at ? (
        <p className="sign-off-details">
          Signed off by {bundle.signed_off_by_name} at{" "}
          {formatDateTime(bundle.signed_off_at)}
        </p>
      ) : null}
      {bundle.paid_at ? (
        <p className="payment-details">
          Paid at {formatDateTime(bundle.paid_at)}
        </p>
      ) : null}
      <p className="decision-reason">{decision.reason}</p>
      <p className="policy-citation">
        <strong>Policy section</strong>
        {decision.section}
      </p>
      <dl className="decision-record">
        <div>
          <dt>Decision maker</dt>
          <dd>{decision.decided_by}</dd>
        </div>
        <div>
          <dt>Decision time</dt>
          <dd>{formatDateTime(decision.decided_at)}</dd>
        </div>
        <div>
          <dt>Claims manual</dt>
          <dd>{decision.manual_version}</dd>
        </div>
      </dl>
      {decision.calculation ? (
        <section className="calculation" aria-label="Payout calculation">
          <h3>Calculation</h3>
          <ul>
            {decision.calculation.lines.map((line, index) => (
              <li key={`${line.step}-${String(index)}`}>
                <span>{line.step}</span>
                <strong className="mono">{formatMoney(line.amount)}</strong>
              </li>
            ))}
          </ul>
          <p>
            <span>Calculated payout</span>
            <strong className="mono">
              {formatMoney(decision.calculation.payout)}
            </strong>
          </p>
        </section>
      ) : null}
      {bundle.evaluation_run_id === null &&
      bundle.claim.status === "waiting_for_sign_off" &&
      bundle.paid_amount === null ? (
        <div className="sign-off">
          <div>
            <h3>Handler sign-off</h3>
            <p>
              {bundle.gateway_refused
                ? "The Gateway refused payment. A handler must sign off before payment can be recorded."
                : "This payout exceeded the approval limit when decided. A handler must sign off before payment can be recorded."}
            </p>
            {bundle.gateway_refusal_reason ? (
              <p className="gateway-reason">
                <strong>Gateway response:</strong>{" "}
                {bundle.gateway_refusal_reason}
              </p>
            ) : null}
          </div>
          <p>
            Signed in as <strong>{handler.name}</strong>.
          </p>
          {handler.can_approve_over_limit ? (
            <button
              type="button"
              className="primary-action"
              disabled={busy}
              onClick={() => {
                void onSignOff();
              }}
            >
              {signOffLabel}
            </button>
          ) : (
            <p role="note">
              You cannot sign off this payout. Only a handler who can approve
              over-limit payouts may release it.
            </p>
          )}
          <p className="fine-print">
            The pilot records payment only; no money is transferred.
          </p>
        </div>
      ) : null}
    </section>
  );
}
