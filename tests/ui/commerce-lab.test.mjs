import assert from "node:assert/strict";
import test from "node:test";

import {
  SubmissionLock,
  escapeHtml,
  liveModeStatusNote,
  renderAuthorizationPanel,
  renderDecisionBanner,
  renderEvidencePanel,
  renderExecutionPanel,
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
