import { useEffect, useState } from "react";
import {
  getClaim,
  listHandlers,
  payClaim,
  runClaim,
  signOffClaim,
  type ClaimBundle,
  type Handler,
} from "./api";
import { ActivityPanel } from "./ActivityPanel";
import { ClaimDetails } from "./ClaimDetails";
import { ClaimOverview } from "./ClaimOverview";
import { DecisionSection } from "./DecisionSection";
import { DocumentSections } from "./DocumentSections";

function errorMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "The action could not be completed.";
}

export function ClaimWorkspace({
  claimId,
  onBack,
}: Readonly<{ claimId: string; onBack: () => void }>) {
  const [bundle, setBundle] = useState<ClaimBundle | null>(null);
  const [handlers, setHandlers] = useState<Handler[]>([]);
  const [handlerId, setHandlerId] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    getClaim(claimId)
      .then((data) => {
        if (active) setBundle(data);
      })
      .catch(() => {
        if (active) setLoadError(true);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [claimId]);

  useEffect(() => {
    if (bundle?.claim.status !== "in_progress") return;
    const timer = window.setInterval(() => {
      getClaim(claimId)
        .then((data) => {
          setBundle(data);
        })
        .catch((error: unknown) => {
          setActionError(errorMessage(error));
        });
    }, 500);
    return () => {
      window.clearInterval(timer);
    };
  }, [claimId, bundle?.claim.status]);

  useEffect(() => {
    if (bundle?.claim.status !== "waiting_for_sign_off" || handlers.length)
      return;
    listHandlers()
      .then((data) => {
        setHandlers(data);
      })
      .catch((error: unknown) => {
        setActionError(errorMessage(error));
      });
  }, [bundle?.claim.status, handlers.length]);

  async function handleRun() {
    setBusy(true);
    setActionError("");
    try {
      await runClaim(claimId, `dashboard-${crypto.randomUUID()}`);
      setBundle(await getClaim(claimId));
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function handleSignOff() {
    if (!bundle?.decision) return;
    setBusy(true);
    setActionError("");
    try {
      if (!bundle.signed_off_by) await signOffClaim(claimId, handlerId);
      setBundle(await payClaim(claimId, bundle.decision.payout));
    } catch (error) {
      setActionError(errorMessage(error));
      setBundle(await getClaim(claimId));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="workspace">
      <button
        type="button"
        className="back-button"
        aria-label="Back to queue"
        onClick={() => {
          onBack();
        }}
      >
        ← Back to queue
      </button>
      {loading ? <p role="status">Loading claim…</p> : null}
      {loadError ? <p role="alert">We could not load this claim.</p> : null}
      {bundle ? (
        <>
          <ClaimOverview
            bundle={bundle}
            busy={busy}
            actionError={actionError}
            onRun={handleRun}
          />
          <DecisionSection
            bundle={bundle}
            handlers={handlers}
            handlerId={handlerId}
            busy={busy}
            onHandlerChange={setHandlerId}
            onSignOff={handleSignOff}
          />
          <ActivityPanel
            activity={bundle.activity}
            status={bundle.claim.status}
          />
          <DocumentSections reads={bundle.documents_read} />
          <ClaimDetails bundle={bundle} />
        </>
      ) : null}
    </div>
  );
}
