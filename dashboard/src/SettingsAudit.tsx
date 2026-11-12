import type { SettingChange } from "./api";
import { formatDateTime } from "./format";

export function SettingsAudit({
  changes,
}: Readonly<{ changes: SettingChange[] }>) {
  return (
    <section className="panel detail-section">
      <h2>Change history</h2>
      {changes.length ? (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Setting</th>
                <th>Previous value</th>
                <th>New value</th>
                <th>Changed by</th>
                <th>When</th>
              </tr>
            </thead>
            <tbody>
              {changes.map((change, index) => (
                <tr key={`${change.key}-${change.changed_at}-${String(index)}`}>
                  <th>{change.key.replaceAll("_", " ")}</th>
                  <td>{JSON.stringify(change.old_value)}</td>
                  <td>{JSON.stringify(change.new_value)}</td>
                  <td>{change.changed_by}</td>
                  <td>{formatDateTime(change.changed_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p>No platform settings have changed since the last seed.</p>
      )}
    </section>
  );
}
