import type { DiffLine } from "./api";

const markers = { added: "+", removed: "−", context: " " };

export function DiffView({ lines }: Readonly<{ lines: DiffLine[] }>) {
  if (!lines.length) return <p className="muted">No text changes.</p>;
  return (
    <pre className="diff-view" aria-label="Changes">
      {lines.map((line, index) => (
        <span className={`diff-${line.kind}`} key={index}>
          {markers[line.kind]}
          {line.text}
          {"\n"}
        </span>
      ))}
    </pre>
  );
}
