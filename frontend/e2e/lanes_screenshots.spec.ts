import { test } from "@playwright/test";

// Lane finder screenshots -> docs/screenshots/lanes/
const SHOTS: [string, string][] = [
  ["01_lane_finder_hazira_khadoli", "/lanes?from=HAZIRA&to=KHADOLI&qty=15&vt=SB04&months=6"],
  ["02_lane_finder_all_vehicles", "/lanes?from=HAZIRA&to=KHADOLI&months=6"],
  ["03_compare_vendors", "/lanes?view=compare&codes=V013,V023"],
];

for (const [w, h, tag] of [[1440, 900, "1440"], [390, 844, "390"]] as const) {
  test(`lane finder screenshots at ${tag}px`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h });
    for (const [name, url] of SHOTS) {
      await page.goto(url);
      await page.waitForSelector('[data-testid="lane-footer"]');
      await page.waitForSelector('[data-testid="lane-summary"], [data-testid="compare-result"]', { timeout: 60_000 });
      await page.waitForLoadState("networkidle");
      await page.waitForTimeout(600);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      if (overflow > 0) console.log(`HORIZONTAL SCROLL ${tag}px ${name}: +${overflow}px`);
      await page.screenshot({ path: `../docs/screenshots/lanes/${name}_${tag}.png`, fullPage: true });
    }
  });
}
