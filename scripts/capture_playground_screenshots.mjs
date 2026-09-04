/**
 * Capture the Playground and Marketplace screens against a running server.
 *
 *   python scripts/run_commerce_lab.py &
 *   node scripts/capture_playground_screenshots.mjs http://127.0.0.1:8080
 *
 * Uses the locally installed Chrome channel rather than a downloaded browser
 * bundle, so it works on a machine that has not run `playwright install`.
 * Every screen is driven the way a person would drive it: type, search, click a
 * candidate, read the verdict. Nothing is stubbed, so a screenshot that shows
 * ALLOW is a screenshot of the controller having answered ALLOW.
 */

import { mkdir } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

// Playwright is a screenshot tool, not a runtime dependency: the product ships
// with no third-party packages at all, and this script must not be the reason
// that stops being true. So it is resolved at run time from wherever it happens
// to be installed, and `PLAYWRIGHT_MODULE` names the path when it is somewhere
// npm's own resolution will not find, such as an npx cache.
const playwrightSpecifier = process.env.PLAYWRIGHT_MODULE
  ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href
  : "playwright";
const { chromium } = await import(playwrightSpecifier);

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = join(HERE, "..", "docs", "screenshots");
const BASE = process.argv[2] || "http://127.0.0.1:8080";
const VIEWPORT = { width: 1180, height: 900 };

async function shot(page, name) {
  await page.waitForTimeout(450);
  await page.screenshot({ path: join(OUT, `${name}.png`), fullPage: false });
  process.stdout.write(`  ${name}.png\n`);
}

async function scrollRegion(page, selector) {
  await page.evaluate((target) => {
    document.querySelector(target)?.scrollIntoView();
    window.scrollBy(0, -130);
  }, selector);
}

async function search(page, intent) {
  await page.fill("#pg-intent", intent);
  await page.click("#pg-search-button");
  await page.waitForSelector(".pgcard", { timeout: 15000 });
}

/**
 * Click a candidate, optionally one whose billing model the merchant left
 * undeclared. Selecting by what the evidence says is the point: the screenshot
 * has to show the controller answering for a listing whose paperwork is
 * genuinely incomplete, not one picked because it produced the wanted verdict.
 */
async function chooseCandidate(page, { billingTone } = {}) {
  if (billingTone) {
    const button = await page.$(
      `.pgcard:has(.readiness__row[data-field="billing_model"][data-tone="${billingTone}"]) [data-authorize]`,
    );
    if (button) {
      await button.click();
      return;
    }
    process.stdout.write(`  ! no candidate with billing tone ${billingTone}\n`);
  }
  await page.click(".pgcard [data-authorize]");
}

async function waitForVerdict(page) {
  await page.waitForSelector(".pgverdict__word", { timeout: 30000 });
  await page.waitForFunction(
    () => {
      const word = document.querySelector(".pgverdict__word")?.textContent?.trim();
      return word === "ALLOW" || word === "BLOCK" || word === "REVIEW";
    },
    { timeout: 30000 },
  );
  return page.$eval(".pgverdict__word", (node) => node.textContent.trim());
}

async function main() {
  await mkdir(OUT, { recursive: true });
  const browser = await chromium.launch({ channel: "chrome" });

  // -- Playground initial ------------------------------------------------
  let context = await browser.newContext({ viewport: VIEWPORT });
  let page = await context.newPage();
  await page.goto(BASE, { waitUntil: "networkidle" });
  await shot(page, "playground-initial");

  // -- Custom search -----------------------------------------------------
  await search(page, "Buy wireless headphones under INR 5,000. No subscriptions.");
  await scrollRegion(page, "#pg-results-region");
  await shot(page, "playground-custom-search");

  // -- ALLOW -------------------------------------------------------------
  await chooseCandidate(page, { billingTone: "ok" });
  let verdict = await waitForVerdict(page);
  await scrollRegion(page, "#pg-outcome-region");
  await shot(page, "playground-allow");
  if (verdict !== "ALLOW") throw new Error(`expected ALLOW, saw ${verdict}`);

  // -- REVIEW: deterministic undeclared-billing scenario -----------------
  await context.close();
  context = await browser.newContext({ viewport: VIEWPORT });
  page = await context.newPage();
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.click('[data-scenario="billing-undeclared"]');
  verdict = await waitForVerdict(page);
  await scrollRegion(page, "#pg-outcome-region");
  await shot(page, "playground-review");
  if (verdict !== "REVIEW") throw new Error(`expected REVIEW, saw ${verdict}`);
  process.stdout.write(`  review screen verdict: ${verdict}\n`);

  // -- BLOCK: the prohibited-content journey -----------------------------
  await context.close();
  context = await browser.newContext({ viewport: VIEWPORT });
  page = await context.newPage();
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.click('[data-scenario="prohibited-content"]');
  verdict = await waitForVerdict(page);
  await scrollRegion(page, "#pg-outcome-region");
  await shot(page, "playground-block");
  if (verdict !== "BLOCK") throw new Error(`expected BLOCK, saw ${verdict}`);
  process.stdout.write(`  block screen verdict: ${verdict}\n`);

  // -- Revocation --------------------------------------------------------
  await context.close();
  context = await browser.newContext({ viewport: VIEWPORT });
  page = await context.newPage();
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.click('[data-scenario="revoked-after-allow"]');
  await waitForVerdict(page);
  await page.waitForSelector("#pg-revoke", { timeout: 20000 });
  await page.click("#pg-revoke");
  await page.waitForTimeout(900);
  await page.click("#pg-execute");
  await page.waitForTimeout(1400);
  await scrollRegion(page, "#pg-outcome-region");
  await shot(page, "playground-revocation");

  // -- Marketplace readiness --------------------------------------------
  await context.close();
  context = await browser.newContext({ viewport: VIEWPORT });
  page = await context.newPage();
  await page.goto(`${BASE}#observe`, { waitUntil: "networkidle" });
  await page.click("#tab-observe");
  await page.waitForTimeout(400);
  await page.fill("#purchase-intent", "Buy a study lamp under INR 2,000. No subscriptions.");
  await page.click("#run-button");
  const unreadyListing = page.locator('.listing[data-transactable="false"]').first();
  await unreadyListing.waitFor({ timeout: 25000 });
  await unreadyListing.scrollIntoViewIfNeeded();
  await page.evaluate(() => window.scrollBy(0, -130));
  await shot(page, "marketplace-readiness");

  // -- Scale lab ---------------------------------------------------------
  await page.click("#tab-scale");
  await page.waitForTimeout(500);
  await page.evaluate(() => window.scrollTo(0, 0));
  await shot(page, "scale-lab");

  // -- Mobile ------------------------------------------------------------
  await context.close();
  context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  page = await context.newPage();
  await page.goto(BASE, { waitUntil: "networkidle" });
  await search(page, "desk lamp under 2000");
  await scrollRegion(page, "#pg-results-region");
  await shot(page, "playground-mobile");

  await context.close();
  await browser.close();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
