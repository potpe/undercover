import { expect, it } from "vitest";
import { formatClaimType, formatMoney, statusLabel } from "./format";

it("formats US dollars and claim labels", () => {
  expect(formatMoney(1250)).toBe("$1,250.00");
  expect(formatMoney(0)).toBe("$0.00");
  expect(formatClaimType("emergency_medical")).toBe("Emergency medical");
});

it("labels every claim state", () => {
  expect(statusLabel("waiting")).toBe("Waiting");
  expect(statusLabel("in_progress")).toBe("In progress");
  expect(statusLabel("waiting_for_sign_off")).toBe("Waiting for sign-off");
  expect(statusLabel("decided")).toBe("Decided");
  expect(statusLabel("left_for_handler")).toBe("Left for handler");
});
