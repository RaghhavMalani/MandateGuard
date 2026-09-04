import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  PLAYGROUND_RAIL,
  escapeHtml,
  railProgress,
  railStates,
  renderChosenProduct,
  renderClarificationRequired,
  renderGapFigure,
  renderJudgeHealth,
  renderNoMatch,
  renderOnboardedResult,
  renderOnboardingForm,
  renderPlaygroundCandidate,
  renderPlaygroundCandidates,
  renderPlaygroundExecution,
  renderPlaygroundFollowUps,
  renderPlaygroundMandate,
  renderPlaygroundRail,
  renderPlaygroundVerdict,
  renderPlaygroundWhy,
  renderReadiness,
  renderScaleWorlds,
  renderScenarioGrid,
  renderSpendingLimitPrompt,
  renderTryThese,
  renderWhyFound,
} from "../../src/mandateguard/product/static/app.js";

const HTML = readFileSync(
  fileURLToPath(new URL("../../src/mandateguard/product/static/index.html", import.meta.url)),
  "utf8",
);
const CSS = readFileSync(
  fileURLToPath(new URL("../../src/mandateguard/product/static/app.css", import.meta.url)),
  "utf8",
);

const CANDIDATE = {
  catalog_product_id: "sandbox.0123456789abcdef01234567",
  merchant_id: "sandbox-acme-audio",
  merchant: "Acme Audio (Synthetic)",
  sku: "audio-headphones-026",
  name: "Kestrel Wireless Headphones M60",
  brand: "Kestrel",
  category: "Headphones",
  category_group: "Electronics",
  description: "A synthetic sandbox listing.",
  price_minor: 89900,
  currency: "INR",
  billing_model: "ONE_TIME",
  recurring: false,
  recurrence_declaration: "SETTLED_ONCE",
  effective_from: "2026-09-01T00:00:00Z",
  evidence_version: "v1",
  world: "SANDBOX",
  synthetic: true,
  why_found: {
    category_match: "Headphones",
    matched_terms: ["wireless", "headphones"],
    brand_match: null,
    within_budget: true,
    lexical_score: 18.4,
    category_score: 9,
    semantic_similarity: 0.82,
    semantic_method: "DETERMINISTIC_CATEGORY_SYNONYM",
    exact_phrase_match: "wireless headphones",
    total_score: 41.2,
  },
  readiness: {
    merchant_identity: "DECLARED",
    sku_evidence: "DECLARED",
    authoritative_price: "DECLARED",
    billing_model: "DECLARED",
    content_classification: "DECLARED",
    intended_use: "DECLARED",
    evidence_version: "CURRENT",
  },
};

/* ------------------------------------------------------------------ */
/* Navigation and page structure                                       */
/* ------------------------------------------------------------------ */

test("the Playground is the first tab and the default panel", () => {
  const order = [...HTML.matchAll(/data-view="([a-z]+)"/g)].map((match) => match[1]);
  assert.deepEqual(order, [
    "playground",
    "observe",
    "attack",
    "scale",
    "evidence",
    "evaluation",
  ]);
  assert.match(HTML, /id="tab-playground"[\s\S]*?aria-selected="true"/);
  // Only the Playground panel is visible on load.
  assert.match(HTML, /<section id="view-playground" class="view"[^>]*tabindex="-1">/);
  assert.match(HTML, /<section id="view-observe"[^>]*hidden>/);
  assert.match(HTML, /<section id="view-scale"[^>]*hidden>/);
});

test("the sandbox is declared before the input, not after it", () => {
  const badge = HTML.indexOf("SIMULATED MERCHANT SANDBOX");
  const explanation = HTML.indexOf("Nothing here represents a live marketplace or real");
  const input = HTML.indexOf('id="pg-intent"');
  assert.ok(badge > 0 && explanation > badge);
  assert.ok(explanation < input, "the sandbox notice must precede the instruction field");
});

test("a first-time reader gets the whole idea before any technical detail", () => {
  const primer = HTML.indexOf("pg-primer");
  const rail = HTML.indexOf("pg-rail-region");
  assert.ok(primer > 0 && primer < rail);
  for (const line of [
    "An AI agent can choose what to buy",
    "Choosing is not permission to spend",
    "execution may proceed",
    "the request breaks your mandate",
    "the evidence is missing or contradicts itself",
    "it never authorizes payment",
  ]) {
    assert.ok(HTML.includes(line), `missing primer line: ${line}`);
  }
});

test("every Playground panel the script writes into exists in the markup", () => {
  for (const id of [
    "pg-catalog-meta",
    "pg-try-row",
    "pg-intent",
    "pg-search-button",
    "pg-error",
    "pg-rail-region",
    "pg-rail",
    "pg-results-region",
    "pg-results-meta",
    "pg-mandate-panel",
    "pg-limit-panel",
    "pg-candidates",
    "pg-nomatch",
    "pg-chosen-region",
    "pg-chosen-panel",
    "pg-outcome-region",
    "pg-verdict",
    "pg-why",
    "pg-execution",
    "pg-followups",
    "pg-scenario-grid",
    "pg-gap-figure",
    "scale-worlds-panel",
    "judge-health-panel",
    "onboard-region",
    "onboard-panel",
  ]) {
    assert.ok(HTML.includes(`id="${id}"`), `missing element: ${id}`);
  }
});

/* ------------------------------------------------------------------ */
/* The live rail                                                       */
/* ------------------------------------------------------------------ */

test("the rail is waiting before anything has been asked", () => {
  const states = railStates({});
  assert.equal(states.INTENT, "waiting");
  assert.equal(states.DECISION, "waiting");
  assert.equal(states.PAYMENT, "waiting");
  assert.equal(railProgress({}), 0);
});

test("the rail advances only as far as the run actually got", () => {
  const states = railStates({
    intent: "headphones under 5000",
    candidates: [CANDIDATE],
    selected: CANDIDATE,
    evidence: [{}, {}, {}],
    snapshot: { state: "RUNNING", result: null },
  });
  assert.equal(states.INTENT, "done");
  assert.equal(states.CANDIDATES, "done");
  assert.equal(states.MANDATEGUARD, "active");
  // No verdict yet, so the rail must not show one.
  assert.equal(states.DECISION, "waiting");
  assert.equal(states.PAYMENT, "waiting");
});

test("the rail reads the decision from the run and never infers it", () => {
  for (const decision of ["ALLOW", "BLOCK", "REVIEW"]) {
    const states = railStates({
      intent: "x",
      candidates: [CANDIDATE],
      selected: CANDIDATE,
      evidence: [{}],
      snapshot: {
        state: "COMPLETE",
        result: { decision, execution: { status: decision === "ALLOW" ? "ORDER_CREATED" : "NOT_CALLED" } },
      },
    });
    assert.equal(states.DECISION, decision.toLowerCase());
    assert.equal(states.PAYMENT, decision === "ALLOW" ? "done" : "stopped");
  }
});

test("a BLOCK never lights the payment stage", () => {
  const html = renderPlaygroundRail({
    intent: "x",
    candidates: [CANDIDATE],
    selected: CANDIDATE,
    evidence: [{}],
    snapshot: {
      state: "COMPLETE",
      result: { decision: "BLOCK", execution: { status: "NOT_CALLED", razorpay_calls: 0 } },
    },
  });
  assert.match(html, /data-stage="PAYMENT" *>|data-state="stopped"/);
  assert.match(html, /Not reached/);
  assert.doesNotMatch(html, /Simulated offline order created/);
});

test("the rail names all eight stages in order", () => {
  const html = renderPlaygroundRail({ intent: "x" });
  let cursor = -1;
  for (const stage of PLAYGROUND_RAIL) {
    const index = html.indexOf(stage.label);
    assert.ok(index > cursor, `stage out of order: ${stage.label}`);
    cursor = index;
  }
});

/* ------------------------------------------------------------------ */
/* Candidates and readiness                                            */
/* ------------------------------------------------------------------ */

test("readiness rows are addressable and toned by what the evidence says", () => {
  const html = renderReadiness({
    merchant_identity: "DECLARED",
    billing_model: "NOT_DECLARED",
    content_classification: "CONFLICTED",
    evidence_version: "CURRENT",
  });
  assert.match(html, /data-field="merchant_identity" data-tone="ok"/);
  assert.match(html, /data-field="billing_model" data-tone="missing"/);
  assert.match(html, /data-field="content_classification" data-tone="conflict"/);
  assert.match(html, /VERIFIED/);
  assert.match(html, /NOT DECLARED/);
  assert.match(html, /CONFLICTED/);
});

test("a candidate shows product, price, merchant, category and why it was found", () => {
  const html = renderPlaygroundCandidate(CANDIDATE);
  assert.match(html, /Kestrel Wireless Headphones M60/);
  assert.match(html, /899/);
  assert.match(html, /Acme Audio \(Synthetic\)/);
  assert.match(html, /Headphones/);
  assert.match(html, /WHY THE AGENT FOUND IT/);
  assert.match(html, /AGENT READINESS/);
  assert.match(html, /CHECK AUTHORIZATION/);
});

test("hashes and identifiers stay behind a disclosure rather than on the card", () => {
  const html = renderPlaygroundCandidate(CANDIDATE);
  const summaryIndex = html.indexOf("Technical detail");
  assert.ok(summaryIndex > 0);
  assert.ok(html.indexOf("audio-headphones-026") > summaryIndex);
  assert.ok(html.indexOf("sandbox-acme-audio") > summaryIndex);
});

test("a renewing listing is flagged on the card before it is chosen", () => {
  const html = renderPlaygroundCandidate({ ...CANDIDATE, recurring: true });
  assert.match(html, /RENEWING PLAN/);
});

test("candidate rendering escapes anything a merchant record could contain", () => {
  const html = renderPlaygroundCandidate({
    ...CANDIDATE,
    name: '<img src=x onerror="alert(1)">',
    merchant: "</script><script>alert(2)</script>",
  });
  assert.doesNotMatch(html, /<img src=x/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /&lt;img src=x/);
});

test("why-found names each of the signals that put a listing on the page", () => {
  const html = renderWhyFound(CANDIDATE.why_found);
  for (const label of [
    "Semantic similarity",
    "Lexical signal",
    "Budget",
    "Brand preference",
    "Category match",
  ]) {
    assert.ok(html.includes(label), `missing signal: ${label}`);
  }
  assert.match(html, /wireless, headphones/);
  assert.match(html, /within your limit/);
  assert.match(html, /Headphones/);
});


test("why-found reports an over-budget candidate honestly", () => {
  const html = renderWhyFound({ within_budget: false, matched_terms: ["lamp"] });
  assert.match(html, /above your limit/);
});


test("why-found admits when a signal is absent rather than inventing one", () => {
  const html = renderWhyFound({ within_budget: true, matched_terms: [] });
  assert.match(html, /no category-synonym signal/);
  assert.match(html, /no direct term match/);
  assert.match(html, /none stated/);
});

test("candidate list marks the chosen one", () => {
  const html = renderPlaygroundCandidates(
    { candidates: [CANDIDATE, { ...CANDIDATE, catalog_product_id: "sandbox.other" }] },
    "sandbox.other",
  );
  assert.match(html, /data-product="sandbox\.other"\s*\n?\s*data-selected="true"/);
});

/* ------------------------------------------------------------------ */
/* Mandate, spending limit, and the empty result                       */
/* ------------------------------------------------------------------ */

test("the mandate panel says what was read and disclaims its own authority", () => {
  const html = renderPlaygroundMandate({
    mandate_plain_english: [
      "Spend at most INR 5,000.00 in total (taken from your instruction).",
      "Nothing involving subscriptions.",
    ],
  });
  assert.match(html, /Spend at most INR 5,000\.00/);
  assert.match(html, /advisory step with no authority/);
  assert.match(html, /enforced by the controller, not by the reader/);
});

test("an instruction with no ceiling asks for one instead of inventing it", () => {
  const html = renderSpendingLimitPrompt({ spending_limit_required: true }, 129900);
  assert.match(html, /You did not state a spending limit/);
  assert.match(html, /will not authorize a purchase without one/);
  assert.match(html, /id="pg-limit-input"/);
  assert.match(html, /value="1299"/);
});

test("a stated ceiling does not raise the prompt", () => {
  assert.equal(renderSpendingLimitPrompt({ spending_limit_required: false }, 100), "");
});

test("an empty result names the constraint that excluded each near miss", () => {
  const html = renderNoMatch({
    candidates: [],
    no_match_message: "No suitable sandbox product matched all of your constraints.",
    constraints_applied: ["Price at most INR 200.00", "No renewing subscription"],
    near_misses: [
      {
        name: "Pinewood Laptop",
        price_minor: 3299900,
        currency: "INR",
        excluded_by: "MAX_TOTAL",
        explanation: "Priced at INR 32,999.00, above your INR 200.00 limit.",
      },
    ],
  });
  assert.match(html, /No suitable sandbox product matched all of your constraints\./);
  assert.match(html, /CONSTRAINTS APPLIED/);
  assert.match(html, /CLOSEST CANDIDATES, AND WHAT EXCLUDED THEM/);
  assert.match(html, /MAX_TOTAL/);
  assert.match(html, /above your INR 200\.00 limit/);
});

test("a result with candidates renders no empty-state panel", () => {
  assert.equal(renderNoMatch({ candidates: [CANDIDATE] }), "");
});

/* ------------------------------------------------------------------ */
/* Chosen product and its evidence                                     */
/* ------------------------------------------------------------------ */

test("the chosen product shows the merchant's evidence text before the verdict", () => {
  const html = renderChosenProduct({
    product: CANDIDATE,
    readiness: CANDIDATE.readiness,
    notice: "SIMULATED MERCHANT SANDBOX. No real money moves.",
    trusted_evidence: [
      {
        evidence_id: "sbev-audio-headphones-026-terms-v1",
        source_kind: "product_terms",
        scope: "PRODUCT",
        text: "Billing model: one-time purchase, settled once at checkout.",
      },
    ],
  });
  assert.match(html, /Billing model: one-time purchase/);
  assert.match(html, /sbev-audio-headphones-026-terms-v1/);
  assert.match(html, /Product terms/);
  assert.match(html, /SIMULATED MERCHANT SANDBOX/);
});

/* ------------------------------------------------------------------ */
/* Verdict, reasons, execution                                         */
/* ------------------------------------------------------------------ */

test("each verdict gets its own headline and its own surface", () => {
  const cases = {
    ALLOW: "Your mandate permits this purchase.",
    BLOCK: "MandateGuard stopped this before payment.",
    REVIEW: "MandateGuard refused to guess.",
  };
  for (const [decision, headline] of Object.entries(cases)) {
    const html = renderPlaygroundVerdict({
      result: { decision },
      explanation: { headline },
    });
    assert.match(html, new RegExp(`data-decision="${decision}"`));
    assert.match(html, new RegExp(decision));
    assert.ok(html.includes(headline));
  }
});

test("the why panel names the failed constraint and the provider-call count", () => {
  const html = renderPlaygroundWhy({
    explanation: {
      why: ["INR 7,999.00 > INR 4,000.00 stated limit"],
      failed_constraints: ["B6"],
      provider_calls: 0,
      external_network_calls: 0,
      controller: "EXISTING_FROZEN_MANDATEGUARD_CONTROLLER",
    },
  });
  assert.match(html, /FAILED CONSTRAINT/);
  assert.match(html, /<code>B6<\/code>/);
  assert.match(html, /PROVIDER CALLS/);
  assert.match(html, /EXISTING_FROZEN_MANDATEGUARD_CONTROLLER/);
});

test("an ALLOW execution is labelled a simulated offline order and nothing stronger", () => {
  const html = renderPlaygroundExecution({
    result: {
      buyer: { merchant: "sandbox-acme-audio", sku: "audio-headphones-026", currency: "INR" },
      execution: {
        status: "ORDER_CREATED",
        order: { order_id: "order_offline_abc", amount: 89900, currency: "INR" },
        razorpay_calls: 1,
        external_network_calls: 0,
      },
    },
  });
  assert.match(html, /SIMULATED OFFLINE ORDER/);
  assert.match(html, /order_offline_abc/);
  assert.match(html, /No external network call was made/);
  assert.match(html, /nothing was captured or settled at a payment provider/);
  // The claim never becomes a Razorpay settlement claim.
  assert.doesNotMatch(html, /captured by Razorpay|settled by Razorpay|payment received/i);
});

test("a stopped execution says payment was not reached and counts zero calls", () => {
  const html = renderPlaygroundExecution({
    result: {
      buyer: { merchant: "sandbox-x", sku: "y" },
      execution: { status: "NOT_CALLED", razorpay_calls: 0, external_network_calls: 0 },
    },
  });
  assert.match(html, /PAYMENT NOT REACHED/);
  assert.match(html, /not created/);
  assert.doesNotMatch(html, /SIMULATED OFFLINE ORDER/);
});

test("a revoked execution shows the consent state at spend time", () => {
  const html = renderPlaygroundExecution({
    result: {
      buyer: { merchant: "sandbox-x", sku: "y" },
      execution: {
        status: "REJECTED_BEFORE_NETWORK",
        reason: "MANDATE_REVOKED",
        consent: { status: "REVOKED" },
        razorpay_calls: 0,
        external_network_calls: 0,
      },
    },
  });
  assert.match(html, /CONSENT AT SPEND TIME/);
  assert.match(html, /REVOKED/);
  assert.match(html, /MANDATE_REVOKED/);
});

test("follow-ups are offered only when the run supports them", () => {
  const authorized = renderPlaygroundFollowUps({
    result: { decision: "ALLOW", execution: { status: "AUTHORIZED" } },
  });
  assert.match(authorized, /id="pg-revoke"/);
  assert.match(authorized, /id="pg-execute"/);

  const executed = renderPlaygroundFollowUps({
    result: { decision: "ALLOW", execution: { status: "ORDER_CREATED" } },
  });
  assert.match(executed, /id="pg-replay"/);
  assert.doesNotMatch(executed, /id="pg-revoke"/);

  const blocked = renderPlaygroundFollowUps({
    result: { decision: "BLOCK", execution: { status: "NOT_CALLED" } },
  });
  assert.equal(blocked, "");
});

test("an unrecoverable REVIEW says so instead of offering a button that cannot work", () => {
  const html = renderPlaygroundFollowUps({
    result: {
      decision: "REVIEW",
      execution: { status: "NOT_CALLED" },
      recovery: { action: { enabled: false } },
    },
  });
  assert.doesNotMatch(html, /id="pg-recover"/);
  assert.match(html, /No trusted evidence provider is configured/);
});

test("a replayed capability reports the rejection and the zero extra calls", () => {
  const html = renderPlaygroundFollowUps({
    result: {
      decision: "ALLOW",
      execution: {
        status: "ORDER_CREATED",
        replay: {
          status: "REJECTED_BEFORE_NETWORK",
          reason: "NONCE_ALREADY_USED",
          razorpay_additional_calls: 0,
        },
      },
    },
  });
  assert.match(html, /REJECTED_BEFORE_NETWORK/);
  assert.match(html, /NONCE_ALREADY_USED/);
  assert.match(html, /0 additional provider calls/);
});

/* ------------------------------------------------------------------ */
/* Scenarios, examples, the gap figure, the scale lab                  */
/* ------------------------------------------------------------------ */

test("scenario cards say which world they run in", () => {
  const html = renderScenarioGrid([
    { scenario_id: "safe-purchase", label: "SAFE PURCHASE", story: "s", world: "SANDBOX" },
    { scenario_id: "recoverable-review", label: "RECOVERABLE REVIEW", story: "r", world: "REGISTERED" },
  ]);
  assert.match(html, /data-scenario="safe-purchase"/);
  assert.match(html, /SANDBOX</);
  assert.match(html, /REGISTERED MERCHANT FIXTURES/);
});

test("try-these prompts are human sentences, not test-case names", () => {
  const html = renderTryThese([
    { label: "Buy headphones under ₹5,000", intent: "Buy wireless headphones under INR 5,000." },
  ]);
  assert.match(html, /data-intent="Buy wireless headphones under INR 5,000\."/);
  assert.match(html, /Buy headphones under/);
  assert.doesNotMatch(html, /test_|case_|preset_id/);
});

test("the gap figure keeps the three populations apart", () => {
  const html = renderGapFigure({ marketplace: 17702, sandbox: 3060 });
  assert.match(html, /SEARCHABLE/);
  assert.match(html, /17,702/);
  assert.match(html, /trust gap/);
  assert.match(html, /AGENT-READY/);
  assert.match(html, /3,060/);
  assert.match(html, /AUTHORIZED NOW/);
  // No total anywhere: adding these numbers would be the one wrong move.
  assert.doesNotMatch(html, /20,762/);
});

test("the scale lab refuses to combine populations into one number", () => {
  const html = renderScaleWorlds(
    { discovery: { catalog: { listings: 17702 } }, system_scale: {} },
    { catalog: { products: 3060 } },
  );
  assert.match(html, /DISCOVERY REALITY/);
  assert.match(html, /JUDGE SANDBOX/);
  assert.match(html, /AUTHORIZATION SCALE/);
  assert.match(html, /MODEL QUALITY/);
  assert.match(html, /never combined into a single/);
});

test("the outcome-mix panel labels itself an experience target", () => {
  const html = renderJudgeHealth({
    queries: 120,
    overall: { rates: { ALLOW: 0.93, BLOCK: 0, REVIEW: 0.07, NO_RESULT: 0 }, candidate_found_rate: 1 },
    ordinary: { rates: { ALLOW: 0.99, BLOCK: 0, REVIEW: 0.01, NO_RESULT: 0 } },
    insistent_selection: { rates: { ALLOW: 0, BLOCK: 1, REVIEW: 0, NO_RESULT: 0 } },
  });
  assert.match(html, /120 fixed judge queries/);
  assert.match(html, /Insisting on a flagged listing/);
  assert.match(html, /experience target, not a safety contract/i);
});

test("the outcome-mix panel does not claim to be preregistered", () => {
  const html = renderJudgeHealth({
    queries: 120,
    overall: { rates: { ALLOW: 0.9, BLOCK: 0.02, REVIEW: 0.08, NO_RESULT: 0 }, candidate_found_rate: 1 },
    ordinary: { rates: { ALLOW: 0.99, BLOCK: 0, REVIEW: 0.01, NO_RESULT: 0 } },
    insistent_selection: { rates: { ALLOW: 0, BLOCK: 1, REVIEW: 0, NO_RESULT: 0 } },
  });
  // The questions and the first measured report landed in one commit, so the
  // panel says so rather than borrowing the credibility of a preregistration
  // that did not happen.
  assert.match(html, /landed in one\s+commit/);
  assert.match(html, /not an independently preregistered evaluation/);
  assert.doesNotMatch(html, /\bfrozen\b/);
});

test("a missing outcome report is reported as missing, not faked", () => {
  assert.match(renderJudgeHealth(null), /has not been generated/);
});

/* ------------------------------------------------------------------ */
/* Simulated merchant onboarding                                       */
/* ------------------------------------------------------------------ */

const ONBOARD_FORM = {
  form: {
    simulation: true,
    notice:
      "SIMULATION. This creates a new synthetic sandbox merchant record. The original marketplace listing is not modified and does not become trusted.",
    copied_from_listing: {
      listing_id: "flipkart.abc",
      title: "Tootpado Cartoon LED Desk Light",
      category_label: "Table Lamps",
      classification: "NEUTRAL_DISCOVERY_ATTRIBUTES",
      note: "Only the words and the shelf are carried across.",
    },
    required_declarations: [
      { field: "merchant_display_name", label: "Merchant identity", type: "text", why: "seller of record" },
      { field: "price_minor", label: "Authoritative price", type: "integer", why: "checked against" },
      { field: "billing_model", label: "Billing model", type: "choice", choices: ["ONE_TIME", "RECURRING"], why: "not inferred" },
      { field: "purposes", label: "Documented intended use", type: "multi-choice", choices: ["home use"], why: "checked against" },
    ],
    prefilled: { merchant_display_name: "Lamp Sandbox Merchant", price_minor: null },
  },
};

test("the onboarding form is labelled a simulation and asks for every trusted field", () => {
  const html = renderOnboardingForm(ONBOARD_FORM);
  assert.match(html, /SIMULATION\./);
  assert.match(html, /does not become trusted/);
  assert.match(html, /data-declare="merchant_display_name"/);
  assert.match(html, /data-declare="price_minor"/);
  assert.match(html, /data-declare="billing_model"/);
  assert.match(html, /data-purpose="true"/);
  assert.match(html, /Only the words and the shelf are carried across\./);
});

test("the onboarding form does not prefill a price from the crawled listing", () => {
  const html = renderOnboardingForm(ONBOARD_FORM);
  const priceField = html.slice(html.indexOf('data-declare="price_minor"'));
  assert.match(priceField.slice(0, 200), /value=""/);
});

test("the onboarded result keeps the original listing visibly untrusted", () => {
  const html = renderOnboardedResult({
    notice: "SIMULATION. A new synthetic sandbox merchant record was created.",
    merchant: {
      display_name: "Lamp Co (Synthetic)",
      merchant_id: "sandbox-onboarded-lamp-co-abc123",
      sku: "onboarded-lamp-abc123",
    },
    product: { catalog_product_id: "sandbox.def", name: "Lamp" },
    readiness: { merchant_identity: "DECLARED", billing_model: "DECLARED" },
    source_listing: {
      title: "Tootpado Cartoon LED Desk Light",
      still_untrusted: true,
      note: "The marketplace listing is unchanged.",
    },
  });
  assert.match(html, /NEW SYNTHETIC MERCHANT RECORD/);
  assert.match(html, /sandbox-onboarded-lamp-co-abc123/);
  assert.match(html, /THE ORIGINAL MARKETPLACE LISTING/);
  assert.match(html, /STILL UNTRUSTED/);
  assert.match(html, /data-untrusted="true"/);
  assert.match(html, /RUN AUTHORIZATION AGAINST THE NEW RECORD/);
});

/* ------------------------------------------------------------------ */
/* Accessibility, motion, and mobile                                   */
/* ------------------------------------------------------------------ */

test("the Playground tab participates in the roving tablist", () => {
  assert.match(HTML, /id="tab-playground"[\s\S]*?role="tab"/);
  assert.match(HTML, /aria-controls="view-playground"/);
  assert.match(HTML, /<section id="view-playground"[^>]*role="tabpanel"/);
  assert.match(HTML, /aria-labelledby="tab-playground"/);
});

test("the instruction field is labelled and the error region is announced", () => {
  assert.match(HTML, /<label class="pg-field__label" for="pg-intent">/);
  assert.match(HTML, /id="pg-error" role="alert"/);
  assert.match(HTML, /id="pg-rail" aria-live="polite"/);
});

test("every Playground control is a real button", () => {
  for (const html of [
    renderPlaygroundCandidate(CANDIDATE),
    renderScenarioGrid([{ scenario_id: "a", label: "A", story: "s", world: "SANDBOX" }]),
    renderTryThese([{ label: "x", intent: "y" }]),
    renderPlaygroundFollowUps({ result: { decision: "ALLOW", execution: { status: "AUTHORIZED" } } }),
  ]) {
    const clickable = [...html.matchAll(/<(\w+)[^>]*data-(?:authorize|scenario|intent)=/g)];
    for (const match of clickable) {
      assert.equal(match[1], "button");
    }
    assert.doesNotMatch(html, /<div[^>]*onclick/);
  }
});

test("reduced motion is honoured for the Playground as well", () => {
  const block = CSS.slice(CSS.indexOf("@media (prefers-reduced-motion: reduce)"));
  assert.match(block, /transition-duration: 1ms !important/);
  assert.match(block, /animation-duration: 1ms !important/);
});

test("the Playground reflows to a single column on a phone", () => {
  const mobile = CSS.slice(CSS.lastIndexOf("@media (max-width: 720px)"));
  for (const selector of [".pg-candidates", ".pg-scenario-grid", ".gapfig__row", ".scaleworlds__row"]) {
    assert.ok(mobile.includes(selector), `no mobile rule for ${selector}`);
  }
  assert.match(mobile, /grid-template-columns: 1fr/);
});

test("no Playground surface reaches outside the origin", () => {
  const playgroundCss = CSS.slice(CSS.indexOf("/* Playground"));
  assert.doesNotMatch(playgroundCss, /https?:\/\//);
  assert.doesNotMatch(playgroundCss, /@import/);
  assert.doesNotMatch(HTML.slice(HTML.indexOf('id="view-playground"'), HTML.indexOf('id="view-observe"')), /https?:\/\//);
});

test("escapeHtml is applied to every interpolated field on a candidate", () => {
  const hostile = '"><script>alert(1)</script>';
  const html = renderPlaygroundCandidate({
    ...CANDIDATE,
    catalog_product_id: hostile,
    sku: hostile,
    merchant_id: hostile,
    billing_model: hostile,
    recurrence_declaration: hostile,
    evidence_version: hostile,
    effective_from: hostile,
    category: hostile,
  });
  assert.doesNotMatch(html, /<script>/);
  assert.ok(html.includes(escapeHtml(hostile)));
});

/* ---------------- unresolved requirements ---------------- */

const UNRESOLVED_SEARCH = {
  candidates: [CANDIDATE],
  clarification_required: true,
  clarification_message:
    "I found matching products, but I could not safely interpret one of your requirements",
  constraint_coverage: {
    coverage_status: "UNRESOLVED_HARD_CONSTRAINT",
    recognized_constraints: ["MAX_TOTAL: INR 3,000.00"],
    unresolved_constraint_spans: [
      { cue: "only", strength: "STRONG", start: 28, end: 32, text: "vegan materials only" },
    ],
    blocks_authorization: true,
  },
};

test("an unresolved requirement is quoted back in the buyer's own words", () => {
  const html = renderClarificationRequired(UNRESOLVED_SEARCH);
  assert.match(html, /could not safely\s+interpret one of your requirements/);
  assert.match(html, /vegan materials only/);
  assert.match(html, /data-status="UNRESOLVED_HARD_CONSTRAINT"/);
});

test("the panel says plainly that nothing was issued and nothing was called", () => {
  const html = renderClarificationRequired(UNRESOLVED_SEARCH);
  assert.match(html, /No mandate was issued/);
  assert.match(html, /no capability exists/);
  assert.match(html, /no payment adapter was called/);
});

test("the panel still shows what the reader did understand", () => {
  const html = renderClarificationRequired(UNRESOLVED_SEARCH);
  assert.match(html, /WHAT IT DID READ AS ENFORCEABLE/);
  assert.match(html, /MAX_TOTAL: INR 3,000.00/);
});

test("a fully covered instruction renders no clarification panel at all", () => {
  assert.equal(renderClarificationRequired({ clarification_required: false }), "");
  assert.equal(renderClarificationRequired(null), "");
});

test("authorization is not offerable while a requirement is unresolved", () => {
  const html = renderPlaygroundCandidates(UNRESOLVED_SEARCH);
  assert.match(html, /disabled/);
  assert.match(html, /CLARIFY YOUR REQUIREMENT FIRST/);
  assert.doesNotMatch(html, /CHECK AUTHORIZATION/);
});

test("authorization stays offerable when every requirement resolved", () => {
  const html = renderPlaygroundCandidates({
    candidates: [CANDIDATE],
    clarification_required: false,
  });
  assert.match(html, /CHECK AUTHORIZATION/);
  assert.doesNotMatch(html, /disabled/);
});

test("the clarification panel escapes the text it quotes back", () => {
  const html = renderClarificationRequired({
    clarification_required: true,
    constraint_coverage: {
      coverage_status: "UNRESOLVED_HARD_CONSTRAINT",
      recognized_constraints: ['<img src=x onerror="alert(1)">'],
      unresolved_constraint_spans: [
        { cue: "only", strength: "</span><script>alert(2)</script>", text: "<script>alert(3)</script>" },
      ],
    },
  });
  assert.doesNotMatch(html, /<script>/);
  assert.doesNotMatch(html, /<img src=x/);
  assert.match(html, /&lt;script&gt;/);
});

test("the Playground markup and stylesheet carry the clarification region", () => {
  assert.match(HTML, /id="pg-clarify-panel"/);
  assert.match(CSS, /\.pgclarify\b/);
  assert.match(CSS, /\.pgcard__go:disabled/);
});
