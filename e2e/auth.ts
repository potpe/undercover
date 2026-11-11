import { expect, type Page } from "@playwright/test";

export async function signIn(
  page: Page,
  email = "dana.whitfield@undercover.example",
) {
  await page.goto("/");
  const signInButton = page.getByRole("button", { name: "Sign in" });
  if (await signInButton.isVisible()) {
    await page.getByLabel("Email address").fill(email);
    await page.getByLabel("Password").fill("test-password");
    await signInButton.click();
  }
  await expect(
    page.getByRole("heading", { name: "Claims queue" }),
  ).toBeVisible();
}
