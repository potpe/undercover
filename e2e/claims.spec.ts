import { expect, test } from "@playwright/test";

test("the queue shows the 25 pilot claims", async ({ page }) => {
  await page.goto("/");

  await expect(
    page.getByRole("heading", { name: "Claims queue" }),
  ).toBeVisible();
  await expect(page.locator("tbody tr")).toHaveCount(25);
  await expect(page.getByText("Maya Patel", { exact: true })).toBeVisible();
  await expect(page.getByText("$420.00", { exact: true })).toBeVisible();
  await expect(page.getByText("CLM-1025", { exact: true })).toBeVisible();
});

test("the agent instructions are read only", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Agent instructions" }).click();

  await expect(
    page.getByRole("heading", { name: "Agent instructions" }),
  ).toBeVisible();
  await expect(
    page.getByText("You are the UnderCover Agent.", { exact: false }),
  ).toBeVisible();
  await expect(page.getByRole("textbox")).toHaveCount(0);
});
