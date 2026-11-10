import type { ClaimStatus } from "./api";

const moneyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
});

const modelCostFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 6,
});

export function formatMoney(amount: number): string {
  return moneyFormatter.format(amount);
}

export function formatModelCost(amount: number): string {
  return modelCostFormatter.format(amount);
}

export function formatSeconds(seconds: number): string {
  return `${seconds.toFixed(1)} s`;
}

export function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function formatClaimType(claimType: string): string {
  const label = claimType.replaceAll("_", " ");
  return label.charAt(0).toUpperCase() + label.slice(1);
}

export function statusLabel(status: ClaimStatus): string {
  const labels: Record<ClaimStatus, string> = {
    waiting: "Waiting",
    in_progress: "In progress",
    waiting_for_sign_off: "Waiting for sign-off",
    decided: "Decided",
    left_for_handler: "Left for handler",
  };
  return labels[status];
}
