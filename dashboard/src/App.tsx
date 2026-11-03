import { useEffect, useState } from "react";
import { getInstructions, listClaims, type ClaimSummary } from "./api";
import { ClaimWorkspace } from "./ClaimWorkspace";
import { formatClaimType, formatMoney } from "./format";
import { StatusBadge } from "./StatusBadge";

type View = "queue" | "instructions";

function QueueView({
  onSelect,
}: Readonly<{ onSelect: (claimId: string) => void }>) {
  const [claims, setClaims] = useState<ClaimSummary[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");

  useEffect(() => {
    let active = true;
    listClaims()
      .then((data) => {
        if (active) {
          setClaims(data);
          setState("ready");
        }
      })
      .catch(() => {
        if (active) setState("error");
      });
    return () => {
      active = false;
    };
  }, []);

  if (state === "loading") return <p role="status">Loading claims queue…</p>;
  if (state === "error") {
    return (
      <p role="alert">
        We could not load the claims queue. Check that the backend is running.
      </p>
    );
  }

  return (
    <section className="panel queue-panel" aria-labelledby="queue-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Claims operations</p>
          <h1 id="queue-title">Claims queue</h1>
          <p>Review the queue and see where each claim stands.</p>
        </div>
        <span className="count">{claims.length} claims</span>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th scope="col">Claim</th>
              <th scope="col">Customer</th>
              <th scope="col">Type and summary</th>
              <th scope="col">Claimed</th>
              <th scope="col">Status</th>
            </tr>
          </thead>
          <tbody>
            {claims.map((claim) => (
              <tr key={claim.claim_id}>
                <td data-label="Claim" className="mono claim-id">
                  <button
                    type="button"
                    className="claim-link"
                    aria-label={`View claim ${claim.claim_id}`}
                    onClick={() => {
                      onSelect(claim.claim_id);
                    }}
                  >
                    {claim.claim_id}
                  </button>
                </td>
                <td data-label="Customer" className="customer-name">
                  {claim.customer_name}
                </td>
                <td data-label="Type and summary">
                  <span className="claim-type">
                    {formatClaimType(claim.claim_type)}
                  </span>
                  <span className="claim-summary">{claim.summary}</span>
                </td>
                <td data-label="Claimed" className="mono money">
                  {formatMoney(claim.amount_claimed)}
                </td>
                <td data-label="Status">
                  <StatusBadge status={claim.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function InstructionsView() {
  const [instructions, setInstructions] = useState("");
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");

  useEffect(() => {
    let active = true;
    getInstructions()
      .then((data) => {
        if (active) {
          setInstructions(data.content);
          setState("ready");
        }
      })
      .catch(() => {
        if (active) setState("error");
      });
    return () => {
      active = false;
    };
  }, []);

  if (state === "loading")
    return <p role="status">Loading agent instructions…</p>;
  if (state === "error") {
    return <p role="alert">We could not load the agent instructions.</p>;
  }

  return (
    <section
      className="panel instructions-panel"
      aria-labelledby="instructions-title"
    >
      <p className="eyebrow">UnderCover Agent</p>
      <h1 id="instructions-title">Agent instructions</h1>
      <p>The current instructions are read only.</p>
      <pre>{instructions}</pre>
    </section>
  );
}

export function App() {
  const [view, setView] = useState<View>("queue");
  const [selectedClaim, setSelectedClaim] = useState<string | null>(null);

  let mainContent = <QueueView onSelect={setSelectedClaim} />;
  if (selectedClaim) {
    mainContent = (
      <ClaimWorkspace
        key={selectedClaim}
        claimId={selectedClaim}
        onBack={() => {
          setSelectedClaim(null);
        }}
      />
    );
  }
  if (view === "instructions") mainContent = <InstructionsView />;

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            UC
          </span>
          <div>
            <span className="brand-name">UnderCover</span>
            <span className="brand-product">Claims pilot</span>
          </div>
        </div>
        <nav aria-label="Main navigation">
          <button
            type="button"
            className={view === "queue" ? "nav-active" : ""}
            aria-current={view === "queue" ? "page" : undefined}
            onClick={() => {
              setView("queue");
              setSelectedClaim(null);
            }}
          >
            Claims
          </button>
          <button
            type="button"
            className={view === "instructions" ? "nav-active" : ""}
            aria-current={view === "instructions" ? "page" : undefined}
            onClick={() => {
              setView("instructions");
            }}
          >
            Agent instructions
          </button>
        </nav>
      </header>
      <main>{mainContent}</main>
      <footer>Decisions grounded in the policy.</footer>
    </div>
  );
}
