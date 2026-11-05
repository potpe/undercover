import type { DocumentRead } from "./api";

export function DocumentSections({
  reads,
}: Readonly<{ reads: DocumentRead[] }>) {
  return (
    <section
      className="panel detail-section"
      aria-labelledby="sections-read-title"
    >
      <h2 id="sections-read-title">Sections read</h2>
      {reads.length ? (
        <ul className="sections-read">
          {reads.map((read) => (
            <li key={`${read.document}-${read.section_id}`}>
              <strong>
                {read.document === "claims_manual"
                  ? "Claims manual"
                  : "Policy wording"}{" "}
                {read.section_id}: {read.title}
              </strong>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">No policy or manual sections read yet.</p>
      )}
    </section>
  );
}
