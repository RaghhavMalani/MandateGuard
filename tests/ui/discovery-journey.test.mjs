import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  JOURNEY_STAGES,
  buildJourney,
  discoverySummaryLine,
  escapeHtml,
  journeyProgress,
  renderCatalogProvenance,
  renderDiscoveryCandidate,
  renderDiscoveryResults,
  renderEngineeringQuality,
  renderJourney,
  renderMandatePanel,
  renderModelQuality,
  renderSystemScale,
} from "../../src/mandateguard/product/static/app.js";

const html = readFileSync(
  fileURLToPath(new URL("../../src/mandateguard/product/static/index.html", import.meta.url)),
  "utf8",
);
const css = readFileSync(
  fileURLToPath(new URL("../../src/mandateguard/product/static/app.css", import.meta.url)),
  "utf8",
);

const crawledCandidate = {
  catalog_product_id: "flipkart.abc123",
  source_product_id: "abc123",
  source: "flipkart",
  title: "Tootpado Cartoon LED Desk Light",
  brand: "Tootpado",
  category_path: ["Home Decor & Festive Needs", "Lighting"],
  top_category: "Home Decor & Festive Needs",
  price_minor: 34900,
  currency: "INR",
  listed_on: "flipkart.com",
  trust_tier: "DISCOVERY_LISTING",
  match: {
    score: 0.93,
    lexical_score: 1.0,
    dense_score: 0.61,
    matched_terms: ["desk", "lamp"],
    headline: "Matches “desk”, “lamp” in its title.",
    detail: "INR 349 is within your INR 2,000 per-unit ceiling; filed under Lighting.",
  },
  classification: {
    predicted_category: "Home Decor & Festive Needs",
    mismatch: { severity: "NONE", rationale: "The declared category matches its own text." },
  },
  anomaly: {
    band: "ELEVATED",
    score: 0.2,
    authorization_authority: "NONE",
    effect: "INVESTIGATION_PRIORITY_ONLY",
    features: [
      {
        feature_id: "missing_trusted_evidence",
        question: "Is there any authoritative evidence at all?",
        finding: "No merchant-controlled evidence exists for this listing.",
        triggered: true,
        value: 1,
        weight: 3,
      },
      {
        feature_id: "price_vs_category",
        question: "Is this priced like other products in its category?",
        finding: "0.6x the category median; within the usual range.",
        triggered: false,
        value: 0,
        weight: 1,
      },
    ],
  },
  transactability: {
    status: "REVIEW REQUIRED",
    next_action: "The merchant must expose authoritative SKU terms.",
    authority_notice: "Diagnostic only. This surface cannot authorize payments.",
    checks: [
      { label: "DISCOVERABLE", status: "YES", detail: "Indexed from flipkart." },
      { label: "PRICE AVAILABLE", status: "YES", detail: "INR 349.00 is published." },
      { label: "MERCHANT IDENTITY", status: "UNRESOLVED", detail: "A marketplace is not a seller of record." },
      { label: "SKU TRUST EVIDENCE", status: "UNRESOLVED", detail: "No merchant vouches for this identifier." },
    ],
  },
  trusted_evidence_count: 0,
  transactable: false,
  stage: "EVIDENCE_INCOMPLETE",
};

const registeredCandidate = {
  ...crawledCandidate,
  catalog_product_id: "mandateguard.def456",
  source_product_id: "merchant-scholarly/studyglow-desk-lamp",
  source: "mandateguard",
  title: "StudyGlow Desk Lamp",
  listed_on: "merchant-scholarly",
  classification: { predicted_category: "DECLARED_BY_MERCHANT" },
  transactability: {
    ...crawledCandidate.transactability,
    status: "EVIDENCE READY",
    next_action: "The authorization controller decides separately.",
  },
  trusted_evidence_count: 2,
  transactable: true,
  stage: "MATCHED",
};

const discovery = {
  mandate: {
    raw_text: "Buy a study lamp under Rs 2000. No subscriptions.",
    max_total_minor: 200000,
    recurring_allowed: false,
    unresolved: [],
  },
  mandate_plain_english: [
    "Spend no more than INR 2,000.00 in total.",
    "One-time payment only. No subscription or recurring charge.",
  ],
  retrieval: {
    catalog_listings: 17702,
    candidates_considered: 176,
    filtered_out: { "priced above your INR 2,000 ceiling": 16 },
    duplicates_suppressed: 4,
    retrieval_ms: 9.1,
  },
  candidates: [registeredCandidate, crawledCandidate],
  summary: { listings: 2, evidence_ready: 1, review_required: 1 },
};

/* ------------------------------------------------------------------ */
/* Story-first opening                                                 */
/* ------------------------------------------------------------------ */

test("the page opens by explaining the problem before any control", () => {
  const opening = html.indexOf('class="opening"');
  const console_ = html.indexOf('class="console"');
  assert.ok(opening > 0 && console_ > opening, "the console must come after the explanation");
  assert.match(html, /An AI agent can decide what to buy/);
  assert.match(html, /Deciding is not the same as being allowed to pay/);
  assert.match(html, /MandateGuard sits between the AI buyer and payment execution/);
});

test("the thesis line appears verbatim", () => {
  assert.match(html, /The agent decides\. MandateGuard verifies\. Razorpay executes\./);
});

test("all three outcomes are defined in plain English on the first screen", () => {
  assert.match(html, /The purchase is authorized and may proceed/);
  assert.match(html, /The proposal violates your mandate/);
  assert.match(html, /not enough trusted evidence to decide safely/);
});

test("the opening does not lead with tier terminology", () => {
  const opening = html.slice(html.indexOf('class="opening"'), html.indexOf('id="console"'));
  assert.doesNotMatch(opening, /Tier A/);
  assert.doesNotMatch(opening, /Tier B/);
  assert.doesNotMatch(opening, /Tier C/);
});

test("free text is the primary input and examples are secondary", () => {
  const label = html.indexOf("What should the agent buy?");
  const examples = html.indexOf("Or start from an example");
  assert.ok(label > 0 && examples > label, "examples must follow the free-text field");
  assert.match(html, /placeholder="Buy wireless headphones below Rs 4,000/);
});

/* ------------------------------------------------------------------ */
/* The nine-stage journey                                              */
/* ------------------------------------------------------------------ */

test("the journey has exactly the nine chronological stages", () => {
  assert.deepEqual(
    JOURNEY_STAGES.map((item) => item.id),
    [
      "USER_INTENT",
      "AGENT_SEARCH",
      "PRODUCT_SELECTED",
      "MANDATE_EXTRACTED",
      "ML_ANALYSIS",
      "TRUSTED_EVIDENCE",
      "AUTHORIZATION",
      "PAYMENT_GATE",
      "OUTCOME",
    ],
  );
});

test("every stage carries a plain-English explanation", () => {
  for (const stage of JOURNEY_STAGES) {
    assert.ok(stage.plain.length > 10, `${stage.id} has no plain-English line`);
    assert.doesNotMatch(stage.plain, /Tier [ABC]/);
  }
});

test("an empty journey renders every stage as not yet reached", () => {
  const stages = buildJourney({});
  assert.equal(stages.length, 9);
  assert.ok(stages.every((item) => item.status === "WAITING"));
  assert.equal(journeyProgress(stages), 0);
});

test("a search fills the discovery stages and leaves authorization waiting", () => {
  const stages = buildJourney({ discovery });
  const byId = new Map(stages.map((item) => [item.id, item]));
  assert.equal(byId.get("USER_INTENT").status, "DONE");
  assert.equal(byId.get("AGENT_SEARCH").status, "DONE");
  assert.match(byId.get("AGENT_SEARCH").detail, /17,702 listings/);
  assert.equal(byId.get("MANDATE_EXTRACTED").status, "DONE");
  assert.equal(byId.get("ML_ANALYSIS").status, "DONE");
  assert.equal(byId.get("AUTHORIZATION").status, "WAITING");
});

test("selecting an unvouched listing stops the journey at review required", () => {
  const stages = buildJourney({
    discovery,
    selection: { title: crawledCandidate.title, transactable: false, status: "REVIEW REQUIRED" },
  });
  const byId = new Map(stages.map((item) => [item.id, item]));
  assert.equal(byId.get("TRUSTED_EVIDENCE").status, "REVIEW");
  assert.equal(byId.get("AUTHORIZATION").status, "REVIEW");
  assert.match(byId.get("PAYMENT_GATE").detail, /Payment-provider calls: 0/);
  assert.match(byId.get("OUTCOME").detail, /No money moved, and none could have/);
});

test("an ALLOW run marks every stage as passed", () => {
  const snapshot = {
    state: "COMPLETE",
    timeline: [{ id: "EVIDENCE_RETRIEVAL", status: "PASS" }],
    result: {
      decision: "ALLOW",
      decision_reason: "All applicable checks passed.",
      evidence: { trusted_evidence_count: 2 },
      authorization: { final_controller: "ALLOW" },
      execution: {
        status: "ORDER_CREATED",
        razorpay_calls: 1,
        order: { amount: 129900, currency: "INR" },
      },
      buyer: { mandate: "Buy a lamp", product: "StudyGlow Desk Lamp" },
    },
  };
  const stages = buildJourney({
    discovery,
    selection: { title: "StudyGlow Desk Lamp", transactable: true, status: "READY" },
    snapshot,
  });
  assert.ok(stages.every((item) => item.status !== "WAITING"), "no stage may stay unreached");
  assert.equal(journeyProgress(stages), 1);
  const rendered = renderJourney(stages);
  assert.match(rendered, /data-state="pass"/);
  assert.doesNotMatch(rendered, /data-state="waiting"/);
});

test("a BLOCK marks the payment gate as never reached", () => {
  const stages = buildJourney({
    snapshot: {
      timeline: [],
      result: {
        decision: "BLOCK",
        decision_reason: "Excluded category.",
        evidence: { trusted_evidence_count: 2 },
        execution: { razorpay_calls: 0 },
        buyer: { mandate: "Buy a course", product: "Market Edge" },
      },
    },
  });
  const byId = new Map(stages.map((item) => [item.id, item]));
  assert.match(byId.get("PAYMENT_GATE").detail, /Never reached. Payment-provider calls: 0/);
  assert.match(byId.get("OUTCOME").detail, /Nothing was charged/);
});

test("the journey renders indexes, names, and statuses", () => {
  const rendered = renderJourney(buildJourney({ discovery }));
  assert.match(rendered, /journey__index">01</);
  assert.match(rendered, /journey__index">09</);
  assert.match(rendered, /User intent/);
  assert.match(rendered, /Payment gate/);
});

/* ------------------------------------------------------------------ */
/* Discovery results                                                   */
/* ------------------------------------------------------------------ */

test("a discovery listing explains why it matched", () => {
  const rendered = renderDiscoveryCandidate(crawledCandidate);
  assert.match(rendered, /Matches “desk”, “lamp” in its title/);
  assert.match(rendered, /within your INR 2,000 per-unit ceiling/);
});

test("a crawled listing is labelled as a discovery listing, not a merchant", () => {
  const rendered = renderDiscoveryCandidate(crawledCandidate);
  assert.match(rendered, /DISCOVERY LISTING/);
  assert.match(rendered, /REVIEW REQUIRED/);
  assert.match(rendered, /data-transactable="false"/);
  assert.match(rendered, /WHY CAN'T I BUY THIS\?/);
});

test("a registered listing offers authorization and says so", () => {
  const rendered = renderDiscoveryCandidate(registeredCandidate);
  assert.match(rendered, /REGISTERED MERCHANT/);
  assert.match(rendered, /EVIDENCE READY/);
  assert.match(rendered, /data-transactable="true"/);
  assert.match(rendered, /AUTHORIZE THIS PURCHASE/);
});

test("every listing carries its transactability checks and authority notice", () => {
  const rendered = renderDiscoveryCandidate(crawledCandidate);
  assert.match(rendered, /MERCHANT IDENTITY/);
  assert.match(rendered, /data-readiness="UNRESOLVED"/);
  assert.match(rendered, /cannot authorize payments/);
});

test("only triggered defensive signals are shown, with their question", () => {
  const rendered = renderDiscoveryCandidate(crawledCandidate);
  assert.match(rendered, /Is there any authoritative evidence at all\?/);
  assert.doesNotMatch(rendered, /Is this priced like other products/);
});

test("a listing with no triggered signal says so rather than showing nothing", () => {
  const clean = { ...crawledCandidate, anomaly: { ...crawledCandidate.anomaly, features: [] } };
  assert.match(renderDiscoveryCandidate(clean), /No defensive signal fired/);
});

test("discovery escapes untrusted listing text", () => {
  const hostile = {
    ...crawledCandidate,
    title: '<img src=x onerror="alert(1)">',
  };
  const rendered = renderDiscoveryCandidate(hostile);
  assert.doesNotMatch(rendered, /<img src=x/);
  assert.match(rendered, /&lt;img src=x/);
});

test("an empty result set explains itself without relaxing the constraint", () => {
  const rendered = renderDiscoveryResults({ candidates: [] });
  assert.match(rendered, /NO LISTING MATCHED THAT INTENT/);
  assert.match(rendered, /Neither is a reason to relax the constraint/);
});

test("the mandate panel shows what was read and what was ruled out", () => {
  const rendered = renderMandatePanel(discovery);
  assert.match(rendered, /Spend no more than INR 2,000.00 in total/);
  assert.match(rendered, /priced above your INR 2,000 ceiling/);
  assert.match(rendered, /not a ranking preference/);
});

test("an unstated constraint is surfaced rather than hidden", () => {
  const rendered = renderMandatePanel({
    ...discovery,
    mandate: { ...discovery.mandate, unresolved: ["RECURRENCE_STANCE_ABSENT"] },
  });
  assert.match(rendered, /NOT STATED/);
  assert.match(rendered, /Recurrence stance absent/);
});

test("the summary line reports catalog size and how many are transactable", () => {
  const line = discoverySummaryLine(discovery);
  assert.match(line, /17,702 listings searched/);
  assert.match(line, /1 of 2 shown are transactable today/);
});

/* ------------------------------------------------------------------ */
/* Measured evidence, kept apart                                       */
/* ------------------------------------------------------------------ */

test("system scale reports catalog, index, and latency without model metrics", () => {
  const rendered = renderSystemScale({
    available: true,
    catalog_listings: 17702,
    categories: 26,
    index_bytes: 7317619,
    catalog_bytes: 4933678,
    cold_load_seconds: 0.264,
    resident_memory_mb: 70.3,
    p50_ms: 9.065,
    p95_ms: 18.845,
    p99_ms: 22.308,
    queries_per_second: 87.63,
    queries_executed: 1525,
    caveat: "Single process, one machine.",
    source: "artifacts/engineering/discovery/scale_benchmark.json",
  });
  assert.match(rendered, /Catalog SKUs/);
  assert.match(rendered, /17,702/);
  assert.match(rendered, /Retrieval P95/);
  assert.match(rendered, /Single process, one machine/);
  assert.doesNotMatch(rendered, /Macro F1/);
  assert.doesNotMatch(rendered, /Python tests/);
});

test("an unrecorded benchmark reports itself missing rather than showing a stale figure", () => {
  const rendered = renderSystemScale({ available: false, reason: "No scale benchmark recorded." });
  assert.match(rendered, /No scale benchmark recorded/);
  assert.doesNotMatch(rendered, /Catalog SKUs/);
});

test("model quality is labelled advisory and never merged with authorization", () => {
  const rendered = renderModelQuality({
    available: true,
    classifier: {
      model: "linear_svc",
      classes: 22,
      macro_f1: 0.9436,
      weighted_f1: 0.9747,
      accuracy: 0.9749,
      top_2_accuracy: 0.9896,
      train: 12061,
      validation: 2583,
      test: 2586,
      advisory_only: true,
    },
    retrieval: {
      configuration: "lexical_only_alpha_1.00__deduplicated",
      recall_at_5: 0.625,
      recall_at_10: 0.6121,
      mrr: 0.6949,
      queries: 44,
      distinct_title_fraction: 1,
    },
    negative_results: [
      { finding: "The learned dense retriever did not beat BM25.", detail: "Recall@10 fell." },
    ],
    boundary: "Model quality is not authorization accuracy.",
  });
  assert.match(rendered, /Macro F1/);
  assert.match(rendered, /Recall@10/);
  assert.match(rendered, /This model cannot allow a payment/);
  assert.match(rendered, /not authorization accuracy/);
  assert.match(rendered, /frozen before the test set was scored/);
  assert.doesNotMatch(rendered, /Catalog SKUs/);
});

test("negative results are published in the interface, not only in the repository", () => {
  const rendered = renderModelQuality({
    available: true,
    classifier: {},
    retrieval: {},
    negative_results: [
      { finding: "An unsupervised anomaly detector was rejected.", detail: "AUC 0.56 vs 0.99." },
    ],
    boundary: "",
  });
  assert.match(rendered, /WHAT WE BUILT, MEASURED, AND DID NOT SHIP/);
  assert.match(rendered, /An unsupervised anomaly detector was rejected/);
});

test("engineering quality reports test counts and denies they are scale", () => {
  const rendered = renderEngineeringQuality({ python: 873, ui: 39 });
  assert.match(rendered, /Python tests/);
  assert.match(rendered, /873/);
  assert.match(rendered, /not a\s+measurement of scale/);
});

test("the measured view keeps the four kinds of evidence in separate sections", () => {
  for (const id of [
    "system-scale-panel",
    "model-quality-panel",
    "measured-panel",
    "engineering-panel",
  ]) {
    assert.ok(html.includes(`id="${id}"`), `${id} is missing from the measured view`);
  }
  assert.match(html, /System scale/);
  assert.match(html, /Model quality/);
  assert.match(html, /Authorization evidence/);
  assert.match(html, /Engineering quality/);
});

/* ------------------------------------------------------------------ */
/* Catalog provenance                                                  */
/* ------------------------------------------------------------------ */

test("catalog provenance names the licence and denies it is evidence", () => {
  const rendered = renderCatalogProvenance({
    available: true,
    catalog: { listings: 17702 },
    provenance: {
      display_name: "Flipkart Products",
      publisher: "PromptCloud",
      licence: "CC BY-SA 4.0",
      catalog_sha256: "abc123",
      attribution: "Published under CC BY-SA 4.0.",
      trust_tier: "DISCOVERY_LISTING",
      trust_note: "Not merchant authorization evidence.",
    },
  });
  assert.match(rendered, /CC BY-SA 4\.0/);
  assert.match(rendered, /PromptCloud/);
  assert.match(rendered, /DISCOVERY_LISTING/);
  assert.match(rendered, /Not merchant authorization evidence/);
});

test("an unbuilt catalog reports why rather than showing an empty shelf", () => {
  const rendered = renderCatalogProvenance({ available: false, reason: "Not built here." });
  assert.match(rendered, /Not built here/);
});

/* ------------------------------------------------------------------ */
/* Layout                                                              */
/* ------------------------------------------------------------------ */

test("every new multi-column block collapses to one column on small viewports", () => {
  const collapse = css.slice(css.indexOf("@media (max-width: 1000px)"));
  for (const selector of [".exchange", ".outcomes", ".quality", ".mandate"]) {
    assert.ok(collapse.includes(selector), `${selector} never collapses`);
  }
});

test("the attack grid header declares five columns", () => {
  const geometry = css.slice(css.indexOf(".attackhead,"), css.indexOf(".attackhead {"));
  assert.equal((geometry.match(/minmax\(0,/g) || []).length, 5);
});

test("the payment-reached flag is styled distinctly for yes and no", () => {
  assert.match(css, /\.paymentflag\[data-reached="NO"\]/);
  assert.match(css, /\.paymentflag\[data-reached="YES"\]/);
});
