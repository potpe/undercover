import type { Activity } from "./api";

type Lookup = Extract<Activity, { kind: "lookup" }>["details"];

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value !== null && typeof value === "object") {
    return `{${Object.entries(value)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value) || "undefined";
}

function lookupKey(lookup: Lookup): string {
  return `${lookup.operation}:${canonical(lookup.input)}`;
}

export function ContextUsed({ activity }: Readonly<{ activity: Activity[] }>) {
  const byFile = new Map<
    string,
    { source: Lookup["sources"][number]; lookups: Map<string, Lookup> }
  >();

  activity.forEach((entry) => {
    if (entry.kind !== "lookup") return;
    entry.details.sources.forEach((source) => {
      const group = byFile.get(source.file) ?? {
        source,
        lookups: new Map<string, Lookup>(),
      };
      group.lookups.set(lookupKey(entry.details), entry.details);
      byFile.set(source.file, group);
    });
  });

  return (
    <section
      className="panel detail-section context-used"
      aria-labelledby="context-used-title"
    >
      <p className="eyebrow">Reference material</p>
      <h2 id="context-used-title">Context used</h2>
      {byFile.size ? (
        <div className="context-files">
          {[...byFile].map(([file, group]) => (
            <article className="context-file" key={file}>
              <div className="context-file-heading">
                <h3>{file}</h3>
                <p>
                  {group.source.system} · {group.source.owner} · reviewed{" "}
                  {group.source.reviewed}
                </p>
              </div>
              <ul>
                {[...group.lookups].map(([key, lookup]) => (
                  <li key={key}>
                    <strong>{lookup.operation.replaceAll("_", " ")}</strong>
                    {Object.keys(lookup.input).length ? (
                      <dl>
                        {Object.entries(lookup.input).map(([name, value]) => (
                          <div key={name}>
                            <dt>{name.replaceAll("_", " ")}</dt>
                            <dd>
                              {typeof value === "string"
                                ? value
                                : canonical(value)}
                            </dd>
                          </div>
                        ))}
                      </dl>
                    ) : null}
                  </li>
                ))}
              </ul>
            </article>
          ))}
        </div>
      ) : (
        <p className="muted">No reference material has been used yet.</p>
      )}
    </section>
  );
}
