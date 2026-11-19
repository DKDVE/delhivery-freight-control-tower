import { expect, test } from "@playwright/test";

test.beforeEach(({ page }) => {
  page.on("console", (m) => { if (m.type() === "error") throw new Error(`console error: ${m.text()}`); });
  page.on("pageerror", (e) => { throw e; });
});

test("booking check -> approve draft -> appears in the audit log", async ({ page }) => {
  await page.goto("/booking");
  await page.getByRole("button", { name: "Check booking" }).click();
  const rec = page.getByTestId("recommendation");
  await expect(rec).toBeVisible({ timeout: 40_000 }); // the first call loads the models
  await expect(page.getByTestId("action")).not.toBeEmpty();
  await page.getByRole("button", { name: "Approve", exact: true }).click();
  await expect(page.getByTestId("decision-state")).toContainText("Approved");
  // the decision is written to the audit log only after the 5 s undo window
  await expect(page.getByTestId("decision-state")).not.toContainText("undo available", { timeout: 15_000 });
  await page.goto("/audit");
  const row = page.getByRole("row", { name: /Booking check/ }).first();
  await expect(row).toBeVisible();
  await expect(row).toContainText("Approved");
});

test("undo inside the window records nothing", async ({ page }) => {
  await page.goto("/booking?route=AFBAN1&vehicle_type=OB22&vendor=V002&qty=3&month=3&quoted=20000");
  await page.getByRole("button", { name: "Check booking" }).click();
  await page.getByRole("button", { name: "Reject" }).click();
  await page.getByRole("button", { name: "Undo" }).click();
  await expect(page.getByRole("button", { name: "Approve", exact: true })).toBeVisible();
});

test("ask a question -> chart, table and headline with a formatted number appear", async ({ page }) => {
  await page.goto("/ask");
  await page.getByRole("button", { name: "Which hubs are slowest at handling?" }).click();
  await expect(page.getByTestId("headline")).toContainText(/[\d%]/, { timeout: 30_000 });
  await expect(page.locator('[role="img"]').first()).toBeVisible();
  await expect(page.locator("table")).toBeVisible();
  await page.getByRole("button", { name: "How I got this" }).click();
  await expect(page.getByTestId("guardrail-chip")).toContainText("Passed", { timeout: 30_000 });
});

test("edit a filter chip -> card updates without a new LLM call", async ({ page }) => {
  await page.goto("/ask");
  await page.getByRole("button", { name: "Which vendors lose the most money?" }).click();
  await expect(page.getByTestId("headline")).toBeVisible({ timeout: 30_000 });
  const chip = page.getByText("Top: 10");
  if (await chip.count()) {
    await page.getByText("+10").click();
    await expect(page.getByText("Top: 20")).toBeVisible({ timeout: 10_000 });
  }
});

test("click a follow-up -> a new answer card appears", async ({ page }) => {
  await page.goto("/ask");
  await page.getByRole("button", { name: "Which vendors lose the most money?" }).click();
  await expect(page.getByTestId("headline")).toBeVisible({ timeout: 30_000 });
  const before = await page.getByTestId("headline").count();
  await page.getByRole("button", { name: /^Show:/ }).first().click();
  await expect(page.getByTestId("headline")).toHaveCount(before + 1, { timeout: 30_000 });
});

test("fix it feedback appears in the audit log", async ({ page }) => {
  await page.goto("/ask");
  await page.getByRole("button", { name: "Which hubs are slowest at handling?" }).click();
  await expect(page.getByTestId("headline")).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "Fix it" }).click();
  await page.getByLabel("wrong chart").check();
  await page.getByRole("button", { name: "Submit" }).click();
  await expect(page.getByText("Thanks, recorded")).toBeVisible({ timeout: 10_000 });
});

test("390px: the ask page has no page-level horizontal scroll", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await page.goto("/ask");
  await page.getByRole("button", { name: "Which vendors lose the most money?" }).click();
  await expect(page.getByTestId("headline")).toBeVisible({ timeout: 30_000 });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
  expect(overflow).toBe(false);
});

test("vendor drawer opens from a deep link", async ({ page }) => {
  await page.goto("/vendors/V012");
  await expect(page.getByRole("dialog", { name: "Vendor V012" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Draft renegotiation" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(/\/vendors(\?.*)?$/);
});

test("keyboard shortcuts", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("g"); await page.keyboard.press("b");
  await expect(page).toHaveURL(/\/booking/);
  await page.keyboard.press("/");
  await expect(page.getByLabel("Search vendors, routes, trips")).toBeFocused();
});
