import { useState } from "react";
import type { ConfigurationVersion } from "./api";
import type { ImprovementProposal } from "./improvement-api";
import { DiffView } from "./DiffView";
import { ConfirmDialog } from "./ConfirmDialog";

export function ProposalReview({
  proposal,
  previous,
  busy,
  onDecision,
}: Readonly<{
  proposal: ImprovementProposal;
  previous: ConfigurationVersion | undefined;
  busy: boolean;
  onDecision: (decision: "approve" | "reject", reason: string) => Promise<void>;
}>) {
  const [reason, setReason] = useState("");
  const [decision, setDecision] = useState<"approve" | "reject" | null>(null);
  return (
    <section className="panel detail-section">
      <p className="eyebrow">Handler review</p>
      <h2>
        {proposal.kind === "configuration"
          ? "Proposed configuration"
          : "Proposed recipe"}
      </h2>
      <p>{proposal.reason}</p>
      {proposal.kind === "configuration" ? (
        <div className="proposal-models">
          <p>
            Before:{" "}
            <span className="mono">
              {previous?.model_name ?? "See Configuration"} ·{" "}
              {previous?.reasoning_effort ?? "Not available"}
            </span>
          </p>
          <p>
            After:{" "}
            <span className="mono">
              {proposal.payload.model} · {proposal.payload.reasoning_effort}
            </span>
          </p>
        </div>
      ) : null}
      <DiffView lines={proposal.diff} />
      <label htmlFor={`rejection-${String(proposal.proposal_id)}`}>
        Rejection reason
      </label>
      <textarea
        id={`rejection-${String(proposal.proposal_id)}`}
        value={reason}
        onChange={(event) => {
          setReason(event.target.value);
        }}
        disabled={busy}
        rows={3}
      />
      <p className="muted">
        A rejection returns this reason to the improver so it can propose again.
      </p>
      <div className="run-controls">
        <button
          type="button"
          className="primary-action"
          disabled={busy}
          onClick={() => {
            setDecision("approve");
          }}
        >
          Approve proposal
        </button>
        <button
          type="button"
          className="secondary-action"
          disabled={busy || !reason.trim()}
          onClick={() => {
            setDecision("reject");
          }}
        >
          Reject proposal
        </button>
      </div>
      {decision ? (
        <ConfirmDialog
          title={
            decision === "approve"
              ? "Approve this proposal?"
              : "Reject this proposal?"
          }
          description={
            decision === "approve"
              ? "The improver will resume and apply this version. A configuration still needs to pass its evaluations before it becomes current."
              : reason
          }
          confirmLabel={
            decision === "approve" ? "Confirm approval" : "Confirm rejection"
          }
          onCancel={() => {
            setDecision(null);
          }}
          onConfirm={() => {
            setDecision(null);
            void onDecision(decision, reason.trim());
          }}
        />
      ) : null}
    </section>
  );
}
