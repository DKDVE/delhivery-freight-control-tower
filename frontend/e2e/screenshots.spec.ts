import { test } from "@playwright/test";

const PAGES: [string, string][] = [
  ["01_overview", "/"],
  ["02_booking_check", "/booking?route=AFBAN1&vehicle_type=OB22&vendor=V002&qty=3&month=3&quoted=20000"],
  ["03_vendors", "/vendors"],
  ["03b_vendor_drawer", "/vendors/V012"],
  ["04_trips", "/trips"],
  ["05_hubs", "/hubs"],
  ["06_ask_the_data", "/ask"],
  ["07_prompt_library", "/prompts"],
  ["08_audit_log", "/audit"],
  ["09_model_cards", "/models"],
];

for (const [w, h, tag] of [[1440, 900, "1440"], [390, 844, "390"]] as const) {
  test(`screenshots at ${tag}px`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h });
    for (const [name, url] of PAGES) {
      await page.goto(url);
      if (name === "02_booking_check") await page.getByRole("button", { name: "Check booking" }).click();
      await page.waitForLoadState("networkidle");
      await page.waitForTimeout(600);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      if (overflow > 0) console.log(`HORIZONTAL SCROLL ${tag}px ${name}: +${overflow}px`);
      await page.screenshot({ path: `../docs/screenshots/${name}_${tag}.png`, fullPage: name !== "03b_vendor_drawer" });
    }
  });
}
