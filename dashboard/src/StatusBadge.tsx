import type { ClaimStatus } from "./api";
import { statusLabel } from "./format";

export function StatusBadge({ status }: Readonly<{ status: ClaimStatus }>) {
  return (
    <span className={`status status-${status}`}>{statusLabel(status)}</span>
  );
}
