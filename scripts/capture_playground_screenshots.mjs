/**
 * Capture the Playground and Marketplace screens against a running server.
 *
 *   python scripts/run_commerce_lab.py &
 *   node scripts/capture_playground_screenshots.mjs http://127.0.0.1:8080
 *
 * Uses the locally installed Chrome channel rather than a downloaded browser
 * bundle, so it works on a machine that has not run `playwright install`.
 * Every screen is driven the way a person would drive it: type, search, select
 * a product, ask for authorization, read the decision. Nothing is stubbed, so a
 * screenshot that shows ALLOW is a screenshot of the controller having answered
 * ALLOW.
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

/**
 * Put a region at the top of the shot.
 *
 * The stylesheet sets `scroll-behavior: smooth`, so an animated scroll would
 * still be in flight when the screenshot is taken. These jumps are instant and
 * absolute for that reason.
 */
async function scrollRegion(page, selector, offset = -110) {
  await page.evaluate(
    ([target, delta]) => {
      const node = document.querySelector(target);
      if (!node) return;
      const top = node.getBoundingClientRect().top + window.scrollY + delta;
      window.scrollTo({ top: Math.max(0, top), behavior: "instant" });
    },
    [selector, offset],
  );
  await page.waitForTimeout(250);
}

async function search(page, intent) {
  await page.fill("#pg-intent", intent);
  await page.click("#pg-search-button");
  await page.waitForSelector(".pgcard", { timeout: 15000 });
}

/**
 * Select a candidate, optionally one whose billing model the merchant left
 * undeclared. Selecting by what the evidence says is the point: the screenshot
 * has to show the controller answering for a listing whose paperwork is
 * genuinely incomplete, not one picked because it produced the wanted verdict.
 */
async function selectCandidate(page, { billingTone } = {}) {
  if (billingTone) {
    const button = await page.$(
      `.pgcard:has(.readiness__row[data-field="billing_model"][data-tone="${billingTone}"]) [data-authorize]`,
    );
    if (button) {
      await button.click();
    } else {
      process.stdout.write(`  ! no candidate with billing tone ${billingTone}\n`);
      await page.click(".pgcard [data-authorize]");
    }
  } else {
    await page.click(".pgcard [data-authorize]");
  }
  await page.waitForSelector('.pgchecks[data-checked="false"]', { timeout: 15000 });
}

async function askAuthorization(page) {
  await page.click("[data-check-authorization]");
  return waitForDecision(page);
}

async function waitForDecision(page) {
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

async function freshPage(browser, viewport = VIEWPORT) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  await page.goto(BASE, { waitUntil: "networkidle" });
  return { context, page };
}

async function main() {
  await mkdir(OUT, { recursive: true });
  const browser = await chromium.launch({ channel: "chrome" });

  // -- 1. Playground initial --------------------------------------------
  let { context, page } = await freshPage(browser);
  await shot(page, "playground-initial");

  // -- 2. Search results -------------------------------------------------
  await search(page, "Buy wireless headphones under INR 5,000. No subscriptions.");
  await scrollRegion(page, "#pg-candidates", -150);
  await shot(page, "playground-custom-search");

  // -- 3. Selected product, before authorization -------------------------
  await selectCandidate(page, { billingTone: "present" });
  // Framed on the comparison so the shot carries both halves of the state:
  // what was allowed against what was proposed, and authorization not yet run.
  await scrollRegion(page, "#pg-compare", -140);
  await shot(page, "playground-selected");

  // -- 4. ALLOW ----------------------------------------------------------
  let verdict = await askAuthorization(page);
  await scrollRegion(page, "#pg-outcome-region", -150);
  await shot(page, "playground-allow");
  if (verdict !== "ALLOW") throw new Error(`expected ALLOW, saw ${verdict}`);

  // -- 5. REVIEW: deterministic undeclared-billing scenario ---------------
  await context.close();
  ({ context, page } = await freshPage(browser));
  await page.click('[data-scenario="billing-undeclared"]');
  verdict = await waitForDecision(page);
  await scrollRegion(page, "#pg-outcome-region", -150);
  await shot(page, "playground-review");
  if (verdict !== "REVIEW") throw new Error(`expected REVIEW, saw ${verdict}`);

  // -- 6. BLOCK: the prohibited-content journey --------------------------
  await context.close();
  ({ context, page } = await freshPage(browser));
  await page.click('[data-scenario="prohibited-content"]');
  verdict = await waitForDecision(page);
  await scrollRegion(page, "#pg-outcome-region", -150);
  await shot(page, "playground-block");
  if (verdict !== "BLOCK") throw new Error(`expected BLOCK, saw ${verdict}`);

  // -- 7. Price mutation rejected at the execution gate ------------------
  await context.close();
  ({ context, page } = await freshPage(browser));
  await page.click('[data-scenario="price-mutation"]');
  verdict = await waitForDecision(page);
  if (verdict !== "ALLOW") throw new Error(`expected ALLOW before mutation, saw ${verdict}`);
  await page.waitForSelector("#pg-mutate-price", { timeout: 20000 });
  await page.click("#pg-mutate-price");
  await page.waitForSelector(".pggate__binding", { timeout: 20000 });
  await scrollRegion(page, "#pg-execution-region");
  await shot(page, "playground-price-mutation");

  // -- 8. Revocation -----------------------------------------------------
  await context.close();
  ({ context, page } = await freshPage(browser));
  await page.click('[data-scenario="revoked-after-allow"]');
  await waitForDecision(page);
  await page.waitForSelector("#pg-revoke", { timeout: 20000 });
  await page.click("#pg-revoke");
  await page.waitForTimeout(900);
  await page.click("#pg-execute");
  await page.waitForTimeout(1400);
  await scrollRegion(page, "#pg-execution-region");
  await shot(page, "playground-revocation");

  // -- 9. Marketplace readiness -----------------------------------------
  await context.close();
  ({ context, page } = await freshPage(browser));
  await page.click("#tab-observe");
  await page.waitForTimeout(400);
  await page.fill("#purchase-intent", "Buy a study lamp under INR 2,000. No subscriptions.");
  await page.click("#run-button");
  const unreadyListing = page.locator('.listing[data-transactable="false"]').first();
  await unreadyListing.waitFor({ timeout: 25000 });
  await unreadyListing.scrollIntoViewIfNeeded();
  await page.evaluate(() => window.scrollBy(0, -130));
  await shot(page, "marketplace-readiness");

  // -- 10. Scale lab -----------------------------------------------------
  await page.click("#tab-scale");
  await page.waitForTimeout(500);
  await page.evaluate(() => window.scrollTo(0, 0));
  await shot(page, "scale-lab");

  // -- 11. Mobile --------------------------------------------------------
  await context.close();
  context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
  });
  page = await context.newPage();
  await page.goto(BASE, { waitUntil: "networkidle" });
  await search(page, "desk lamp under 2000");
  await scrollRegion(page, "#pg-candidates", -60);
  await shot(page, "playground-mobile");

  await context.close();
  await browser.close();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
