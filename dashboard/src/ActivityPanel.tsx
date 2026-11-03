import type { Activity, ClaimStatus } from "./api";

export function ActivityPanel({
  activity,
  status,
}: Readonly<{ activity: Activity[]; status: ClaimStatus }>) {
  return (
    <section className="panel activity-panel" aria-labelledby="activity-title">
      <div className="detail-heading">
        <h2 id="activity-title">Agent activity</h2>
        {status === "in_progress" ? (
          <span className="live-label">Live</span>
        ) : null}
      </div>
      {activity.length ? (
        <ol>
          {activity.map((entry) => (
            <li key={entry.id}>
              <time dateTime={entry.created_at}>
                {new Date(entry.created_at).toLocaleTimeString("en-US", {
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </time>
              <span>{entry.message}</span>
            </li>
          ))}
        </ol>
      ) : (
        <p className="muted">No agent activity yet.</p>
      )}
    </section>
  );
}
