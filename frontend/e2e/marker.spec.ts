import { expect, test } from "@playwright/test";

test("clicking a map marker opens its details", async ({ page }) => {
  await page.goto("/map");
  await page.getByRole("region", { name: "Outage map" }).getByRole("button", { name: /AWS N. Virginia/ }).click();
  await expect(page.getByText(/Increased error rates in US-EAST-1/)).toBeVisible();
});
