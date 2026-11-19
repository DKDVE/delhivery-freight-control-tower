import { test } from "@playwright/test";

// One screenshot per answer-card chart shape (docs/screenshots/ask/).
const CARDS: [string, string][] = [
  ["bar", "Which vendors lose the most money?"],
  ["line", "How has margin trended by month?"],
  ["kpi_tile", "What is the margin in the south zone?"],
  ["table_only", "Which lanes have the widest vendor price gap?"],
];

for (const [w, tag] of [[1440, "1440"], [390, "390"]] as const) {
  test(`answer card screenshots at ${tag}px`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: 1000 });
    for (const [name, question] of CARDS) {
      await page.goto("/ask");
      await page.getByRole("textbox", { name: "Your question" }).fill(question);
      await page.getByRole("button", { name: "Ask" }).click();
      await page.getByTestId("headline").waitFor({ timeout: 30_000 });
      await page.waitForTimeout(400);
      await page.screenshot({ path: `../docs/screenshots/ask/${name}_${tag}.png`, fullPage: true });
    }
  });
}
