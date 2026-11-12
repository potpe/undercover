import type { ImprovementRound } from "./improvement-api";
import { formatPercent } from "./format";

export function VersionChart({
  rounds,
}: Readonly<{ rounds: ImprovementRound[] }>) {
  const points = rounds
    .filter(
      (round) =>
        round.candidate_version !== null && round.held_out_after !== null,
    )
    .toReversed();
  if (!points.length)
    return (
      <p className="muted">
        The chart will appear after a version is evaluated.
      </p>
    );
  return (
    <section className="panel detail-section">
      <h2>Versions tried</h2>
      <p>Held-out accuracy and explanation grade use separate scales.</p>
      <svg
        className="version-chart"
        viewBox="0 0 720 300"
        role="img"
        aria-label="Held-out accuracy and grade by version"
      >
        <text x="40" y="24">
          Accuracy (0–100%)
        </text>
        <text x="400" y="24">
          Grade (0–3)
        </text>
        <path
          d="M40 45 V250 H330 M400 45 V250 H690"
          fill="none"
          stroke="currentColor"
        />
        <text x="40" y="42">
          100%
        </text>
        <text x="400" y="42">
          3
        </text>
        <text x="40" y="270">
          0%
        </text>
        <text x="400" y="270">
          0
        </text>
        {points.map((round, index) => {
          const x = 80 + (index * 220) / Math.max(1, points.length - 1);
          const accuracy = round.held_out_after?.accuracy;
          const grade = round.held_out_after?.grade;
          return (
            <g key={round.round_id}>
              {accuracy !== null && accuracy !== undefined ? (
                <circle
                  className="chart-accuracy"
                  cx={x}
                  cy={250 - accuracy * 200}
                  r="5"
                />
              ) : null}
              {grade !== null && grade !== undefined ? (
                <circle
                  className="chart-grade"
                  cx={x + 360}
                  cy={250 - (grade / 3) * 200}
                  r="5"
                />
              ) : null}
              <text x={x - 10} y="290">
                v{round.candidate_version}
              </text>
              <text x={x + 350} y="290">
                v{round.candidate_version}
              </text>
            </g>
          );
        })}
      </svg>
      <table aria-label="Held-out version scores">
        <thead>
          <tr>
            <th>Version</th>
            <th className="money">Accuracy</th>
            <th className="money">Grade (0–3)</th>
          </tr>
        </thead>
        <tbody>
          {points.map((round) => (
            <tr key={round.round_id}>
              <th>{round.candidate_version}</th>
              <td className="money mono">
                {round.held_out_after?.accuracy === null
                  ? "Not available"
                  : formatPercent(round.held_out_after?.accuracy ?? 0)}
              </td>
              <td className="money mono">
                {round.held_out_after?.grade?.toFixed(2) ?? "Not graded"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
