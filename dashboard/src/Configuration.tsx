import { useEffect, useState } from "react";
import { getConfiguration, type ConfigurationHistory } from "./api";
import { DiffView } from "./DiffView";
import { formatPercent } from "./format";

export function Configuration() {
  const [history, setHistory] = useState<ConfigurationHistory | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    getConfiguration()
      .then((data) => {
        if (active) setHistory(data);
      })
      .catch(() => {
        if (active) setError(true);
      });
    return () => {
      active = false;
    };
  }, []);
  if (error)
    return (
      <p role="alert">We could not load configuration versions. Try again.</p>
    );
  if (!history) return <p role="status">Loading configuration…</p>;
  return (
    <div className="scoreboard-layout">
      <section className="panel detail-section">
        <p className="eyebrow">UnderCover Agent</p>
        <h1>Configuration</h1>
        <p>Review the claim agent’s instructions, model and version history.</p>
        <strong>Current version: {history.current_version}</strong>
      </section>
      {history.versions.map((version) => (
        <section className="panel detail-section" key={version.version}>
          <h2>Version {version.version}</h2>
          <p className="mono">
            {version.model_name} · {version.reasoning_effort}
          </p>
          <p>{version.reason}</p>
          <p className="muted">
            Proposed by {version.proposed_by}; approved by{" "}
            {version.approved_by ?? "Initial seed"}.
          </p>
          {version.held_out_scores ? (
            <p>
              Held-out accuracy:{" "}
              <span>
                {formatPercent(version.held_out_scores.accuracy ?? 0)}
              </span>
              ; grade:{" "}
              <span>
                {version.held_out_scores.grade?.toFixed(2) ?? "Not graded"} / 3
              </span>
            </p>
          ) : (
            <p className="muted">No whole-set round evaluations yet.</p>
          )}
          <details>
            <summary>Instructions</summary>
            <pre className="configuration-text">{version.instructions}</pre>
          </details>
          {version.version === 1 ? (
            <p>Version 1 has no earlier version to compare.</p>
          ) : (
            <DiffView lines={version.diff} />
          )}
        </section>
      ))}
    </div>
  );
}
