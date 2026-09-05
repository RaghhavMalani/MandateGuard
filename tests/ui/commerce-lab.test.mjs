import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  ATTACK_SURFACES,
  EVALUATION_EVIDENCE,
  SubmissionLock,
  escapeHtml,
  failedConstraint,
  liveModeStatusNote,
  PLAYGROUND_RAIL,
  railStates,
  renderAttackLab,
  renderAuthorizationPanel,
  renderBlockStory,
  renderBoundedScale,
  renderConsentStrip,
  renderDecisionBanner,
  renderEngineeringQuality,
  renderEvidencePanel,
  renderExecutionPanel,
  renderMeasuredEvidence,
  renderNoMatch,
  renderOnboardedResult,
  renderOnboardingForm,
  renderPlaygroundCandidate,
  renderPlaygroundExecution,
  renderPlaygroundVerdict,
  renderProvenance,
  renderResearch,
  renderReviewRecovery,
  renderReviewStory,
  renderRevocationStory,
  renderSpine,
  renderStory,
  renderTransactability,
  renderTryThese,
  spineProgress,
} from "../../src/mandateguard/product/static/app.js";


test("submission lock prevents a second in-flight submission", () => {
  const lock = new SubmissionLock();
  assert.equal(lock.acquire(), true);
  assert.equal(lock.acquire(), false);
  assert.equal(lock.locked, true);
  lock.release();
  assert.equal(lock.acquire(), true);
});


test("HTML rendering escapes untrusted buyer content", () => {
  assert.equal(escapeHtml('<img src=x onerror="alert(1)">'), "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;");
});


test("ALLOW decision is visible as the final controller result", () => {
  const html = renderDecisionBanner({
    decision: "ALLOW",
    decision_reason: "All checks passed.",
  });
  assert.match(html, /FINAL CONTROLLER/);
  assert.match(html, /ALLOW/);
  assert.match(html, /decision-banner--allow/);
});


test("REVIEW is presented as deliberate restraint, not a payment attempt", () => {
  const html = renderDecisionBanner({
    decision: "REVIEW",
    decision_reason: "Trusted evidence is insufficient.",
  });
  assert.match(html, /Human\/evidence review required before execution/);
  assert.match(html, /MandateGuard refused to guess\. No payment was attempted\./);
  assert.match(html, /Trusted evidence is insufficient/);
});


test("BLOCK and REVIEW execution panels both prove zero Razorpay calls", () => {
  for (const decision of ["BLOCK", "REVIEW"]) {
    const html = renderExecutionPanel({
      status: "NOT_CALLED",
      reason: `${decision} prevented execution.`,
      razorpay_calls: 0,
      external_network_calls: 0,
    });
    assert.match(html, /RAZORPAY CALLS/);
    assert.match(html, /<strong>0<\/strong>/);
    assert.match(html, /External network calls<\/span><strong>0<\/strong>/);
  }
});


test("no-evidence state explains why buyer prose cannot authorize", () => {
  const html = renderEvidencePanel({
    retrieval_method: "HYBRID_TFIDF_AND_EMBEDDING",
    top_k: 1,
    trusted_evidence_count: 0,
    cards: [],
    buyer_text: { text: "The buyer says it is safe." },
  });
  assert.match(html, /NO TRUSTED MERCHANT EVIDENCE SELECTED/);
  assert.match(html, /result must route to REVIEW/);
  assert.match(html, /NOT TRUSTED EVIDENCE/);
});


test("semantic cache HIT is rendered separately from the controller", () => {
  const html = renderAuthorizationPanel({
    deterministic: { action: "ALLOW", tier_a: [], tier_b: [] },
    semantic: {
      verdict: "PASS",
      checks: [],
      cache: { status: "HIT", key_prefix: "abc123" },
    },
    final_controller: "ALLOW",
    controller_source: "EXISTING_FROZEN_MANDATEGUARD_CONTROLLER",
  });
  assert.match(html, /SEMANTIC CACHE/);
  assert.match(html, /HIT/);
  assert.match(html, /FINAL CONTROLLER/);
});


test("successful execution renders verified capability and replay rejection", () => {
  const html = renderExecutionPanel({
    status: "ORDER_CREATED",
    environment: "OFFLINE_DEMO_TEST_DOUBLE",
    razorpay_calls: 1,
    external_network_calls: 0,
    capability: {
      signature_verified: true,
      transaction_bound: true,
      request_bound: true,
      merchant_bound: true,
      expiry_valid: true,
      single_use: true,
    },
    order: {
      order_id: "order_test_123",
      amount: 129900,
      currency: "INR",
      receipt: "mg_receipt_123",
      status: "created",
    },
    replay: {
      status: "REJECTED_BEFORE_NETWORK",
      reason: "NONCE_ALREADY_USED",
      razorpay_additional_calls: 0,
    },
  });
  assert.match(html, /OFFLINE DEMO REPLAY/);
  assert.match(html, /NO LIVE RAZORPAY REQUEST/);
  assert.match(html, /SIMULATED EXECUTION RECEIPT/);
  assert.match(html, /LOCAL RECEIPT CREATED/);
  assert.match(html, /preserved engineering evidence/);
  assert.doesNotMatch(html, />RAZORPAY TEST MODE</);
  assert.match(html, /VERIFIED/);
  assert.match(html, /REJECTED BEFORE NETWORK/);
  assert.match(html, /Razorpay additional calls: 0/);
});


test("live Test Mode label appears only for the backend live environment", () => {
  const html = renderExecutionPanel({
    status: "ORDER_CREATED",
    environment: "RAZORPAY_TEST_MODE",
    razorpay_calls: 1,
    external_network_calls: 1,
    capability: {},
    order: {},
  });
  assert.match(html, />RAZORPAY TEST MODE</);
  assert.match(html, /PAYMENT ORDER/);
  assert.match(html, /ORDER CREATED/);
  assert.doesNotMatch(html, /OFFLINE DEMO REPLAY/);
  assert.doesNotMatch(html, /preserved engineering evidence/);
});


test("unavailable live mode is stated in the page, not only in a tooltip", () => {
  const note = liveModeStatusNote({ available: false, problems: [], missing_configuration: ["OPENAI_API_KEY"] });
  assert.match(note, /LIVE TEST UNAVAILABLE/);
  assert.match(note, /Server-side credentials are not configured on this deployment\./);
  assert.match(note, /Offline demo remains fully available\./);

  // Missing credentials is the judge-facing reason, even when a library is also absent.
  const deployed = liveModeStatusNote({
    available: false,
    missing_configuration: ["OPENAI_API_KEY"],
    problems: ["OpenAI Python package is not installed"],
  });
  assert.match(deployed, /Server-side credentials are not configured on this deployment\./);
  assert.doesNotMatch(deployed, /Python package/);

  const withProblem = liveModeStatusNote({
    available: false,
    missing_configuration: [],
    problems: ["OpenAI Python package is not installed"],
  });
  assert.match(withProblem, /LIVE TEST UNAVAILABLE/);
  assert.match(withProblem, /OpenAI Python package is not installed\./);

  assert.equal(liveModeStatusNote({ available: true, problems: [] }), null);
});


test("recoverable REVIEW shows a server-selected acquisition action and zero calls", () => {
  const html = renderReviewRecovery(
    {
      status: "AVAILABLE",
      gap: { reason: "Recurring terms could not be verified." },
      trusted_source: { label: "Merchant SKU Terms" },
      action: { enabled: true },
      rounds_used: 0,
      max_rounds: 2,
    },
    { razorpay_calls: 0 },
  );
  assert.match(html, /EVIDENCE GAP/);
  assert.match(html, /Recurring terms could not be verified/);
  assert.match(html, /TRUSTED SOURCE AVAILABLE/);
  assert.match(html, /ACQUIRE TRUSTED EVIDENCE/);
  assert.match(html, /Razorpay calls <strong>0<\/strong>/);
});


test("resolved review shows the fresh controller transition", () => {
  const html = renderReviewRecovery(
    {
      status: "RESOLVED",
      transition: "REVIEW -> ALLOW",
      resolved_after: "1 trusted evidence acquisition",
      new_evidence_items: 1,
      payment_provider_calls_before_final_allow: 0,
    },
    { razorpay_calls: 1 },
  );
  assert.match(html, /REVIEW -&gt; ALLOW/);
  assert.match(html, /1 trusted evidence acquisition/);
  assert.match(html, /Razorpay calls before final ALLOW <strong>0<\/strong>/);
});


test("transactability shows the current REVIEW and incomplete evidence", () => {
  const html = renderTransactability({
    readiness: [
      { label: "PRICE", status: "VERIFIED" },
      { label: "RECURRENCE TERMS", status: "MISSING" },
    ],
    status: "REVIEW",
    evidence_readiness: "INCOMPLETE",
    next_action: "Additional trusted evidence may make this transaction evaluable.",
    authority_notice: "Diagnostic only. This surface cannot authorize payments.",
  });
  assert.match(html, /CURRENT STATUS/);
  assert.match(html, />REVIEW</);
  assert.match(html, /EVIDENCE READINESS/);
  assert.match(html, /INCOMPLETE/);
  assert.doesNotMatch(html, /REVIEW LIKELY/);
  assert.match(html, /may make this transaction evaluable/);
  assert.match(html, /RECURRENCE TERMS/);
  assert.match(html, /MISSING/);
  assert.match(html, /cannot authorize payments/);
});


const CONSENT_CAPABILITY = {
  signature_verified: true,
  transaction_bound: true,
  request_bound: true,
  merchant_bound: true,
  mandate_identity_bound: true,
  mandate_version_bound: true,
  expiry_valid: true,
};


test("issued capability offers revocation while proving zero Razorpay calls", () => {
  const html = renderExecutionPanel({
    status: "AUTHORIZED",
    reason: null,
    razorpay_calls: 0,
    external_network_calls: 0,
    capability: CONSENT_CAPABILITY,
    consent: {
      status: "ACTIVE",
      mandate_version: 7,
      authority: "DEMO USER REVOCATION",
      can_revoke: true,
      can_execute: true,
      teaching: "MandateGuard revalidates its trusted mandate state immediately before execution.",
    },
  });
  assert.match(html, /CONSENT STATE/);
  assert.match(html, /consent-state--active/);
  assert.match(html, /Mandate v7/);
  assert.match(html, /CAPABILITY ISSUED/);
  assert.match(html, /RAZORPAY CALLS 0/);
  assert.match(html, /id="revoke-mandate-button"/);
  assert.match(html, /id="attempt-execution-button"/);
  assert.doesNotMatch(html, /REJECTED BEFORE NETWORK/);
  assert.match(html, /revalidates its trusted mandate state/);
});


test("revoked mandate refuses a still-valid capability before any network call", () => {
  const html = renderExecutionPanel({
    status: "REJECTED_BEFORE_NETWORK",
    reason: "MANDATE_REVOKED",
    razorpay_calls: 0,
    external_network_calls: 0,
    capability: CONSENT_CAPABILITY,
    consent: {
      status: "REVOKED",
      mandate_version: 7,
      authority: "DEMO USER REVOCATION",
      can_revoke: false,
      can_execute: false,
      teaching: "The capability is still signed and unexpired. Current consent no longer permits execution.",
    },
  });
  assert.match(html, /REJECTED BEFORE NETWORK/);
  assert.match(html, /Mandate revoked/);
  assert.match(html, /RAZORPAY CALLS 0/);
  assert.match(html, /consent-state--revoked/);
  // The teaching moment: the capability itself is still cryptographically sound.
  assert.match(html, /CRYPTOGRAPHICALLY VALID/);
  assert.match(html, /Signature<\/span><strong data-valid="true">VERIFIED/);
  assert.match(html, /still signed and unexpired\. Current consent no longer permits execution/);
  // A consumed capability must not offer either control again.
  assert.doesNotMatch(html, /id="revoke-mandate-button"/);
  assert.doesNotMatch(html, /id="attempt-execution-button"/);
});


test("consent panel never claims bank, UPI, or Razorpay mandate revocation", () => {
  const html = renderExecutionPanel({
    status: "REJECTED_BEFORE_NETWORK",
    reason: "MANDATE_SUPERSEDED",
    razorpay_calls: 0,
    external_network_calls: 0,
    capability: { ...CONSENT_CAPABILITY, mandate_version_bound: false },
    consent: {
      status: "SUPERSEDED",
      mandate_version: 7,
      authority: "DEMO USER REVOCATION",
      can_revoke: false,
      can_execute: false,
      teaching: "The capability is still signed and unexpired. Current consent no longer permits execution.",
    },
  });
  assert.match(html, /Mandate superseded/);
  assert.match(html, /Authority: DEMO USER REVOCATION/);
  assert.match(html, /Mandate version<\/span><strong data-valid="false">FAILED/);
  for (const overclaim of [/bank consent/i, /UPI mandate/i, /Razorpay mandate/i, /identity verified/i]) {
    assert.doesNotMatch(html, overclaim);
  }
});


// ---------------------------------------------------------------------------
// Judge-facing transaction story
// ---------------------------------------------------------------------------

const stylesheet = readFileSync(
  fileURLToPath(new URL("../../src/mandateguard/product/static/app.css", import.meta.url)),
  "utf-8",
);

const SPINE_IDS = [
  "USER_MANDATE",
  "AI_BUYER",
  "PRODUCT",
  "EVIDENCE_RETRIEVAL",
  "DETERMINISTIC_VERIFICATION",
  "SEMANTIC_VERIFICATION",
  "AUTHORIZATION",
  "EXECUTION",
];

const timelineOf = (...statuses) =>
  SPINE_IDS.map((id, index) => ({ id, status: statuses[index], detail: null }));

const BLOCKED_TIMELINE = timelineOf(
  "PASS", "PASS", "PASS", "PASS", "PASS", "BLOCK", "BLOCK", "BLOCK",
);

const BLOCK_RESULT = {
  decision: "BLOCK",
  decision_reason: "trusted evidence includes the prohibited characteristic",
  buyer: { mandate: "Buy the Market Edge Decision Course. No gambling-related products." },
  evidence: {
    classification: "TRUSTED MERCHANT EVIDENCE",
    cards: [
      {
        evidence_id: "academy-terms-v1",
        scope: "MERCHANT",
        text: "Academy products are sold as one-time purchases.",
      },
      {
        evidence_id: "market-edge-evidence-v1",
        scope: "PRODUCT",
        text: "The syllabus teaches casino gambling techniques and wager selection.",
      },
    ],
  },
  authorization: {
    final_controller: "BLOCK",
    deterministic: { action: "ALLOW", tier_a: [], tier_b: [] },
    semantic: {
      verdict: "VIOLATION",
      checks: [
        {
          constraint_id: "purpose.1",
          family: "purpose",
          constraint: "Declared purchase purpose: professional development.",
          status: "PASS",
          reason: "trusted evidence states the declared purpose",
        },
        {
          constraint_id: "exclusion.1",
          family: "exclusion",
          constraint: "Excluded product characteristic: gambling-related products.",
          status: "VIOLATION",
          reason: "trusted evidence includes the prohibited characteristic",
        },
      ],
    },
  },
  execution: { status: "NOT_CALLED", razorpay_calls: 0, external_network_calls: 0 },
};


test("the decision spine stops at the failing stage and never reaches execution", () => {
  assert.equal(spineProgress(BLOCKED_TIMELINE), 5 / 8);

  const html = renderSpine(BLOCKED_TIMELINE);
  // Exactly one stop bar, and it sits on the stage that actually halted.
  assert.equal(html.match(/data-halt="true"/g).length, 1);
  assert.match(html, /data-step="SEMANTIC_VERIFICATION" data-state="block" data-halt="true"/);
  assert.doesNotMatch(html, /data-step="EXECUTION"[^>]*data-halt="true"/);
});


test("the spine reaches execution only when every stage passed", () => {
  assert.equal(spineProgress(timelineOf(...Array(8).fill("PASS"))), 1);
  assert.equal(spineProgress(timelineOf(...Array(7).fill("PASS"), "AUTHORIZED")), 1);

  // A run refused at the execution gate must not draw a line into the provider.
  const refused = timelineOf(...Array(7).fill("PASS"), "REJECTED");
  assert.equal(spineProgress(refused), 7 / 8);
  assert.match(
    renderSpine(refused),
    /data-step="EXECUTION" data-state="stopped" data-halt="true"/,
  );

  assert.equal(spineProgress(timelineOf(...Array(8).fill("WAITING"))), 0);
});


test("failed constraint resolves to the semantic violation, not a passing check", () => {
  const failed = failedConstraint(BLOCK_RESULT.authorization);
  assert.equal(failed.layer, "SEMANTIC");
  assert.equal(failed.family, "exclusion");
  assert.equal(failed.constraint, "Excluded product characteristic: gambling-related products.");

  // With no semantic violation the deterministic layer supplies the reason.
  const deterministic = failedConstraint({
    deterministic: {
      tier_a: [
        { family: "A1", label: "Authoritative price", status: "PASS", reason: null },
        { family: "A2", label: "SKU ownership", status: "FAIL", reason: "SKU is not owned." },
      ],
      tier_b: [],
    },
    semantic: { checks: [] },
  });
  assert.equal(deterministic.layer, "DETERMINISTIC");
  assert.equal(deterministic.family, "A2");
});


test("BLOCK story answers why in one screen: constraint, tier, controller", () => {
  const html = renderBlockStory(BLOCK_RESULT);
  assert.match(html, /Why was this transaction blocked\?/);

  // The mandate and the trusted evidence are presented against each other.
  assert.match(html, /USER MANDATE/);
  assert.match(html, /TRUSTED MERCHANT EVIDENCE/);
  assert.match(html, /No gambling-related products/);
  assert.match(html, /casino gambling techniques/);
  assert.match(html, /trusted evidence includes the prohibited characteristic/);

  // The three facts a judge needs.
  assert.match(html, /FAILED CONSTRAINT[\s\S]*?exclusion/);
  assert.match(html, /EVIDENCE TIER[\s\S]*?TRUSTED MERCHANT EVIDENCE/);
  assert.match(html, /CONTROLLER[\s\S]*?BLOCK/);
});


test("BLOCK story leads with product-scope evidence over merchant-wide terms", () => {
  const html = renderBlockStory(BLOCK_RESULT);
  assert.ok(
    html.indexOf("market-edge-evidence-v1") < html.indexOf("academy-terms-v1"),
    "the SKU-specific evidence that decided the constraint must be read first",
  );
});


test("a blocked run reports zero rupees and zero provider calls", () => {
  const html = renderDecisionBanner(BLOCK_RESULT);
  assert.match(html, /VALUE MOVED[\s\S]*?₹0</);
  assert.match(html, /RAZORPAY CALLS[\s\S]*?>0</);
  assert.match(html, /EXTERNAL CALLS[\s\S]*?>0</);
  assert.doesNotMatch(html, /₹0\.00/);
});


const REVIEW_RESULT = {
  decision: "REVIEW",
  decision_reason: "trusted evidence is insufficient for the exclusion",
  transactability: {
    status: "REVIEW",
    evidence_readiness: "INCOMPLETE",
    next_action: "Additional trusted evidence may make this transaction evaluable.",
    readiness: [
      { label: "PRICE", status: "VERIFIED" },
      { label: "SKU OWNERSHIP", status: "VERIFIED" },
      { label: "MERCHANT BINDING", status: "VERIFIED" },
      { label: "PURPOSE EVIDENCE", status: "AVAILABLE" },
      { label: "RECURRENCE TERMS", status: "MISSING" },
    ],
  },
  authorization: {
    final_controller: "REVIEW",
    semantic: {
      verdict: "ABSTAIN",
      checks: [
        {
          constraint_id: "exclusion.1",
          family: "exclusion",
          constraint: "Excluded product characteristic: subscriptions.",
          status: "ABSTAIN",
          reason: "trusted evidence is insufficient for the exclusion",
        },
      ],
    },
  },
  execution: { status: "NOT_CALLED", razorpay_calls: 0, external_network_calls: 0 },
};


test("REVIEW story separates what is known from what is missing", () => {
  const html = renderReviewStory(REVIEW_RESULT);
  assert.match(html, /WHAT WE KNOW/);
  assert.match(html, /WHAT WE DO NOT KNOW/);
  assert.match(html, /WHY THAT MATTERS/);

  const known = html.slice(html.indexOf("WHAT WE KNOW"), html.indexOf("WHAT WE DO NOT KNOW"));
  assert.match(known, /PRICE/);
  assert.match(known, /SKU OWNERSHIP/);
  assert.match(known, /MERCHANT BINDING/);
  assert.doesNotMatch(known, /RECURRENCE TERMS/);

  const missing = html.slice(html.indexOf("WHAT WE DO NOT KNOW"), html.indexOf("WHY THAT MATTERS"));
  assert.match(missing, /RECURRENCE TERMS/);
  assert.match(missing, /MISSING/);
});


test("REVIEW story states why the gap prevents a decision, and that nothing moved", () => {
  const html = renderReviewStory(REVIEW_RESULT);
  assert.match(html, /Excluded product characteristic: subscriptions\./);
  assert.match(
    html,
    /MandateGuard cannot determine whether this transaction violates that constraint/,
  );
  assert.match(html, /OUTCOME[\s\S]*?REVIEW/);
  assert.match(html, /MONEY MOVED[\s\S]*?₹0</);
  assert.match(html, /may make this transaction evaluable/);
});


test("recovered REVIEW reports the transition and zero calls before the final ALLOW", () => {
  const html = renderStory({
    decision: "ALLOW",
    buyer: { product: "Aurora Focus Lamp", price_minor: 149900, currency: "INR" },
    recovery: {
      status: "RESOLVED",
      transition: "REVIEW -> ALLOW",
      resolved_after: "1 trusted evidence acquisition",
      payment_provider_calls_before_final_allow: 0,
    },
    execution: {
      status: "ORDER_CREATED",
      order: { amount: 149900, currency: "INR" },
      consent: { status: "ACTIVE" },
    },
  });
  assert.match(html, /Reached after 1 trusted evidence acquisition/);
  assert.match(html, /a fresh run of the full controller/);
  assert.match(html, /Payment-provider calls before the final ALLOW: <strong>0<\/strong>/);
  assert.match(html, /₹1,499/);
});


// ---------------------------------------------------------------------------
// Revocation
// ---------------------------------------------------------------------------

const ISSUED_CAPABILITY = {
  signature_verified: true,
  expiry_valid: true,
  transaction_bound: true,
  request_bound: true,
};


test("consent strip reports an active mandate against a verified capability", () => {
  const html = renderConsentStrip({
    capability: ISSUED_CAPABILITY,
    consent: { status: "ACTIVE" },
  });
  assert.match(html, /data-consent-status="ACTIVE"/);
  for (const row of ["SIGNED", "UNEXPIRED", "HASH BOUND"]) {
    assert.match(html, new RegExp(`${row}</span>\\s*<span class="consentstrip__value">VERIFIED`));
  }
  assert.match(html, /CONSENT<\/span>\s*<span class="consentstrip__value">ACTIVE/);
  assert.doesNotMatch(html, /REVOKED/);
});


test("revocation changes the consent row alone and stops execution before the network", () => {
  const html = renderRevocationStory({
    status: "REJECTED_BEFORE_NETWORK",
    reason: "MANDATE_REVOKED",
    razorpay_calls: 0,
    external_network_calls: 0,
    capability: ISSUED_CAPABILITY,
    consent: {
      status: "REVOKED",
      authority: "DEMO USER REVOCATION",
      teaching:
        "The capability is still signed and unexpired. Current consent no longer permits execution.",
    },
  });

  // The cryptographic rows are untouched; only consent flipped.
  assert.match(html, /data-consent-status="REVOKED"/);
  assert.equal(html.match(/consentstrip__value">VERIFIED/g).length, 3);
  assert.match(html, /CONSENT<\/span>\s*<span class="consentstrip__value">REVOKED/);

  assert.match(html, /REJECTED BEFORE NETWORK/);
  assert.match(html, /still signed and unexpired\. Current consent no longer permits execution/);
  assert.match(html, /Authority: DEMO USER REVOCATION/);
});


test("a refusal at the execution gate is not reported as a controller decision", () => {
  const html = renderDecisionBanner({
    decision: "ALLOW",
    decision_reason: "All applicable deterministic and semantic checks passed.",
    execution: {
      status: "REJECTED_BEFORE_NETWORK",
      reason: "MANDATE_REVOKED",
      razorpay_calls: 0,
      external_network_calls: 0,
      consent: { status: "REVOKED" },
    },
  });
  assert.match(html, /EXECUTION GATE/);
  assert.match(html, /REJECTED BEFORE NETWORK/);
  assert.match(html, /Mandate revoked/);
  // The controller really did allow; the banner must not overwrite that.
  assert.match(html, /FINAL CONTROLLER <strong>ALLOW<\/strong>/);
  assert.match(html, /VALUE MOVED[\s\S]*?₹0</);
});


// ---------------------------------------------------------------------------
// Attack lab
// ---------------------------------------------------------------------------

test("every attack surface is analysed across all five columns", () => {
  assert.ok(ATTACK_SURFACES.length >= 12);
  const html = renderAttackLab();
  for (const surface of ATTACK_SURFACES) {
    for (const field of [
      "surface",
      "detail",
      "signal",
      "control",
      "decision",
      "paymentReached",
      "evidence",
    ]) {
      assert.ok(surface[field], `${surface.id} is missing ${field}`);
    }
    assert.ok(html.includes(escapeHtml(surface.surface)));
    assert.ok(html.includes(escapeHtml(surface.signal)));
    assert.ok(html.includes(escapeHtml(surface.control)));
  }
  for (const expected of [
    "Price mutation after ALLOW",
    "Capability replay",
    "Consent revocation",
    "Evidence omission",
    "Cross-run consent reuse",
    "Listing category laundering",
    "Purchase of an unvouched listing",
  ]) {
    assert.ok(
      ATTACK_SURFACES.some((item) => item.surface === expected),
      `${expected} must be covered`,
    );
  }
});


test("no analysed attack surface reaches a payment", () => {
  for (const surface of ATTACK_SURFACES) {
    assert.equal(
      surface.paymentReached,
      "NO",
      `${surface.id} claims a payment was reached`,
    );
  }
  const html = renderAttackLab();
  assert.match(html, /PAYMENT REACHED\?/);
  assert.match(html, /data-reached="NO"/);
  assert.doesNotMatch(html, /data-reached="YES"/);
});


test("attack lab states defensive decisions only", () => {
  const allowed = new Set([
    "REJECTED BEFORE NETWORK",
    "BLOCK OR REVIEW",
    "REVIEW",
    "REVIEW REQUIRED",
    "REVIEW PRIORITY RAISED",
    "EVIDENCE REFUSED",
  ]);
  for (const surface of ATTACK_SURFACES) {
    assert.ok(allowed.has(surface.decision), `unexpected decision: ${surface.decision}`);
  }
  // The analysis describes surfaces, signals, and controls, never a procedure.
  const prose = ATTACK_SURFACES.map(
    (item) => `${item.detail} ${item.signal} ${item.control}`,
  ).join(" ");
  for (const forbidden of [/step 1/i, /how to bypass/i, /in order to evade/i, /payload/i]) {
    assert.doesNotMatch(prose, forbidden);
  }

  const html = renderAttackLab();
  assert.match(html, /REJECTED BEFORE NETWORK/);
  assert.match(html, /data-treatment="REJECTED BEFORE NETWORK"/);
});


// ---------------------------------------------------------------------------
// Scale and claim boundaries
// ---------------------------------------------------------------------------

test("bounded scale claims are read from the server evidence policy", () => {
  const html = renderBoundedScale({
    resolve: { top_k: 5, max_acquisition_rounds: 2, max_new_evidence_items: 4 },
  });
  assert.match(html, /Evidence retrieval<\/dt><dd>top-k 5/);
  assert.match(html, /Recovery<\/dt><dd>max 2 rounds/);
  assert.match(html, /New trusted evidence<\/dt><dd>max 4 items/);
  assert.match(html, /Execution capability<\/dt><dd>single-use/);
  assert.match(html, /External payment call<\/dt><dd>only after final ALLOW/);

  // A different server policy must change the rendered claim.
  const other = renderBoundedScale({
    resolve: { top_k: 3, max_acquisition_rounds: 1, max_new_evidence_items: 2 },
  });
  assert.match(other, /top-k 3/);
  assert.match(other, /max 1 rounds/);
  assert.doesNotMatch(other, /top-k 5/);
});


test("measured evidence reports the frozen result with its caveats intact", () => {
  const html = renderMeasuredEvidence();
  assert.equal(EVALUATION_EVIDENCE.cases, 20);
  assert.equal(
    EVALUATION_EVIDENCE.allow + EVALUATION_EVIDENCE.block + EVALUATION_EVIDENCE.review,
    EVALUATION_EVIDENCE.cases,
  );
  assert.equal(EVALUATION_EVIDENCE.safetyViolations, 0);

  assert.match(html, /20<\/strong>\s*independent synthetic recovery/);
  assert.match(html, /REVIEW to ALLOW<\/dt>[\s\S]*?>7</);
  assert.match(html, /REVIEW to BLOCK<\/dt>[\s\S]*?>3</);
  assert.match(html, /REVIEW to REVIEW<\/dt>[\s\S]*?>10</);
  assert.match(html, /Safety violations<\/dt>[\s\S]*?>0</);
  assert.match(html, /₹29,923 of frozen synthetic transaction value/);
  assert.match(html, /docs\/RESOLVE_EVALUATION_RESULTS\.md/);
});


test("the interface never converts test counts or synthetic cases into a scale claim", () => {
  const html = renderMeasuredEvidence();
  assert.match(html, /Not merchant traffic, not revenue, not conversion lift/);
  assert.match(html, /not evidence of generalization/);
  assert.match(html, /not a production throughput claim/i);
  assert.match(html, /Half of the initially non-executable cases stayed at REVIEW/);
  // Test counts belong to engineering quality and appear nowhere in the
  // authorization-evidence block, so a reader cannot read one as the other.
  assert.doesNotMatch(html, /Python tests/);
  assert.doesNotMatch(html, /UI tests/);
  assert.doesNotMatch(html, /Secondary proof/);
  for (const overclaim of [/TPS/, /requests per second/i, /production scale/i, /at scale/i]) {
    assert.doesNotMatch(html, overclaim);
  }

  const engineering = renderEngineeringQuality({ python: 800, ui: 60 });
  assert.match(engineering, /Python tests/);
  assert.match(engineering, /UI tests/);
  assert.match(engineering, /not a\s+measurement of scale/i);

  const research = renderResearch({
    authorization_use: "Not used in the authorization gate.",
    finding: "Evidence composition predicted stability better than quantity.",
    scope: "62 correlated evidence subsets across six synthetic queries.",
    source: "artifacts/engineering/int3/RUN.md",
  });
  assert.match(research, /EXPERIMENTAL/);
  assert.match(research, /Not used in the authorization gate\./);
});


test("call accounting is never opted into the figure animation", () => {
  // Interpolating a call count would paint integers the run never produced.
  const banner = renderDecisionBanner(BLOCK_RESULT);
  const ledger = banner.slice(banner.indexOf('class="ledger"'));
  assert.doesNotMatch(ledger, /data-figure/);
  assert.match(ledger, /RAZORPAY CALLS[\s\S]*?<dd class="ledger__value">0<\/dd>/);

  // Aggregate evaluation figures may animate, and still render their true value.
  const measured = renderMeasuredEvidence();
  assert.match(measured, /data-figure="animate"/);
  assert.match(measured, /<dd class="measured__value" data-figure="animate">7<\/dd>/);
});


// ---------------------------------------------------------------------------
// Evidence provenance
// ---------------------------------------------------------------------------

test("provenance ties each constraint to the evidence set that decided it", () => {
  const html = renderProvenance({
    evidence: {
      evidence_set_sha256: "5f7d75b0ad451d8f1ff9ec4270d8e3dc",
      buyer_text: { text: "Highest lexical catalog match." },
      cards: [
        {
          evidence_id: "aurora-listing-v1",
          source_kind: "product_listing",
          merchant_id: "merchant-lumen",
          sku: "aurora-focus-lamp",
          scope: "PRODUCT",
          text: "The registered listing does not record the billing model.",
          retrieval_score: 0.41,
          acquisition: "INITIAL_RETRIEVAL",
        },
        {
          evidence_id: "aurora-sku-terms-v2",
          source_kind: "product_terms",
          merchant_id: "merchant-lumen",
          sku: "aurora-focus-lamp",
          scope: "PRODUCT",
          text: "Billing model: one-time purchase with no subscription.",
          retrieval_score: null,
          acquisition: "BOUNDED_TRUSTED_ACQUISITION",
        },
      ],
    },
    authorization: {
      semantic: {
        checks: [
          {
            constraint_id: "exclusion.1",
            family: "exclusion",
            constraint: "Excluded product characteristic: subscriptions.",
            status: "PASS",
            reason: "trusted evidence explicitly excludes the prohibited characteristic",
          },
        ],
      },
    },
  });

  assert.match(html, /EVIDENCE SET COMMITMENT/);
  assert.match(html, /5f7d75b0ad451d8f1ff9ec4270d8e3dc/);
  assert.match(html, /evaluated against exactly this canonical evidence set/);

  // Source, trust tier, merchant, scope, effective state and claim per item.
  assert.match(html, /TRUSTED MERCHANT EVIDENCE/);
  assert.match(html, /product_terms/);
  assert.match(html, /merchant-lumen/);
  assert.match(html, /SKU aurora-focus-lamp/);
  assert.match(html, /ACQUIRED DURING RECOVERY/);
  assert.match(html, /RESOLVED AT RETRIEVAL/);
  assert.match(html, /data-acquisition="BOUNDED_TRUSTED_ACQUISITION"/);

  // The constraint it decided, and the buyer text that could not.
  assert.match(html, /Excluded product characteristic: subscriptions\./);
  assert.match(html, /data-status="PASS"/);
  assert.match(html, /BUYER-PROVIDED TEXT<\/strong> is recorded for the audit trail and is never/);
});


test("the evidence view explains itself before any run has happened", () => {
  const html = renderProvenance(null);
  assert.match(html, /No run has been observed yet/);
  assert.match(html, /source, trust tier, scope, and hash commitment/);
});


// ---------------------------------------------------------------------------
// Presentation guarantees held in the stylesheet
// ---------------------------------------------------------------------------

test("reduced motion disables animation and never hides revealed content", () => {
  const block = stylesheet.slice(stylesheet.indexOf("@media (prefers-reduced-motion: reduce)"));
  assert.match(block, /animation-duration: 1ms !important/);
  assert.match(block, /animation-iteration-count: 1 !important/);
  assert.match(block, /transition-duration: 1ms !important/);
  assert.match(block, /scroll-behavior: auto/);
  assert.match(block, /\[data-reveal\] \{ opacity: 1; transform: none; \}/);

  // The hidden entrance state only exists while script has armed it, so copy is
  // readable when the transition cannot run at all.
  assert.match(stylesheet, /\[data-reveal-armed="true"\] \[data-reveal\] \{\s*opacity: 0;/);
});


test("every multi-column story layout collapses to one column on small viewports", () => {
  const collapses = (selector) =>
    new RegExp(`\\${selector}[^{}]*\\{[^}]*grid-template-columns: 1fr`).test(stylesheet);
  for (const selector of [
    ".spine",
    ".decision-banner",
    ".verification__grid",
    ".conflict",
    ".knowledge",
    ".attack",
    ".factrow",
    ".ledger",
    ".measured__grid",
    ".hero__facts",
    ".hero",
  ]) {
    assert.ok(collapses(selector), `${selector} must collapse to one column`);
  }

  // The mobile tab strip must not bleed outside the padded row: a negative
  // inline margin there gives the whole document horizontal overflow.
  assert.doesNotMatch(stylesheet, /\.mainnav \{[^}]*margin-inline: calc\(var\(--gutter\) \* -1\)/);
  assert.match(stylesheet, /body \{[\s\S]*?overflow-x: hidden;/);
  // Grid tracks that hold long text must be allowed to shrink below content width.
  assert.match(stylesheet, /grid-template-columns: auto minmax\(0, 1fr\)/);
});


// ---------------------------------------------------------------------------
// Judge Playground
// ---------------------------------------------------------------------------

const sandboxCandidate = {
  catalog_product_id: "sandbox.abc123",
  merchant_id: "sandbox-brightleaf-lighting",
  merchant: "Brightleaf Lighting (Synthetic)",
  sku: "lighting-desk-lamps-007",
  name: "Dimmable study lamp",
  category: "Desk lamps",
  category_id: "lighting-desk-lamps",
  price_minor: 159900,
  currency: "INR",
  billing_model: "ONE_TIME",
  recurring: false,
  recurrence_declaration: "SETTLED_ONCE",
  evidence_version: "v1",
  effective_from: "2026-09-01T00:00:00Z",
  why_found: {
    semantic_similarity: 1,
    semantic_method: "DETERMINISTIC_CATEGORY_SYNONYM_V1",
    lexical_score: 42.15,
    matched_terms: ["study", "lamp"],
    exact_phrase_match: "study lamp",
    within_budget: true,
    brand_match: null,
    category_match: "Desk lamps",
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


test("Playground is the primary tab and explains the sandbox before controls", () => {
  const html = readFileSync(
    fileURLToPath(new URL("../../src/mandateguard/product/static/index.html", import.meta.url)),
    "utf8",
  );
  assert.ok(html.indexOf(">PLAYGROUND</button>") < html.indexOf(">MARKETPLACE</button>"));
  assert.match(html, /SIMULATED MERCHANT SANDBOX/);
  assert.match(html, /Test MandateGuard with arbitrary buying instructions\./);
  assert.match(html, /Nothing here represents[\s\S]*live marketplace or real[\s\S]*money\./);
  assert.match(html, /type="button"[^>]*id="pg-search-button"/);
});


test("Playground rail walks the product story and never infers a verdict", () => {
  assert.deepEqual(
    PLAYGROUND_RAIL.map((stage) => stage.id),
    ["MANDATE", "SELECTION", "MANDATEGUARD", "EXECUTION"],
  );
  const deciding = railStates({
    intent: "lamp",
    candidates: [sandboxCandidate],
    selected: sandboxCandidate,
    snapshot: { state: "RUNNING" },
  });
  assert.equal(deciding.MANDATE, "done");
  assert.equal(deciding.SELECTION, "done");
  assert.equal(deciding.MANDATEGUARD, "active");
  assert.equal(deciding.EXECUTION, "waiting");
});


test("each sandbox result names only the actual retrieval signals and readiness fields", () => {
  const html = renderPlaygroundCandidate(sandboxCandidate);
  assert.match(html, /Dimmable study lamp/);
  assert.match(html, /₹1,599/);
  assert.match(html, /Brightleaf Lighting \(Synthetic\)/);
  assert.match(html, /Desk lamps/);
  for (const label of ["Key terms", "Budget", "Category match"]) {
    assert.match(html, new RegExp(label));
  }
  assert.doesNotMatch(html, /Semantic similarity/);
  assert.match(html, /<dt>Category match<\/dt><dd>Desk lamps<\/dd>/);
  assert.match(html, /phrase “study lamp”/);
  // The evidence matrix is still published in full, one disclosure away.
  assert.match(html, /Merchant identity/);
  assert.match(html, /SKU evidence/);
  assert.match(html, /Billing model/);
  assert.match(html, /Evidence version/);
  assert.match(html, /SELECT PRODUCT/);
});


test("an empty sandbox search shows closest candidates and the excluding constraint", () => {
  const html = renderNoMatch({
    no_match_message: "No suitable sandbox product matched all of your constraints.",
    candidates: [],
    constraints_applied: ["Price at most INR 100.00"],
    near_misses: [
      {
        name: "Dimmable study lamp",
        price_minor: 159900,
        currency: "INR",
        excluded_by: "MAX_TOTAL",
        explanation: "Priced above your INR 100.00 limit.",
      },
    ],
  });
  assert.match(html, /No suitable sandbox product matched all of your constraints\./);
  assert.match(html, /CLOSEST CANDIDATES, AND WHAT EXCLUDED THEM/);
  assert.match(html, /MAX_TOTAL/);
});


test("Playground verdict and execution claims stay literal", () => {
  for (const [decision, headline] of [
    ["ALLOW", "This purchase matches your mandate. Payment execution may proceed."],
    ["BLOCK", "MandateGuard stopped this before payment."],
    ["REVIEW", "MandateGuard refused to guess."],
  ]) {
    const html = renderPlaygroundVerdict({
      result: { decision },
      explanation: { headline },
    });
    assert.match(html, new RegExp(decision));
    assert.match(html, new RegExp(headline.replace(/[.]/g, "\\.")));
  }
  const execution = renderPlaygroundExecution({
    result: {
      buyer: { price_minor: 159900, currency: "INR", merchant: "sandbox-light", sku: "lamp-1" },
      execution: {
        status: "ORDER_CREATED",
        razorpay_calls: 1,
        external_network_calls: 0,
        consent: { status: "ACTIVE" },
        order: { order_id: "order_offline_123", amount: 159900, currency: "INR" },
      },
    },
  });
  assert.match(execution, /SIMULATED OFFLINE ORDER/);
  assert.match(execution, /No external network call was made/);
  assert.match(execution, /nothing was captured or settled/);
  assert.doesNotMatch(execution, /payment captured/i);
});


test("merchant onboarding shows exact new declarations and the unchanged trust boundary", () => {
  const form = renderOnboardingForm({
    form: {
      notice: "SIMULATION. The source remains untrusted.",
      copied_from_listing: {
        title: "Historical lamp",
        category_label: "Lighting",
        note: "Only words and shelf are carried across.",
      },
      generated_declarations: [
        { field: "sku_ownership", label: "SKU ownership", value: "BOUND", why: "New exact identity." },
        { field: "recurrence", label: "Recurrence declaration", value: "DERIVED", why: "From billing." },
        { field: "exclusions", label: "Exclusion declaration", value: "DERIVED", why: "From content." },
      ],
      required_declarations: [],
    },
  });
  assert.match(form, /SKU ownership/);
  assert.match(form, /Recurrence declaration/);
  assert.match(form, /Exclusion declaration/);
  assert.match(form, /PUBLISH EVIDENCE AND RE-RUN AUTHORIZATION/);

  const result = renderOnboardedResult({
    notice: "A new synthetic record was created.",
    merchant: { display_name: "Lamp Works (Synthetic)", merchant_id: "sandbox-onboarded-lamp", sku: "lamp-1" },
    product: { ...sandboxCandidate, purpose_claims: ["individual study"], exclusion_claims: ["gambling"] },
    readiness: sandboxCandidate.readiness,
    source_listing: { title: "Historical lamp", still_untrusted: true, note: "The source row is unchanged." },
  });
  assert.match(result, /NEW SYNTHETIC MERCHANT RECORD/);
  assert.match(result, /STILL UNTRUSTED/);
  assert.match(result, /Authoritative price/);
  assert.match(result, /SKU ownership/);
  assert.match(result, /RUN AUTHORIZATION AGAINST THE NEW RECORD/);
});


test("try-these prompts are real keyboard buttons and remain human-readable", () => {
  const html = renderTryThese([
    { label: "Buy headphones under ₹5,000", intent: "Buy headphones under INR 5,000." },
    {
      label: "Show me what happens if I revoke permission",
      intent: "Buy a lamp under INR 2,000.",
      defer_execution: true,
    },
  ]);
  assert.equal((html.match(/<button/g) || []).length, 2);
  assert.equal((html.match(/type="button"/g) || []).length, 2);
  assert.match(html, /Buy headphones under ₹5,000/);
  assert.match(html, /data-defer-execution="true"/);
  assert.match(stylesheet, /:focus-visible/);
  assert.match(stylesheet, /@media \(prefers-reduced-motion: reduce\)/);
});
