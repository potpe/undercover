import type { Activity, ClaimStatus } from "./api";

const sourceLabels: Record<
  Extract<Activity, { kind: "refusal" }>["details"]["source"],
  string
> = {
  app: "App",
  context: "Context",
  gateway: "Gateway",
  memory: "Memory",
};

function ActivityDetails({ entry }: Readonly<{ entry: Activity }>) {
  switch (entry.kind) {
    case "activity":
      return null;
    case "tool":
      return (
        <p className="activity-detail">
          <strong>{entry.details.name}</strong>
          {Object.keys(entry.details.input).length ? (
            <code>{JSON.stringify(entry.details.input)}</code>
          ) : null}
        </p>
      );
    case "lookup":
      return (
        <div className="activity-detail lookup-detail">
          <p>
            <strong>Reference lookup:</strong>{" "}
            {entry.details.operation.replaceAll("_", " ")}
          </p>
          <dl aria-label="Lookup inputs">
            {Object.entries(entry.details.input).map(([name, value]) => (
              <div key={name}>
                <dt>{name.replaceAll("_", " ")}</dt>
                <dd>
                  {typeof value === "string" ? value : JSON.stringify(value)}
                </dd>
              </div>
            ))}
          </dl>
          <ul aria-label="Reference sources">
            {entry.details.sources.map((source) => (
              <li key={source.file}>
                <strong>{source.file}</strong>
                <span>
                  {source.system} · {source.owner} · reviewed {source.reviewed}
                </span>
              </li>
            ))}
          </ul>
        </div>
      );
    case "memory_search":
      return (
        <div className="activity-detail">
          <p>Search: {entry.details.query}</p>
          {entry.details.results.length ? (
            <ul>
              {entry.details.results.map((result, index) => (
                <li key={`${String(index)}-${result}`}>{result}</li>
              ))}
            </ul>
          ) : (
            <p>No earlier notes found.</p>
          )}
        </div>
      );
    case "memory_save":
      return (
        <div className="activity-detail">
          <p>Saved {entry.details.messages.length} notes.</p>
          {entry.details.messages.length ? (
            <ul>
              {entry.details.messages.map((message, index) => (
                <li key={`${String(index)}-${message}`}>{message}</li>
              ))}
            </ul>
          ) : null}
        </div>
      );
    case "refusal":
      return (
        <div className="activity-detail refusal-detail">
          <strong>{sourceLabels[entry.details.source]} refused</strong>
          <p>{entry.details.reason}</p>
          <span>Request: {entry.details.operation}</span>
        </div>
      );
  }
}

export function ActivityPanel({
  activity,
  status,
}: Readonly<{ activity: Activity[]; status: ClaimStatus }>) {
  return (
    <section className="panel activity-panel" aria-labelledby="activity-title">
      <div className="detail-heading">
        <h2 id="activity-title">Agent activity</h2>
        {status === "in_progress" ? (
          <span className="live-label" role="status">
            Working · updates live
          </span>
        ) : null}
      </div>
      {activity.length ? (
        <ol>
          {activity.map((entry) => (
            <li
              key={entry.id}
              className={entry.kind === "refusal" ? "activity-refusal" : ""}
            >
              <time dateTime={entry.created_at}>
                {new Date(entry.created_at).toLocaleTimeString("en-US", {
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </time>
              <div className="activity-content">
                <span>{entry.message}</span>
                <ActivityDetails entry={entry} />
              </div>
            </li>
          ))}
        </ol>
      ) : (
        <p className="muted">No agent activity yet.</p>
      )}
    </section>
  );
}
