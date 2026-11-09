import type { ClaimBundle, Handler } from "./api";
import { formatDateTime, formatMoney } from "./format";

export function DecisionSection({
  bundle,
  handlers,
  handlerId,
  busy,
  onHandlerChange,
  onSignOff,
}: Readonly<{
  bundle: ClaimBundle;
  handlers: Handler[];
  handlerId: string;
  busy: boolean;
  onHandlerChange: (handlerId: string) => void;
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
        {decision.policy_section}
      </p>
      {bundle.claim.status === "waiting_for_sign_off" &&
      bundle.paid_amount === null ? (
        <div className="sign-off">
          <div>
            <h3>Handler sign-off</h3>
            <p>
              This payout is over $1,000.00. Payment is blocked until a handler
              signs off.
            </p>
          </div>
          <label htmlFor="handler">Signing handler</label>
          <select
            id="handler"
            value={handlerId}
            disabled={busy || Boolean(bundle.signed_off_by)}
            onChange={(event) => {
              onHandlerChange(event.target.value);
            }}
          >
            <option value="">Choose a handler</option>
            {handlers.map((handler) => (
              <option key={handler.handler_id} value={handler.handler_id}>
                {handler.name}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="primary-action"
            disabled={busy || !handlerId}
            onClick={() => {
              void onSignOff();
            }}
          >
            {signOffLabel}
          </button>
          <p className="fine-print">
            The pilot records payment only; no money is transferred.
          </p>
        </div>
      ) : null}
    </section>
  );
}
