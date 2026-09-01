import assert from "node:assert/strict";
import test from "node:test";

import {
  SubmissionLock,
  escapeHtml,
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
  assert.match(html, /ORDER CREATED/);
  assert.match(html, /VERIFIED/);
  assert.match(html, /REJECTED BEFORE NETWORK/);
  assert.match(html, /Razorpay additional calls: 0/);
});
