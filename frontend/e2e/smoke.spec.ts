import { expect, test } from "@playwright/test";

test("dashboard shows the verdict, banners and tiles in demo mode", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("having problems");
  await expect(page.getByText("Demo data")).toBeVisible();
  await expect(page.getByRole("link", { name: /GitHub: Major outage/ })).toBeVisible();
  await expect(page.getByText("Only from your network").first()).toBeVisible();
  await page.getByRole("button", { name: /^Problems/ }).click();
  await expect(page.getByRole("link", { name: /YouTube/ })).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("service detail explains the verdict", async ({ page }) => {
  await page.goto("/service/steam");
  await expect(page.getByRole("heading", { name: "Steam" })).toBeVisible();
  await expect(page.getByText(/Failing from your network only/)).toBeVisible();
  await expect(page.getByText("Checks from your network")).toBeVisible();
});

test("admin can sign in and watch a catalog service", async ({ page }) => {
  await page.goto("/catalog");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByLabel("Username").fill("admin");
  await page.getByLabel(/Password/).fill("e2e-password-123");
  await page.getByRole("button", { name: "Sign in" }).last().click();
  await expect(page.getByText("admin")).toBeVisible();
  const row = page.getByRole("listitem").filter({ hasText: "Bitbucket" });
  await row.getByRole("button", { name: "Watch" }).click();
  await expect(row.getByRole("button", { name: "Watching" })).toBeVisible();
});

test("keyboard shortcut opens incidents", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await page.keyboard.press("g");
  await page.keyboard.press("i");
  await expect(page).toHaveURL(/\/incidents/);
  await expect(page.getByRole("heading", { name: "Incidents" })).toBeVisible();
});

test("map shows affected regions and agents", async ({ page }) => {
  await page.goto("/map");
  await expect(page.getByRole("heading", { name: "Affected regions" })).toBeVisible();
  await expect(page.getByRole("button", { name: /AWS N. Virginia/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "Agent Frankfurt Pi" })).toBeVisible();
  await page.locator("aside").getByRole("button", { name: /AWS N. Virginia/ }).click();
  await expect(page.getByText(/Increased error rates in US-EAST-1/)).toBeVisible();
});
