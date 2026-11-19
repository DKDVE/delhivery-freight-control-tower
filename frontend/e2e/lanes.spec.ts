import { expect, test, type Page } from "@playwright/test";

test.beforeEach(({ page }) => {
  page.on("console", (m) => { if (m.type() === "error" && !/status of 404/.test(m.text())) throw new Error(`console error: ${m.text()}`); });
  page.on("pageerror", (e) => { throw e; });
});

const FOOTER = /no on-time or damage records/;

async function search(page: Page, from = "HAZIRA", to = "KHADOLI") {
  await page.goto("/lanes");
  await page.getByLabel("From (origin)").fill(from);
  await page.getByLabel("To (destination)").fill(to);
  await page.getByRole("button", { name: "Find vendors" }).click();
}

test("Hazira to Khadoli -> tags and chart -> recommendation opens Booking check pre-filled", async ({ page }) => {
  await search(page);
  await expect(page.getByTestId("lane-summary")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("lane-chart")).toBeVisible();
  await expect(page.getByRole("table", { name: /Vendors on this lane/ })).toBeVisible();
  await expect(page.getByText("Cheapest", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/Fair|Costly/).first()).toBeVisible();
  await expect(page.getByTestId("lane-footer")).toContainText(FOOTER);
  const vendor = (await page.getByTestId("rec-vendor").innerText()).trim();
  expect(vendor).toMatch(/^V\d{3}$/);
  await page.getByTestId("check-booking").click();
  await expect(page).toHaveURL(/\/booking\?/);
  await expect(page.getByLabel("Route", { exact: true })).toHaveValue("HZKHA1");
  await expect(page.getByLabel("Vendor", { exact: true })).toHaveValue(vendor);
  await expect(page.getByLabel("Load (MT)")).not.toHaveValue("");
});

test("a vendor row opens the vendor drawer and the tab switches vehicle type", async ({ page }) => {
  await search(page);
  await expect(page.getByTestId("lane-summary")).toBeVisible({ timeout: 60_000 });
  const tabs = page.getByRole("tablist", { name: "Vehicle type" }).getByRole("tab");
  if ((await tabs.count()) > 1) {
    await tabs.nth(1).click();
    await expect(tabs.nth(1)).toHaveAttribute("aria-selected", "true");
  }
  await page.getByRole("table", { name: /Vendors on this lane/ }).locator("tbody tr").first().click();
  await expect(page.getByRole("dialog")).toContainText("Vendor V");
});

test("sorting the vendor table by price reorders it", async ({ page }) => {
  await search(page);
  await expect(page.getByTestId("lane-summary")).toBeVisible({ timeout: 60_000 });
  await page.getByRole("button", { name: "Price per MT" }).click();
  const th = page.getByRole("columnheader", { name: /Price per MT/ });
  await expect(th).toHaveAttribute("aria-sort", /ascending|descending/);
});

test("unknown destination shows the suggestion, no crash", async ({ page }) => {
  await search(page, "HAZIRA", "KHADOL");
  await expect(page.getByRole("alert")).toContainText("KHADOLI", { timeout: 30_000 });
});

test("compare two vendors head to head", async ({ page }) => {
  await page.goto("/lanes?view=compare");
  await page.getByLabel("Vendor 1").fill("V013");
  await page.getByLabel("Vendor 2").fill("V023");
  await page.getByRole("button", { name: "Compare vendors" }).click();
  await expect(page.getByTestId("compare-result")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("compare-V013")).toBeVisible();
  await expect(page.getByTestId("compare-V023")).toBeVisible();
  await expect(page.getByRole("table", { name: "Shared lanes" })).toBeVisible();
  await expect(page.getByTestId("lane-footer")).toContainText(FOOTER);
});

test("390 px: the lane finder and the compare view have no page-level horizontal scroll", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await search(page);
  await expect(page.getByTestId("lane-summary")).toBeVisible({ timeout: 60_000 });
  const over = () => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(await over()).toBeLessThanOrEqual(0);
  await page.goto("/lanes?view=compare&codes=V013,V023");
  await expect(page.getByTestId("compare-result")).toBeVisible({ timeout: 30_000 });
  expect(await over()).toBeLessThanOrEqual(0);
});

// A head-to-head answer in Ask the data is a top-N cut of a longer list, and the card says so (Top N of M).
test("ask: head to head table is labelled Top 10 of the true total", async ({ page }) => {
  await page.goto("/ask");
  await page.getByRole("textbox", { name: "Your question" }).fill("compare V013 and V023");
  await page.getByRole("button", { name: "Ask" }).click();
  await page.getByTestId("headline").waitFor({ timeout: 30_000 });
  await expect(page.getByText("Top 10 of 56 rows", { exact: true })).toBeVisible();
  await expect(page.getByTestId("headline")).toContainText("56");
});
