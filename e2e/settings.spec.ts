import { expect, test } from "@playwright/test";
import { signIn } from "./auth";

test("a senior handler confirms a setting and remembers the theme", async ({
  page,
}) => {
  await signIn(page);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByLabel("Approval limit (USD)").fill("1100");
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "Confirm settings change" }).click();
  await expect(page.getByText("Settings saved.")).toBeVisible();
  await page.getByLabel("Appearance", { exact: true }).selectOption("dark");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(page.getByLabel("Approval limit (USD)")).toHaveValue("1100");
  await page.getByLabel("Appearance", { exact: true }).selectOption("light");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});

test("Marcus sees settings but cannot change them", async ({ page }) => {
  await signIn(page, "marcus.oyelaran@undercover.example");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(page.getByLabel("Approval limit (USD)")).toBeDisabled();
  await expect(page.getByRole("note")).toContainText("only a handler");
  await expect(page.getByRole("button", { name: "Save settings" })).toHaveCount(
    0,
  );
});
