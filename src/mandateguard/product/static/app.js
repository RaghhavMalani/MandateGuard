const terminalStates = new Set(["COMPLETE", "ERROR"]);
const liveRazorpayEvidenceUrl =
  "https://github.com/RaghhavMalani/MandateGuard/blob/b104488ba92fd7b2802b4e053e48e3d398d5f65f/artifacts/engineering/agentic_commerce/int1-razorpay-exec-20260830T074115Z-507323be/RUN.md";

/** Judge-facing labels for the eight controller steps, keyed by backend step id. */
const SPINE_LABELS = {
  USER_MANDATE: "User mandate",
  AI_BUYER: "AI buyer proposal",
  PRODUCT: "Product",
  EVIDENCE_RETRIEVAL: "Evidence",
  DETERMINISTIC_VERIFICATION: "Deterministic verification",
  SEMANTIC_VERIFICATION: "Semantic verification",
  AUTHORIZATION: "Authorization",
  EXECUTION: "Execution",
};

const SPINE_ORDER = Object.keys(SPINE_LABELS);

/**
 * Defensive adversarial taxonomy.
 *
 * Every row names a surface and the control that already exists in this
 * repository. Nothing here is a procedure for defeating a control, and nothing
 * here participates in an authorization decision.
 */
export const ATTACK_SURFACES = [
  {
    id: "intent-laundering",
    surface: "Intent laundering",
    detail: "The buyer restates the mandate in its own prose so the proposal reads as compliant.",
    signal: "Proposal rationale asserts a property no trusted evidence supports",
    control: "Buyer prose is never resolvable as evidence",
    decision: "BLOCK OR REVIEW",
    paymentReached: "NO",
    evidence: "POLICY VIOLATION example",
  },
  {
    id: "evidence-omission",
    surface: "Evidence omission",
    detail: "The authoritative terms that would decide the constraint are simply absent.",
    signal: "Constraint has no resolvable merchant evidence in the top-k set",
    control: "Semantic verifier abstains rather than defaulting",
    decision: "REVIEW",
    paymentReached: "NO",
    evidence: "AMBIGUOUS EVIDENCE example",
  },
  {
    id: "price-mutation",
    surface: "Price mutation after ALLOW",
    detail: "The order amount differs from the amount the controller authorized.",
    signal: "Execution request hash does not match the signed capability",
    control: "Request binding checked at the execution gate",
    decision: "REJECTED BEFORE NETWORK",
    paymentReached: "NO",
    evidence: "tests/test_execution_authorization.py",
  },
  {
    id: "sku-substitution",
    surface: "SKU substitution",
    detail: "A different product is presented for execution than the one that was verified.",
    signal: "Transaction body hash diverges from the authorized transaction",
    control: "Transaction binding and SKU ownership",
    decision: "REJECTED BEFORE NETWORK",
    paymentReached: "NO",
    evidence: "tests/test_execution_context_binding.py",
  },
  {
    id: "recurring-disguise",
    surface: "Recurring billing disguised as one-time",
    detail: "A subscription is presented as a single charge under a no-subscriptions mandate.",
    signal: "Recurrence cue in listing text with no authoritative recurrence terms",
    control: "Catalog recurrence check and the semantic recurrence family",
    decision: "BLOCK OR REVIEW",
    paymentReached: "NO",
    evidence: "RECOVERABLE REVIEW example",
  },
  {
    id: "stale-evidence",
    surface: "Stale or superseded evidence",
    detail: "Evidence or a mandate version that no longer reflects current state is reused.",
    signal: "Mandate version in the capability trails the registry",
    control: "Mandate version binding and canonical evidence set hash",
    decision: "REJECTED BEFORE NETWORK",
    paymentReached: "NO",
    evidence: "tests/test_mandate_revocation.py",
  },
  {
    id: "cross-merchant",
    surface: "Cross-merchant evidence",
    detail: "Evidence owned by one merchant is offered to justify another merchant's product.",
    signal: "Requested evidence id resolves to a different merchant",
    control: "Merchant-scoped resolution refuses the lookup",
    decision: "EVIDENCE REFUSED",
    paymentReached: "NO",
    evidence: "Resolve safety invariant S3: 0 observed",
  },
  {
    id: "cross-sku",
    surface: "Cross-SKU evidence",
    detail: "Evidence bound to a different SKU is offered for the proposed SKU.",
    signal: "Requested evidence id resolves to a different SKU",
    control: "SKU-scoped resolution refuses the lookup",
    decision: "EVIDENCE REFUSED",
    paymentReached: "NO",
    evidence: "Resolve safety invariant S4: 0 observed",
  },
  {
    id: "authority-conflict",
    surface: "Authority conflict",
    detail: "Two trusted sources make contradictory claims about the same constraint.",
    signal: "Gap analysis records conflicting authoritative statements",
    control: "No forced resolution between trusted sources",
    decision: "REVIEW",
    paymentReached: "NO",
    evidence: "Resolve safety invariant S2: 0 observed",
  },
  {
    id: "capability-replay",
    surface: "Capability replay",
    detail: "A signed, still-valid capability is submitted for execution a second time.",
    signal: "Decision nonce already present in the execution ledger",
    control: "Single-use nonce ledger, checked before the provider call",
    decision: "REJECTED BEFORE NETWORK",
    paymentReached: "NO",
    evidence: "TEST CAPABILITY REPLAY in this lab",
  },
  {
    id: "consent-revocation",
    surface: "Consent revocation",
    detail: "The user withdraws consent after a valid capability has already been issued.",
    signal: "Current mandate state reads REVOKED at execution time",
    control: "Mandate registry revalidated immediately before execution",
    decision: "REJECTED BEFORE NETWORK",
    paymentReached: "NO",
    evidence: "REVOKED AFTER ALLOW example",
  },
  {
    id: "cross-run-consent",
    surface: "Cross-run consent reuse",
    detail: "One run's consent state is treated as authority for a different run.",
    signal: "Mandate identity in the capability does not match this run's mandate",
    control: "Mandate state isolated per commerce run",
    decision: "REJECTED BEFORE NETWORK",
    paymentReached: "NO",
    evidence: "tests/test_mandate_revocation.py",
  },
  {
    id: "listing-laundering",
    surface: "Listing category laundering",
    detail:
      "A listing keeps a benign declared category while its own text describes something else.",
    signal: "Trained classifier disagrees with the listing's declared category",
    control: "Mismatch raises investigation priority only; it cannot authorize",
    decision: "REVIEW PRIORITY RAISED",
    paymentReached: "NO",
    evidence: "artifacts/engineering/discovery/anomaly_evaluation.json",
  },
  {
    id: "unvouched-listing",
    surface: "Purchase of an unvouched listing",
    detail:
      "An agent proposes a product from a crawled catalog that no merchant has published terms for.",
    signal: "Zero merchant-controlled evidence resolves for this merchant and SKU",
    control: "Discovery cannot substitute for merchant evidence",
    decision: "REVIEW REQUIRED",
    paymentReached: "NO",
    evidence: "Any crawled listing in the discovery results",
  },
];

/**
 * Frozen evaluator output. Reproduced verbatim from the recorded artifact so the
 * interface cannot restate the result more favourably than the evaluation did.
 */
export const EVALUATION_EVIDENCE = {
  cases: 20,
  allow: 7,
  block: 3,
  review: 10,
  safetyViolations: 0,
  valueMoved: 2992300,
  currency: "INR",
  source: "docs/RESOLVE_EVALUATION_RESULTS.md",
  summarySha256: "c47836a8d0af42b39b3150be3de850ee883aa00d7248025d3a256cdfe714b1af",
  negativeResult:
    "Half of the initially non-executable cases stayed at REVIEW after bounded recovery. Evidence and execution requirements were not relaxed to improve the recovery rate.",
};

/** Engineering quality only. Updated when the suites change; a test count is
    never presented as scale, model quality, or authorization evidence. */
export const TEST_TOTALS = { python: 1061, ui: 87 };

export class SubmissionLock {
  #locked = false;

  acquire() {
    if (this.#locked) return false;
    this.#locked = true;
    return true;
  }

  release() {
    this.#locked = false;
  }

  get locked() {
    return this.#locked;
  }
}

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function statusClass(status) {
  const normalized = String(status || "WAITING").toLowerCase().replaceAll("_", "-");
  return `status status--${escapeHtml(normalized)}`;
}

function statusBadge(status) {
  return `<span class="${statusClass(status)}">${escapeHtml(status)}</span>`;
}

function yesNo(value) {
  return value ? "VERIFIED" : "FAILED";
}

function money(minor, currency) {
  const amount = Number(minor || 0) / 100;
  try {
    return new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency: currency || "INR",
      minimumFractionDigits: 0,
      maximumFractionDigits: 2,
    }).format(amount);
  } catch {
    return `${escapeHtml(currency)} ${amount.toFixed(2)}`;
  }
}

function shortId(value) {
  const text = String(value || "");
  return text.length > 24 ? `${text.slice(0, 12)}...${text.slice(-8)}` : text;
}

function humanize(value) {
  const text = String(value || "").replaceAll("_", " ").toLowerCase();
  return text ? text.replace(/^./, (character) => character.toUpperCase()) : "Not recorded";
}

function displayScore(value) {
  if (value === null || value === undefined) return "n/a";
  const score = Number(value);
  return Number.isFinite(score) ? score.toFixed(3) : String(value);
}

/**
 * Opt a figure into an entrance animation.
 *
 * The digits are never interpolated. Counting 0 -> N paints every integer below
 * N, and each of those frames is a number the run or the evaluation did not
 * produce: a screenshot taken mid-tween would misreport the result. So the
 * figure is always written at its true value and only the element moves.
 */
function figureAttr(value, { animate = false } = {}) {
  return animate && Number.isFinite(Number(value)) ? ' data-figure="animate"' : "";
}

export function liveModeStatusNote(live) {
  if (!live || live.available) return null;
  const problem = (live.problems || [])[0];
  const reason = (live.missing_configuration || []).length
    ? "Server-side credentials are not configured on this deployment."
    : problem
      ? `${problem}.`
      : "This deployment does not enable live execution.";
  return `LIVE TEST UNAVAILABLE. ${reason} Offline demo remains fully available.`;
}

/* ------------------------------------------------------------------ */
/* Decision spine                                                      */
/* ------------------------------------------------------------------ */

function spineState(status) {
  switch (String(status || "WAITING").toUpperCase()) {
    case "PASS":
    case "AUTHORIZED":
      return "pass";
    case "BLOCK":
      return "block";
    case "REVIEW":
      return "review";
    case "REJECTED":
      return "stopped";
    default:
      return "waiting";
  }
}

export function renderSpine(timeline) {
  const byId = new Map((timeline || []).map((item) => [item.id, item]));
  const haltIndex = SPINE_ORDER.findIndex((id) => {
    const state = spineState(byId.get(id)?.status);
    return state === "block" || state === "review" || state === "stopped";
  });
  return SPINE_ORDER.map((id, index) => {
    const item = byId.get(id) || { status: "WAITING", detail: null };
    const state = spineState(item.status);
    return `
      <li class="spine__step" data-step="${escapeHtml(id)}" data-state="${state}"${
        index === haltIndex ? ' data-halt="true"' : ""
      }>
        <span class="spine__node" aria-hidden="true"></span>
        <span class="spine__index">${String(index + 1).padStart(2, "0")}</span>
        <span class="spine__name">${escapeHtml(SPINE_LABELS[id])}</span>
        <span class="spine__status">${escapeHtml(item.status || "WAITING")}</span>
        <span class="spine__detail">${escapeHtml(item.detail || "Not reached")}</span>
      </li>`;
  }).join("");
}

/**
 * Fraction of the spine the signal is allowed to travel.
 *
 * Only steps that actually passed advance the signal. A step that blocked,
 * abstained, or was refused is where motion halts, so the fill stops short of
 * it and the stop bar renders on that step. A blocked run can therefore never
 * draw a line that reaches the provider.
 */
export function spineProgress(timeline) {
  const byId = new Map((timeline || []).map((item) => [item.id, item]));
  let advanced = 0;
  for (const [index, id] of SPINE_ORDER.entries()) {
    if (spineState(byId.get(id)?.status) !== "pass") break;
    advanced = index + 1;
  }
  return advanced / SPINE_ORDER.length;
}

/* ------------------------------------------------------------------ */
/* The chronological journey                                           */
/* ------------------------------------------------------------------ */

/**
 * The nine stages a purchase passes through, in the order they happen.
 *
 * This is deliberately not the controller's internal step list. A first-time
 * reader needs the story of the transaction; the controller's own timeline is
 * still available underneath each stage for anyone who wants it.
 */
export const JOURNEY_STAGES = [
  { id: "USER_INTENT", label: "User intent", plain: "You said what you wanted." },
  { id: "AGENT_SEARCH", label: "Agent search", plain: "The agent searched the catalog." },
  { id: "PRODUCT_SELECTED", label: "Product selected", plain: "One product was chosen." },
  { id: "MANDATE_EXTRACTED", label: "Mandate extracted", plain: "Your words became checkable rules." },
  { id: "ML_ANALYSIS", label: "Product analysis", plain: "Models read the listing and flagged what looks odd." },
  { id: "TRUSTED_EVIDENCE", label: "Trusted evidence", plain: "We looked for what the merchant can actually prove." },
  { id: "AUTHORIZATION", label: "Authorization", plain: "The controller decided: allow, block, or review." },
  { id: "PAYMENT_GATE", label: "Payment gate", plain: "Only a signed capability can pass this point." },
  { id: "OUTCOME", label: "Outcome", plain: "What happened to the money." },
];

const JOURNEY_ORDER = JOURNEY_STAGES.map((item) => item.id);

function stageState(status) {
  switch (String(status || "WAITING").toUpperCase()) {
    case "DONE":
    case "PASS":
    case "ALLOW":
    case "AUTHORIZED":
      return "pass";
    case "BLOCK":
      return "block";
    case "REVIEW":
      return "review";
    case "REJECTED":
    case "STOPPED":
      return "stopped";
    case "RUNNING":
      return "running";
    default:
      return "waiting";
  }
}

/**
 * Fold a discovery result and a controller run into the nine visible stages.
 *
 * Either half may be absent: a search with no selection yet fills the first
 * five, and an unvouched listing stops at stage six on purpose.
 */
export function buildJourney({ discovery, selection, snapshot } = {}) {
  const result = snapshot?.result;
  const timeline = new Map((snapshot?.timeline || []).map((item) => [item.id, item]));
  const decision = result?.decision;
  const execution = result?.execution;
  const stages = new Map(JOURNEY_STAGES.map((item) => [item.id, { ...item, status: "WAITING", detail: "Not reached yet." }]));

  const set = (id, status, detail) => {
    const stage = stages.get(id);
    if (stage) Object.assign(stage, { status, detail });
  };

  if (discovery) {
    const lines = discovery.mandate_plain_english || [];
    set("USER_INTENT", "DONE", `“${discovery.mandate?.raw_text || ""}”`);
    const retrieval = discovery.retrieval || {};
    set(
      "AGENT_SEARCH",
      "DONE",
      `Searched ${Number(retrieval.catalog_listings || 0).toLocaleString("en-IN")} listings, ` +
        `considered ${retrieval.candidates_considered ?? 0}, in ${Math.round(retrieval.retrieval_ms ?? 0)} ms.`,
    );
    set("MANDATE_EXTRACTED", "DONE", lines.join(" "));
    const candidates = discovery.candidates || [];
    if (candidates.length) {
      const flagged = candidates.filter(
        (item) => item.anomaly?.band === "HIGH" || item.anomaly?.band === "ELEVATED",
      ).length;
      set(
        "ML_ANALYSIS",
        "DONE",
        `Classified ${candidates.length} candidates; ${flagged} carry at least one signal worth a closer look.`,
      );
    }
  }

  if (selection) {
    set("PRODUCT_SELECTED", "DONE", selection.title || "A listing was selected.");
    if (!selection.transactable) {
      set(
        "TRUSTED_EVIDENCE",
        "REVIEW",
        "No merchant has published authoritative terms for this listing.",
      );
      set("AUTHORIZATION", "REVIEW", "Nothing to authorize: the evidence is incomplete.");
      set("PAYMENT_GATE", "REVIEW", "Never reached. Payment-provider calls: 0.");
      set(
        "OUTCOME",
        "REVIEW",
        "REVIEW REQUIRED. No money moved, and none could have.",
      );
    }
  }

  if (result) {
    const evidence = result.evidence || {};
    const evidenceStep = timeline.get("EVIDENCE_RETRIEVAL");
    set(
      "TRUSTED_EVIDENCE",
      evidenceStep?.status === "PASS" ? "DONE" : evidenceStep?.status || "WAITING",
      `${evidence.trusted_evidence_count ?? 0} merchant-controlled evidence items resolved for this exact merchant and SKU.`,
    );
    const authorization = result.authorization || {};
    set(
      "AUTHORIZATION",
      decision === "ALLOW" ? "DONE" : decision || "WAITING",
      result.decision_reason || `Controller returned ${decision}.`,
    );
    const calls = execution?.razorpay_calls ?? 0;
    if (decision === "ALLOW") {
      set(
        "PAYMENT_GATE",
        execution?.status === "REJECTED_BEFORE_NETWORK" ? "REJECTED" : "DONE",
        execution?.status === "REJECTED_BEFORE_NETWORK"
          ? `${humanize(execution.reason)}. The gate refused before any provider call.`
          : `Signed single-use capability accepted. Adapter calls: ${calls}.`,
      );
    } else {
      set("PAYMENT_GATE", decision || "WAITING", `Never reached. Payment-provider calls: ${calls}.`);
    }
    set(
      "OUTCOME",
      execution?.status === "REJECTED_BEFORE_NETWORK" ? "REJECTED" : decision || "WAITING",
      outcomeSentence(result),
    );
    if (!discovery) {
      set("USER_INTENT", "DONE", `“${result.buyer?.mandate || ""}”`);
      set("AGENT_SEARCH", "DONE", "The agent used the registered merchant catalog.");
      set("PRODUCT_SELECTED", "DONE", result.buyer?.product || "");
      set("MANDATE_EXTRACTED", "DONE", "Hard constraints and semantic constraints extracted.");
      set("ML_ANALYSIS", "DONE", "Not applicable: this product came from the registered catalog.");
    }
  }

  return JOURNEY_ORDER.map((id) => stages.get(id));
}

function outcomeSentence(result) {
  const execution = result?.execution || {};
  const order = execution.order;
  if (execution.status === "REJECTED_BEFORE_NETWORK") {
    return "Nothing was charged. The capability was refused at the gate.";
  }
  if (order) {
    return `An order for ${money(order.amount, order.currency)} was created.`;
  }
  if (result?.decision === "ALLOW") {
    return "A capability was issued and is being held, not spent.";
  }
  if (result?.decision === "BLOCK") {
    return "Nothing was charged. The proposal contradicted the mandate.";
  }
  return "Nothing was charged. MandateGuard refused to guess.";
}

export function renderJourney(stages) {
  const list = stages || buildJourney({});
  const haltIndex = list.findIndex((item) => {
    const state = stageState(item.status);
    return state === "block" || state === "review" || state === "stopped";
  });
  return list
    .map((item, index) => {
      const state = stageState(item.status);
      return `
      <li class="journey__step" data-step="${escapeHtml(item.id)}" data-state="${state}"${
        index === haltIndex ? ' data-halt="true"' : ""
      }>
        <span class="journey__node" aria-hidden="true"></span>
        <span class="journey__index">${String(index + 1).padStart(2, "0")}</span>
        <div class="journey__body">
          <p class="journey__name">${escapeHtml(item.label)}</p>
          <p class="journey__plain">${escapeHtml(item.plain)}</p>
          <p class="journey__detail">${escapeHtml(item.detail || "Not reached yet.")}</p>
        </div>
        <span class="journey__status">${escapeHtml(item.status || "WAITING")}</span>
      </li>`;
    })
    .join("");
}

export function journeyProgress(stages) {
  const list = stages || [];
  let advanced = 0;
  for (const [index, item] of list.entries()) {
    if (stageState(item.status) !== "pass") break;
    advanced = index + 1;
  }
  return list.length ? advanced / list.length : 0;
}

/* ------------------------------------------------------------------ */
/* Discovery over the large catalog                                    */
/* ------------------------------------------------------------------ */

export function renderMandatePanel(discovery) {
  if (!discovery) return "";
  const lines = (discovery.mandate_plain_english || [])
    .map((line) => `<li>${escapeHtml(line)}</li>`)
    .join("");
  const unresolved = (discovery.mandate?.unresolved || [])
    .map((code) => `<code>${escapeHtml(humanize(code))}</code>`)
    .join("");
  const filtered = Object.entries(discovery.retrieval?.filtered_out || {})
    .map(
      ([reason, count]) =>
        `<li><strong>${escapeHtml(count)}</strong> ${escapeHtml(reason)}</li>`,
    )
    .join("");
  return `
    <div class="mandate">
      <div class="mandate__col">
        <p class="microlabel">WHAT WE READ FROM YOUR WORDS</p>
        <ul class="mandate__list">${lines || "<li>No constraints were extracted.</li>"}</ul>
        ${
          unresolved
            ? `<p class="mandate__unresolved"><span>NOT STATED</span> ${unresolved}</p>`
            : ""
        }
      </div>
      <div class="mandate__col">
        <p class="microlabel">WHAT THE SEARCH RULED OUT</p>
        <ul class="mandate__list mandate__list--filtered">${
          filtered || "<li>Nothing was filtered out by your constraints.</li>"
        }</ul>
        <p class="mandate__note">
          A price ceiling is not a ranking preference. A listing above it is not a candidate at all.
        </p>
      </div>
    </div>`;
}

function transactabilityRows(transactability) {
  return (transactability?.checks || [])
    .map(
      (check) => `
        <li data-status="${escapeHtml(check.status)}">
          <span>${escapeHtml(check.label)}</span>
          <strong data-readiness="${escapeHtml(check.status)}">${escapeHtml(check.status)}</strong>
          <small>${escapeHtml(check.detail)}</small>
        </li>`,
    )
    .join("");
}

function anomalyRows(anomaly) {
  const triggered = (anomaly?.features || []).filter((item) => item.triggered);
  if (!triggered.length) {
    return '<li class="signal signal--clean">No defensive signal fired on this listing.</li>';
  }
  return triggered
    .sort((a, b) => b.value * b.weight - a.value * a.weight)
    .map(
      (item) => `
        <li class="signal">
          <p class="signal__question">${escapeHtml(item.question)}</p>
          <p class="signal__finding">${escapeHtml(item.finding)}</p>
        </li>`,
    )
    .join("");
}

//: Every price in the crawled catalog was captured between December 2015 and
//: June 2016. Rendering one bare, next to a buy button, invites the reader to
//: take it as a live offer, so it never appears without this label.
export const HISTORICAL_PRICE_LABEL = "Historical listing price";
export const HISTORICAL_PRICE_PERIOD = "2015-2016 dataset snapshot";
export const HISTORICAL_PRICE_EXPLANATION =
  "Marketplace prices come from the historical discovery dataset and are not live offers.";

export function renderListingPrice(candidate) {
  const registered = candidate.source === "mandateguard";
  if (candidate.price_minor === null || candidate.price_minor === undefined) {
    return `
      <p class="listing__price">
        <span class="listing__pricevalue listing__pricevalue--absent">No published price</span>
      </p>`;
  }
  const amount = money(candidate.price_minor, candidate.currency);
  if (registered) {
    // A registered product's price comes from merchant-published terms, not
    // from the 2015-2016 crawl, so it is not labelled historical.
    return `
      <p class="listing__price">
        <span class="listing__pricelabel">Merchant-published price</span>
        <span class="listing__pricevalue">${escapeHtml(amount)}</span>
      </p>`;
  }
  return `
    <p class="listing__price" data-historical="true">
      <span class="listing__pricelabel">${escapeHtml(HISTORICAL_PRICE_LABEL)}</span>
      <span class="listing__pricevalue">${escapeHtml(amount)}</span>
      <span class="listing__pricenote">${escapeHtml(HISTORICAL_PRICE_PERIOD)}</span>
    </p>`;
}

export function renderDiscoveryCandidate(candidate, { selected = false } = {}) {
  const mismatch = candidate.classification?.mismatch;
  const registered = candidate.source === "mandateguard";
  return `
    <article class="listing" data-transactable="${candidate.transactable ? "true" : "false"}"
             data-selected="${selected ? "true" : "false"}"
             data-product="${escapeHtml(candidate.catalog_product_id)}">
      <header class="listing__head">
        <div>
          <p class="listing__tier">
            <span class="tierchip tierchip--${registered ? "registered" : "discovery"}">${
              registered ? "REGISTERED MERCHANT" : "DISCOVERY LISTING"
            }</span>
            <span class="listing__stage">${escapeHtml(humanize(candidate.stage))}</span>
          </p>
          <h3 class="listing__name">${escapeHtml(candidate.title)}</h3>
          ${renderListingPrice(candidate)}
          <p class="listing__meta">
            ${escapeHtml(candidate.top_category)}
            ${candidate.brand ? ` &middot; ${escapeHtml(candidate.brand)}` : ""}
          </p>
        </div>
        <p class="listing__status" data-status="${escapeHtml(candidate.transactability?.status)}">
          ${escapeHtml(candidate.transactability?.status || "UNKNOWN")}
        </p>
      </header>

      <p class="listing__why">${escapeHtml(candidate.match?.headline || "")}</p>
      <p class="listing__whydetail">${escapeHtml(candidate.match?.detail || "")}</p>

      <ul class="readiness readiness--listing">${transactabilityRows(candidate.transactability)}</ul>

      <p class="listing__next">${escapeHtml(candidate.transactability?.next_action || "")}</p>

      <details class="disclosure disclosure--inline">
        <summary class="disclosure__summary">
          <span>Technical detail</span>
          <small>Match scores, classifier, defensive signals</small>
        </summary>
        <dl class="datarows datarows--inline">
          <div><dt>Match score</dt><dd><code>${escapeHtml(displayScore(candidate.match?.score))}</code></dd></div>
          <div><dt>Lexical</dt><dd><code>${escapeHtml(displayScore(candidate.match?.lexical_score))}</code></dd></div>
          <div><dt>Embedding</dt><dd><code>${escapeHtml(displayScore(candidate.match?.dense_score))}</code></dd></div>
        </dl>
        <p class="microlabel">CLASSIFIER (ADVISORY)</p>
        <p class="listing__classifier">
          Predicted category <strong>${escapeHtml(
            candidate.classification?.predicted_category || "not classified",
          )}</strong>${
            mismatch
              ? ` &middot; mismatch <strong data-severity="${escapeHtml(
                  mismatch.severity,
                )}">${escapeHtml(mismatch.severity)}</strong>`
              : ""
          }
        </p>
        ${mismatch ? `<p class="listing__mismatch">${escapeHtml(mismatch.rationale)}</p>` : ""}
        <p class="microlabel">DEFENSIVE SIGNALS</p>
        <ul class="signals">${anomalyRows(candidate.anomaly)}</ul>
        <p class="authority-alert">${escapeHtml(
          candidate.transactability?.authority_notice || "",
        )}</p>
      </details>

      <div class="listing__actions">
        <button class="btn ${
          candidate.transactable ? "btn--primary" : "btn--secondary"
        }" type="button" data-select="${escapeHtml(candidate.catalog_product_id)}">
          ${candidate.transactable ? "AUTHORIZE THIS PURCHASE" : "WHY CAN'T I BUY THIS?"}
        </button>
      </div>
    </article>`;
}

export function renderDiscoveryResults(discovery) {
  if (!discovery) return "";
  const candidates = discovery.candidates || [];
  if (!candidates.length) {
    return `
      <div class="empty-evidence">
        <strong>NO LISTING MATCHED THAT INTENT</strong>
        <p>Every candidate was ruled out by a constraint you stated, or the catalog carries
           nothing like it. Neither is a reason to relax the constraint.</p>
      </div>`;
  }
  // Persistent, above the results, on every render. Not a tooltip, not tucked
  // into provenance: a reader who never opens a disclosure still sees it.
  const historicalNote = candidates.some((item) => item.source !== "mandateguard")
    ? `<p class="listing-note" data-historical-note="true">
         ${escapeHtml(HISTORICAL_PRICE_EXPLANATION)}
       </p>`
    : "";
  return (
    historicalNote + candidates.map((item) => renderDiscoveryCandidate(item)).join("")
  );
}

export function discoverySummaryLine(discovery) {
  if (!discovery) return "";
  const retrieval = discovery.retrieval || {};
  const summary = discovery.summary || {};
  const total = Number(retrieval.catalog_listings || 0).toLocaleString("en-IN");
  return (
    `${total} catalog listings searched in ${Math.round(retrieval.retrieval_ms ?? 0)} ms · ` +
    `${summary.evidence_ready ?? 0} of ${summary.listings ?? 0} shown are transactable today`
  );
}

/* ------------------------------------------------------------------ */
/* Verdict and decision stories                                        */
/* ------------------------------------------------------------------ */

const DECISION_RESOLUTION = {
  ALLOW: "Mandate verified. Execution capability issued.",
  BLOCK: "Execution prevented before Razorpay.",
  REVIEW: "Human/evidence review required before execution.",
  ERROR: "The run stopped safely before execution.",
};

export function renderDecisionBanner(result) {
  const decision = result?.decision || "ERROR";
  const execution = result?.execution || {};
  const rejected = execution.status === "REJECTED_BEFORE_NETWORK";
  const order = execution.order;
  const headline = rejected ? "REJECTED BEFORE NETWORK" : decision;
  // A refusal at the execution gate is not a controller decision. Saying
  // "final controller: rejected" would misreport an authorization that passed,
  // so the gate and the controller are labelled separately.
  const kicker = rejected ? "EXECUTION GATE" : "FINAL CONTROLLER";
  const resolution = rejected
    ? "The controller authorized this transaction. The execution gate refused it before any provider call."
    : DECISION_RESOLUTION[decision] || "The controller returned a bounded result.";
  const controllerLine = rejected
    ? `<p class="decision-banner__controller">FINAL CONTROLLER <strong>${escapeHtml(
        decision,
      )}</strong></p>`
    : "";
  const restraint =
    decision === "REVIEW"
      ? '<p class="decision-restraint">MandateGuard refused to guess. No payment was attempted.</p>'
      : "";
  const valueLabel = order ? "ORDER CREATED" : "VALUE MOVED";
  const valueAmount = order ? money(order.amount, order.currency) : money(0, "INR");
  const callsLabel = order ? "ADAPTER CALLS" : "RAZORPAY CALLS";
  return `
    <div class="decision-banner decision-banner--${escapeHtml(decision.toLowerCase())}"
         data-rejected="${rejected ? "true" : "false"}">
      <div class="decision-banner__verdict">
        <p class="decision-banner__kicker">${kicker}</p>
        <strong class="decision-banner__word">${escapeHtml(headline)}</strong>
        <p class="decision-banner__resolution">${escapeHtml(resolution)}</p>
        ${controllerLine}
      </div>
      <div class="decision-banner__reason">
        <p class="microlabel">EXACT REASON</p>
        <p class="decision-banner__reasontext">${escapeHtml(
          rejected
            ? humanize(execution.reason)
            : result?.decision_reason || "No controller result is available.",
        )}</p>
        ${restraint}
      </div>
      <dl class="ledger">
        <div class="ledger__item">
          <dt>${valueLabel}</dt>
          <dd class="ledger__value">${valueAmount}</dd>
        </div>
        <div class="ledger__item">
          <dt>${callsLabel}</dt>
          <dd class="ledger__value">${escapeHtml(execution.razorpay_calls ?? 0)}</dd>
        </div>
        <div class="ledger__item">
          <dt>EXTERNAL CALLS</dt>
          <dd class="ledger__value">${escapeHtml(execution.external_network_calls ?? 0)}</dd>
        </div>
      </dl>
    </div>
  `;
}

/** Locate the single check that decided a BLOCK, preferring the semantic layer. */
export function failedConstraint(authorization) {
  const semantic = (authorization?.semantic?.checks || []).find(
    (check) => check.status === "VIOLATION" || check.status === "FAIL",
  );
  if (semantic) {
    return {
      layer: "SEMANTIC",
      family: semantic.family || semantic.constraint_id,
      constraint: semantic.constraint,
      reason: semantic.reason,
      id: semantic.constraint_id,
    };
  }
  const deterministic = (authorization?.deterministic?.tier_a || [])
    .concat(authorization?.deterministic?.tier_b || [])
    .find((check) => check.status !== "PASS" && check.status !== "NOT_EVALUATED");
  if (deterministic) {
    return {
      layer: "DETERMINISTIC",
      family: deterministic.family,
      constraint: deterministic.label,
      reason: deterministic.reason,
      id: deterministic.family,
    };
  }
  return null;
}

function evidenceClaimList(cards) {
  const ordered = [...(cards || [])].sort((a, b) =>
    a.scope === b.scope ? 0 : a.scope === "PRODUCT" ? -1 : 1,
  );
  if (!ordered.length) {
    return '<li class="claim claim--empty">No trusted merchant evidence was resolved.</li>';
  }
  return ordered
    .map(
      (card) => `
        <li class="claim">
          <p class="claim__meta">
            <span class="tierchip">TRUSTED</span>
            <span class="claim__scope">${escapeHtml(card.scope)}</span>
            <code>${escapeHtml(card.evidence_id)}</code>
          </p>
          <p class="claim__text">${escapeHtml(card.text)}</p>
        </li>`,
    )
    .join("");
}

export function renderBlockStory(result) {
  const failed = failedConstraint(result?.authorization);
  const evidence = result?.evidence || {};
  return `
    <div class="story-block">
      <h2 class="story__question">Why was this transaction blocked?</h2>
      <div class="conflict">
        <section class="conflict__side conflict__side--mandate" aria-labelledby="conflict-mandate">
          <h3 class="conflict__title" id="conflict-mandate">USER MANDATE</h3>
          <p class="conflict__quote">${escapeHtml(result?.buyer?.mandate)}</p>
          <p class="microlabel">DECLARED CONSTRAINT</p>
          <p class="conflict__declared">${escapeHtml(
            failed?.constraint || "No failing constraint was recorded.",
          )}</p>
        </section>
        <div class="conflict__pivot" aria-hidden="true"><span>VS</span></div>
        <section class="conflict__side conflict__side--evidence" aria-labelledby="conflict-evidence">
          <h3 class="conflict__title" id="conflict-evidence">TRUSTED MERCHANT EVIDENCE</h3>
          <ul class="claimlist">${evidenceClaimList(evidence.cards)}</ul>
        </section>
      </div>
      <p class="conflict__verdict">${escapeHtml(
        failed?.reason || result?.decision_reason || "The controller recorded no reason.",
      )}</p>
      <dl class="factrow">
        <div class="factrow__item">
          <dt>FAILED CONSTRAINT</dt>
          <dd class="factrow__value factrow__value--danger">${escapeHtml(
            failed?.family || "NOT RECORDED",
          )}</dd>
        </div>
        <div class="factrow__item">
          <dt>EVIDENCE TIER</dt>
          <dd class="factrow__value">${escapeHtml(
            evidence.classification || "TRUSTED MERCHANT EVIDENCE",
          )}</dd>
        </div>
        <div class="factrow__item">
          <dt>CONTROLLER</dt>
          <dd class="factrow__value factrow__value--danger">${escapeHtml(
            result?.authorization?.final_controller || "BLOCK",
          )}</dd>
        </div>
      </dl>
    </div>
  `;
}

export function renderReviewStory(result) {
  const readiness = result?.transactability?.readiness || [];
  const known = readiness.filter((item) => item.status === "VERIFIED");
  const unknown = readiness.filter((item) => item.status !== "VERIFIED");
  const abstained = (result?.authorization?.semantic?.checks || []).filter(
    (check) => check.status === "ABSTAIN" || check.status === "NOT_EVALUATED",
  );
  const list = (items, empty) =>
    items.length
      ? items
          .map(
            (item) =>
              `<li><span>${escapeHtml(item.label)}</span><strong data-readiness="${escapeHtml(
                item.status,
              )}">${escapeHtml(item.status)}</strong></li>`,
          )
          .join("")
      : `<li class="knowledge__empty">${escapeHtml(empty)}</li>`;
  const undecidable = abstained.length
    ? abstained
        .map(
          (check) => `
          <li>
            <p class="undecidable__constraint">${escapeHtml(check.constraint)}</p>
            <p class="undecidable__why">MandateGuard cannot determine whether this transaction violates that constraint: ${escapeHtml(
              check.reason || "trusted evidence is insufficient",
            )}.</p>
          </li>`,
        )
        .join("")
    : '<li><p class="undecidable__why">No semantic constraint abstained on this run.</p></li>';
  return `
    <div class="story-block">
      <h2 class="story__question">What is missing, and why it stops the payment</h2>
      <div class="knowledge">
        <section class="knowledge__col knowledge__col--known" aria-labelledby="know-title">
          <h3 class="knowledge__title" id="know-title">WHAT WE KNOW</h3>
          <ul class="knowledge__list">${list(known, "Nothing was independently verified.")}</ul>
        </section>
        <section class="knowledge__col knowledge__col--unknown" aria-labelledby="unknown-title">
          <h3 class="knowledge__title" id="unknown-title">WHAT WE DO NOT KNOW</h3>
          <ul class="knowledge__list">${list(unknown, "No evidence gap was recorded.")}</ul>
        </section>
        <section class="knowledge__col knowledge__col--matters" aria-labelledby="matters-title">
          <h3 class="knowledge__title" id="matters-title">WHY THAT MATTERS</h3>
          <ul class="undecidable">${undecidable}</ul>
        </section>
      </div>
      <dl class="factrow">
        <div class="factrow__item">
          <dt>OUTCOME</dt>
          <dd class="factrow__value factrow__value--warning">REVIEW</dd>
        </div>
        <div class="factrow__item">
          <dt>MONEY MOVED</dt>
          <dd class="factrow__value">${money(0, "INR")}</dd>
        </div>
        <div class="factrow__item">
          <dt>NEXT ACTION</dt>
          <dd class="factrow__value factrow__value--plain">${escapeHtml(
            result?.transactability?.next_action || "No evidence-readiness action is required.",
          )}</dd>
        </div>
      </dl>
    </div>
  `;
}

export function renderAllowStory(result) {
  const execution = result?.execution || {};
  const order = execution.order || {};
  const recovery = result?.recovery;
  const recovered =
    recovery?.status === "RESOLVED"
      ? `<p class="story__recovered">Reached after ${escapeHtml(
          recovery.resolved_after,
        )} and a fresh run of the full controller. Payment-provider calls before the final ALLOW: <strong>${escapeHtml(
          recovery.payment_provider_calls_before_final_allow ?? 0,
        )}</strong>.</p>`
      : "";
  return `
    <div class="story-block">
      <h2 class="story__question">What the capability permits</h2>
      ${recovered}
      <dl class="factrow">
        <div class="factrow__item">
          <dt>PRODUCT</dt>
          <dd class="factrow__value factrow__value--plain">${escapeHtml(result?.buyer?.product)}</dd>
        </div>
        <div class="factrow__item">
          <dt>ORDER VALUE</dt>
          <dd class="factrow__value">${
            order.amount === undefined
              ? money(result?.buyer?.price_minor, result?.buyer?.currency)
              : money(order.amount, order.currency)
          }</dd>
        </div>
        <div class="factrow__item">
          <dt>SCOPE</dt>
          <dd class="factrow__value factrow__value--plain">Single use, one merchant, one SKU</dd>
        </div>
      </dl>
    </div>
  `;
}

/** Consent state strip. The row that changes under revocation is the whole point of the surface. */
export function renderConsentStrip(execution) {
  const capability = execution?.capability || {};
  const consent = execution?.consent || {};
  const status = String(consent.status || "MISSING").toUpperCase();
  const active = status === "ACTIVE";
  const rows = [
    ["SIGNED", yesNo(capability.signature_verified), capability.signature_verified],
    ["UNEXPIRED", yesNo(capability.expiry_valid), capability.expiry_valid],
    [
      "HASH BOUND",
      yesNo(capability.transaction_bound && capability.request_bound),
      Boolean(capability.transaction_bound && capability.request_bound),
    ],
    ["CONSENT", status, active],
  ]
    .map(
      ([label, value, ok], index) => `
        <li class="consentstrip__row" data-row="${escapeHtml(label)}" data-ok="${ok ? "true" : "false"}"
            ${index === 3 ? 'data-consent="true"' : ""}>
          <span class="consentstrip__label">${escapeHtml(label)}</span>
          <span class="consentstrip__value">${escapeHtml(value)}</span>
        </li>`,
    )
    .join("");
  return `<ul class="consentstrip" data-consent-status="${escapeHtml(status)}">${rows}</ul>`;
}

export function renderRevocationStory(execution) {
  const consent = execution?.consent || {};
  const status = String(consent.status || "MISSING").toUpperCase();
  const withdrawn = status === "REVOKED" || status === "SUPERSEDED";
  return `
    <div class="story-block">
      <h2 class="story__question">Consent state at the moment of execution</h2>
      ${renderConsentStrip(execution)}
      <div class="revocation__result" data-withdrawn="${withdrawn ? "true" : "false"}">
        <p class="revocation__headline">${
          withdrawn ? "REJECTED BEFORE NETWORK" : "CAPABILITY HELD, NOT YET EXECUTED"
        }</p>
        <p class="revocation__teaching">${escapeHtml(
          consent.teaching || "MandateGuard revalidates its trusted mandate state before execution.",
        )}</p>
        <p class="revocation__authority">Authority: ${escapeHtml(
          consent.authority || "DEMO USER REVOCATION",
        )}</p>
      </div>
    </div>
  `;
}

export function renderStory(result) {
  const decision = result?.decision;
  const execution = result?.execution || {};
  const consentStatus = String(execution.consent?.status || "").toUpperCase();
  if (decision === "BLOCK") return renderBlockStory(result);
  if (decision === "REVIEW") return renderReviewStory(result);
  if (decision === "ALLOW" && execution.status === "AUTHORIZED") {
    return renderRevocationStory(execution);
  }
  if (decision === "ALLOW" && execution.status === "REJECTED_BEFORE_NETWORK") {
    return renderRevocationStory(execution);
  }
  if (decision === "ALLOW" && consentStatus && execution.status !== "ORDER_CREATED") {
    return renderRevocationStory(execution);
  }
  if (decision === "ALLOW") return renderAllowStory(result);
  return "";
}

/* ------------------------------------------------------------------ */
/* Verification record                                                 */
/* ------------------------------------------------------------------ */

export function renderBuyerPanel(buyer) {
  const tools = (buyer?.tool_calls || [])
    .map(
      (call) => `
        <li>
          <span>${escapeHtml(String(call.sequence).padStart(2, "0"))}</span>
          <code>${escapeHtml(call.name)}</code>
        </li>`,
    )
    .join("");
  return `
    <div class="panel">
      <div class="panel__head">
        <h3 class="panel__title">Buyer and product</h3>
        <p class="panel__meta">No payment authority</p>
      </div>
      <div class="product">
        <p class="product__price">${money(buyer?.price_minor, buyer?.currency)}</p>
        <h4 class="product__name">${escapeHtml(buyer?.product)}</h4>
        <p class="product__desc">${escapeHtml(buyer?.product_description)}</p>
      </div>
      <dl class="datarows">
        <div><dt>Merchant</dt><dd><code>${escapeHtml(buyer?.merchant)}</code></dd></div>
        <div><dt>SKU</dt><dd><code>${escapeHtml(buyer?.sku)}</code></dd></div>
        <div><dt>Currency</dt><dd>${escapeHtml(buyer?.currency)}</dd></div>
        <div><dt>Quantity</dt><dd>${escapeHtml(buyer?.quantity)}</dd></div>
      </dl>
      <p class="microlabel">BUYER TOOL CALLS</p>
      <ol class="toollist">${tools || "<li>No tool calls recorded.</li>"}</ol>
      <p class="microlabel">BUYER RATIONALE</p>
      <p class="panel__prose">${escapeHtml(buyer?.rationale)}</p>
      <p class="authority-alert">${escapeHtml(buyer?.authority_notice)}</p>
    </div>
  `;
}

export function renderEvidencePanel(evidence) {
  const cards = (evidence?.cards || [])
    .map(
      (card) => `
        <article class="evidence-card" data-acquisition="${escapeHtml(card.acquisition || "")}">
          <p class="evidence-card__top">
            <span class="tierchip">TRUSTED</span>
            <code>${escapeHtml(card.evidence_id)}</code>
          </p>
          <p class="evidence-card__text">${escapeHtml(card.text)}</p>
          <dl class="evidence-card__meta">
            <div><dt>Source</dt><dd>${escapeHtml(card.source_kind)}</dd></div>
            <div><dt>Scope</dt><dd>${escapeHtml(card.scope)}</dd></div>
            <div><dt>Score</dt><dd><code>${escapeHtml(displayScore(card.retrieval_score))}</code></dd></div>
          </dl>
        </article>`,
    )
    .join("");
  const empty = `
    <div class="empty-evidence">
      <strong>NO TRUSTED MERCHANT EVIDENCE SELECTED</strong>
      <p>The controller cannot treat buyer prose as a substitute. The result must route to REVIEW.</p>
    </div>`;
  return `
    <div class="panel">
      <div class="panel__head">
        <h3 class="panel__title">Evidence retrieval</h3>
        <p class="panel__meta">${escapeHtml(evidence?.retrieval_method)}</p>
      </div>
      <dl class="datarows datarows--inline">
        <div><dt>Top-k</dt><dd><code>${escapeHtml(evidence?.top_k)}</code></dd></div>
        <div><dt>Trusted count</dt><dd><code>${escapeHtml(evidence?.trusted_evidence_count)}</code></dd></div>
        <div><dt>Alpha</dt><dd><code>${escapeHtml(evidence?.alpha)}</code></dd></div>
      </dl>
      <div class="evidence-grid">${cards || empty}</div>
      <div class="buyer-text">
        <p class="buyer-text__head">
          <strong>BUYER-PROVIDED TEXT</strong>
          <span>NOT TRUSTED EVIDENCE</span>
        </p>
        <p>${escapeHtml(evidence?.buyer_text?.text)}</p>
      </div>
    </div>
  `;
}

export function renderReviewRecovery(recovery, execution) {
  if (!recovery) return "";
  const gap = recovery.gap;
  const source = recovery.trusted_source;
  const calls = recovery.payment_provider_calls_before_final_allow ?? execution?.razorpay_calls ?? 0;
  if (recovery.status === "RESOLVED") {
    return `
      <div class="resolve resolve--resolved">
        <p class="microlabel">REVIEW RESOLVED</p>
        <strong class="resolve__transition">${escapeHtml(recovery.transition)}</strong>
        <p>Resolved after: <b>${escapeHtml(recovery.resolved_after)}</b></p>
        <p class="resolve__accounting">
          <span>New trusted items <strong>${escapeHtml(recovery.new_evidence_items)}</strong></span>
          <span>Razorpay calls before final ALLOW <strong>${escapeHtml(calls)}</strong></span>
        </p>
        <p class="resolve__boundary">A fresh evidence set was canonicalized and the full controller ran again.</p>
      </div>`;
  }
  const action = recovery.action?.enabled
    ? '<button class="btn btn--resolve" id="acquire-evidence-button" type="button">ACQUIRE TRUSTED EVIDENCE</button>'
    : `<p class="resolve__unavailable">${escapeHtml(humanize(recovery.status))}. The controller remains at REVIEW.</p>`;
  return `
    <div class="resolve">
      <div class="resolve__head">
        <div>
          <p class="microlabel">EVIDENCE GAP</p>
          <h4 class="resolve__gap">${escapeHtml(
            gap?.reason || "No registered recoverable gap was identified.",
          )}</h4>
        </div>
        ${statusBadge("REVIEW")}
      </div>
      <div class="resolve__source">
        <p class="microlabel">TRUSTED SOURCE AVAILABLE</p>
        <strong>${escapeHtml(source?.label || "No registered source")}</strong>
        <small>Selected by the server registry. Buyer source input is disabled.</small>
      </div>
      <p class="resolve__accounting">
        <span>Razorpay calls <strong>${escapeHtml(execution?.razorpay_calls ?? 0)}</strong></span>
        <span>Acquisition round <strong>${escapeHtml(
          `${recovery.rounds_used}/${recovery.max_rounds}`,
        )}</strong></span>
      </p>
      ${action}
    </div>`;
}

export function renderTransactability(transactability) {
  if (!transactability) return "";
  const rows = (transactability.readiness || [])
    .map(
      (item) =>
        `<li><span>${escapeHtml(item.label)}</span><strong data-readiness="${escapeHtml(
          item.status,
        )}">${escapeHtml(item.status)}</strong></li>`,
    )
    .join("");
  return `
    <div class="transactability">
      <div class="transactability__head">
        <div>
          <p class="microlabel">CURRENT STATUS</p>
          <h4>${escapeHtml(transactability.status)}</h4>
        </div>
        <strong class="transactability__readiness">EVIDENCE READINESS ${escapeHtml(
          transactability.evidence_readiness || "UNKNOWN",
        )}</strong>
      </div>
      <ul class="readiness">${rows}</ul>
      <div class="transactability__next">
        <p class="microlabel">NEXT ACTION</p>
        <p>${escapeHtml(transactability.next_action)}</p>
      </div>
      <small>${escapeHtml(transactability.authority_notice)}</small>
    </div>`;
}

function renderCheckRows(checks) {
  return (checks || [])
    .map(
      (check) => `
        <li>
          <div>
            <code>${escapeHtml(check.family || check.constraint_id)}</code>
            <span>${escapeHtml(check.label || check.family || "Semantic constraint")}</span>
          </div>
          ${statusBadge(check.status)}
          ${check.reason ? `<p>${escapeHtml(check.reason)}</p>` : ""}
        </li>`,
    )
    .join("");
}

export function renderAuthorizationPanel(authorization) {
  const deterministic = authorization?.deterministic || {};
  const semantic = authorization?.semantic || {};
  const cache = semantic.cache || {};
  return `
    <div class="panel panel--authorization">
      <div class="panel__head">
        <h3 class="panel__title">Authorization</h3>
        <p class="panel__meta">${escapeHtml(authorization?.controller_source)}</p>
      </div>
      <div class="layer">
        <div class="layer__head">
          <span class="layer__key">A</span>
          <div><strong>DETERMINISTIC</strong><small>Tier A/B checks</small></div>
          ${statusBadge(deterministic.action === "ALLOW" ? "PASS" : deterministic.action)}
        </div>
        <ul class="checklist">${renderCheckRows(deterministic.tier_a)}</ul>
        <details class="disclosure disclosure--inline">
          <summary class="disclosure__summary"><span>Tier B consistency checks</span></summary>
          <ul class="checklist">${renderCheckRows(deterministic.tier_b)}</ul>
        </details>
      </div>
      <div class="layer">
        <div class="layer__head">
          <span class="layer__key">B</span>
          <div><strong>SEMANTIC</strong><small>Purpose, exclusions, recurrence</small></div>
          ${statusBadge(semantic.verdict)}
        </div>
        <ul class="checklist checklist--semantic">${
          renderCheckRows(semantic.checks) ||
          '<li class="not-evaluated">No semantic constraints were evaluated.</li>'
        }</ul>
        <p class="cachestrip">
          <span>SEMANTIC CACHE</span>
          <strong>${escapeHtml(cache.status || "NOT USED")}</strong>
          <code>${escapeHtml(cache.key_prefix || "NO KEY")}</code>
        </p>
      </div>
      <p class="final-controller final-controller--${escapeHtml(
        String(authorization?.final_controller || "ERROR").toLowerCase(),
      )}">
        <span>C FINAL CONTROLLER</span>
        <strong>${escapeHtml(authorization?.final_controller)}</strong>
      </p>
    </div>
  `;
}

export function renderExecutionPanel(execution) {
  if (execution?.status === "AUTHORIZED" || execution?.status === "REJECTED_BEFORE_NETWORK") {
    const capability = execution.capability || {};
    const consent = execution.consent || {};
    const rejected = execution.status === "REJECTED_BEFORE_NETWORK";
    const bindingRows = [
      ["Signature", capability.signature_verified],
      ["Transaction bound", capability.transaction_bound],
      ["Request bound", capability.request_bound],
      ["Merchant bound", capability.merchant_bound],
      ["Mandate identity", capability.mandate_identity_bound],
      ["Mandate version", capability.mandate_version_bound],
      ["Expiry valid", capability.expiry_valid],
    ]
      .map(
        ([label, value]) => `
          <li><span>${escapeHtml(label)}</span><strong data-valid="${value ? "true" : "false"}">${yesNo(
            value,
          )}</strong></li>`,
      )
      .join("");
    const controls = [
      consent.can_revoke
        ? '<button class="btn btn--danger" id="revoke-mandate-button" type="button">REVOKE MANDATE</button>'
        : "",
      consent.can_execute
        ? '<button class="btn btn--secondary" id="attempt-execution-button" type="button">ATTEMPT EXECUTION</button>'
        : "",
    ].join("");
    return `
      <div class="panel">
        <div class="panel__head">
          <h3 class="panel__title">Execution</h3>
          <p class="panel__meta">${escapeHtml(execution.environment)}</p>
        </div>
        <div class="consent-state consent-state--${escapeHtml(
          String(consent.status || "missing").toLowerCase(),
        )}">
          <div class="consent-state__head">
            <div><span>CONSENT STATE</span><strong>${escapeHtml(
              consent.status || "MISSING",
            )}</strong></div>
            <code>Mandate v${escapeHtml(consent.mandate_version ?? "n/a")}</code>
          </div>
          <div class="consent-state__outcome">
            <span>${rejected ? "REJECTED BEFORE NETWORK" : "AUTHORIZED"}</span>
            <strong>${rejected ? escapeHtml(humanize(execution.reason)) : "CAPABILITY ISSUED"}</strong>
            <small>RAZORPAY CALLS ${escapeHtml(execution.razorpay_calls ?? 0)}</small>
          </div>
          <p>${escapeHtml(consent.teaching)}</p>
          <small class="consent-authority">Authority: ${escapeHtml(consent.authority)}</small>
          <div class="consent-actions">${controls}</div>
        </div>
        <div class="capability">
          <p class="capability__head">
            <span>SIGNED CAPABILITY</span>
            <strong>CRYPTOGRAPHICALLY VALID</strong>
          </p>
          <ul class="bindings">${bindingRows}</ul>
        </div>
        <p class="network-line">
          <span>External network calls</span><strong>${escapeHtml(
            execution.external_network_calls ?? 0,
          )}</strong>
        </p>
      </div>
    `;
  }
  if (!execution || execution.status !== "ORDER_CREATED") {
    const decisionClass = execution?.reason?.toLowerCase().includes("review") ? "review" : "block";
    return `
      <div class="panel">
        <div class="panel__head">
          <h3 class="panel__title">Execution</h3>
          <p class="panel__meta">Stopped at MandateGuard</p>
        </div>
        <div class="execstop execstop--${decisionClass}">
          <div class="execstop__count">
            <span>RAZORPAY CALLS</span>
            <strong>${escapeHtml(execution?.razorpay_calls ?? 0)}</strong>
          </div>
          <div>
            <h4>Execution stopped at MandateGuard.</h4>
            <p>${escapeHtml(execution?.reason || "Execution did not run.")}</p>
          </div>
        </div>
        <p class="network-line">
          <span>External network calls</span><strong>${escapeHtml(
            execution?.external_network_calls ?? 0,
          )}</strong>
        </p>
      </div>
    `;
  }
  const isOfflineDemo = execution.environment === "OFFLINE_DEMO_TEST_DOUBLE";
  const isLiveTest = execution.environment === "RAZORPAY_TEST_MODE";
  const environmentLabel = isOfflineDemo
    ? "OFFLINE DEMO REPLAY"
    : isLiveTest
      ? "RAZORPAY TEST MODE"
      : "EXECUTION ENVIRONMENT";
  const environmentDetail = isOfflineDemo ? "NO LIVE RAZORPAY REQUEST" : execution.environment;
  const resultLabel = isOfflineDemo ? "SIMULATED EXECUTION RECEIPT" : "PAYMENT ORDER";
  const resultStatus = isOfflineDemo ? "LOCAL RECEIPT CREATED" : "ORDER CREATED";
  const liveEvidence = isOfflineDemo
    ? `
      <p class="live-evidence-note">
        Live Razorpay Test Mode execution independently verified in
        <a href="${liveRazorpayEvidenceUrl}" target="_blank" rel="noreferrer noopener">preserved engineering evidence</a>.
      </p>`
    : "";
  const capability = execution.capability || {};
  const order = execution.order || {};
  const bindingRows = [
    ["Signature", capability.signature_verified],
    ["Transaction bound", capability.transaction_bound],
    ["Request bound", capability.request_bound],
    ["Merchant bound", capability.merchant_bound],
    ["Expiry valid", capability.expiry_valid],
    ["Single-use", capability.single_use],
  ]
    .map(
      ([label, value]) => `
        <li><span>${escapeHtml(label)}</span><strong data-valid="${value ? "true" : "false"}">${yesNo(
          value,
        )}</strong></li>`,
    )
    .join("");
  const replay = execution.replay
    ? `
      <div class="replay">
        <div>
          <p class="microlabel">REPLAY SECURITY RESULT</p>
          <strong>${escapeHtml(String(execution.replay.status).replaceAll("_", " "))}</strong>
          <span>${escapeHtml(humanize(execution.replay.reason))}</span>
        </div>
        <p>Razorpay additional calls: ${escapeHtml(execution.replay.razorpay_additional_calls)}</p>
      </div>`
    : `<button class="btn btn--secondary" id="replay-button" type="button">TEST CAPABILITY REPLAY</button>`;
  return `
    <div class="panel">
      <div class="panel__head">
        <h3 class="panel__title">Execution</h3>
        <p class="panel__meta">${environmentLabel}</p>
      </div>
      <p class="envline">
        <span>${environmentLabel}</span>
        <code>${escapeHtml(environmentDetail)}</code>
      </p>
      <div class="capability">
        <p class="capability__head">
          <span>SIGNED CAPABILITY</span>
          <strong>BOUND AND VERIFIED</strong>
        </p>
        <ul class="bindings">${bindingRows}</ul>
      </div>
      <div class="order">
        <p class="order__head">
          <span>${resultLabel}</span><strong>${resultStatus}</strong>
        </p>
        <dl class="datarows">
          <div><dt>Order ID</dt><dd><code title="${escapeHtml(order.order_id)}">${escapeHtml(
            shortId(order.order_id),
          )}</code></dd></div>
          <div><dt>Amount</dt><dd>${money(order.amount, order.currency)}</dd></div>
          <div><dt>Currency</dt><dd>${escapeHtml(order.currency)}</dd></div>
          <div><dt>Receipt</dt><dd><code title="${escapeHtml(order.receipt)}">${escapeHtml(
            shortId(order.receipt),
          )}</code></dd></div>
          <div><dt>Status</dt><dd>${escapeHtml(String(order.status || "").toUpperCase())}</dd></div>
        </dl>
      </div>
      <p class="callrow">
        <span>Adapter calls <strong>${escapeHtml(execution.razorpay_calls)}</strong></span>
        <span>External calls <strong>${escapeHtml(execution.external_network_calls)}</strong></span>
      </p>
      ${liveEvidence}
      ${replay}
    </div>
  `;
}

/* ------------------------------------------------------------------ */
/* Attack lab, evidence provenance, evaluation                         */
/* ------------------------------------------------------------------ */

export function renderAttackLab(surfaces = ATTACK_SURFACES) {
  return surfaces
    .map(
      (item) => `
        <article class="attack" id="attack-${escapeHtml(item.id)}">
          <div class="attack__surface">
            <p class="microlabel">ATTACK SURFACE</p>
            <h3 class="attack__name">${escapeHtml(item.surface)}</h3>
            <p class="attack__detail">${escapeHtml(item.detail)}</p>
          </div>
          <div class="attack__signal">
            <p class="microlabel">OBSERVED SIGNAL</p>
            <p class="attack__signaltext">${escapeHtml(item.signal)}</p>
          </div>
          <div class="attack__control">
            <p class="microlabel">CONTROL HIT</p>
            <p class="attack__controltext">${escapeHtml(item.control)}</p>
          </div>
          <div class="attack__treatment">
            <p class="microlabel">DECISION</p>
            <p class="treatment" data-treatment="${escapeHtml(item.decision)}">${escapeHtml(
              item.decision,
            )}</p>
          </div>
          <div class="attack__payment">
            <p class="microlabel">PAYMENT REACHED?</p>
            <p class="paymentflag" data-reached="${escapeHtml(item.paymentReached)}">${escapeHtml(
              item.paymentReached,
            )}</p>
            <code class="attack__evidence">${escapeHtml(item.evidence)}</code>
          </div>
        </article>`,
    )
    .join("");
}

export function renderProvenance(result) {
  if (!result) {
    return `
      <div class="provenance__empty">
        <h3>No run has been observed yet.</h3>
        <p>Run a scenario in OBSERVE. The evidence that decided that run appears here with its
           source, trust tier, scope, and hash commitment.</p>
      </div>`;
  }
  const evidence = result.evidence || {};
  const cards = evidence.cards || [];
  const items = cards.length
    ? cards
        .map(
          (card) => `
          <li class="prov" data-acquisition="${escapeHtml(card.acquisition || "")}">
            <div class="prov__spine" aria-hidden="true"></div>
            <div class="prov__body">
              <p class="prov__top">
                <span class="tierchip">TRUSTED MERCHANT EVIDENCE</span>
                <code>${escapeHtml(card.evidence_id)}</code>
              </p>
              <p class="prov__claim">${escapeHtml(card.text)}</p>
              <dl class="prov__meta">
                <div><dt>Source</dt><dd>${escapeHtml(card.source_kind)}</dd></div>
                <div><dt>Merchant</dt><dd><code>${escapeHtml(card.merchant_id)}</code></dd></div>
                <div><dt>Scope</dt><dd>${escapeHtml(
                  card.sku ? `SKU ${card.sku}` : "MERCHANT WIDE",
                )}</dd></div>
                <div><dt>Effective state</dt><dd>${escapeHtml(
                  card.acquisition === "BOUNDED_TRUSTED_ACQUISITION"
                    ? "ACQUIRED DURING RECOVERY"
                    : "RESOLVED AT RETRIEVAL",
                )}</dd></div>
                <div><dt>Retrieval score</dt><dd><code>${escapeHtml(
                  displayScore(card.retrieval_score),
                )}</code></dd></div>
              </dl>
            </div>
          </li>`,
        )
        .join("")
    : '<li class="prov prov--empty"><div class="prov__body"><p class="prov__claim">No trusted merchant evidence was resolved for this run.</p></div></li>';
  const constraints = (result.authorization?.semantic?.checks || [])
    .map(
      (check) => `
        <li class="link" data-status="${escapeHtml(check.status)}">
          <p class="link__family"><code>${escapeHtml(check.family)}</code></p>
          <p class="link__text">${escapeHtml(check.constraint)}</p>
          ${statusBadge(check.status)}
          <p class="link__reason">${escapeHtml(check.reason || "Not evaluated.")}</p>
        </li>`,
    )
    .join("");
  return `
    <div class="provenance__hash">
      <p class="microlabel">EVIDENCE SET COMMITMENT</p>
      <code class="hash">${escapeHtml(evidence.evidence_set_sha256 || "not recorded")}</code>
      <p class="provenance__hashnote">
        Every constraint below was evaluated against exactly this canonical evidence set.
      </p>
    </div>
    <div class="provenance__grid">
      <section aria-labelledby="prov-items">
        <h3 class="provenance__heading" id="prov-items">EVIDENCE THAT REACHED THE VERIFIER</h3>
        <ul class="provlist">${items}</ul>
      </section>
      <section aria-labelledby="prov-links">
        <h3 class="provenance__heading" id="prov-links">CONSTRAINTS IT DECIDED</h3>
        <ul class="linklist">${constraints || '<li class="link">No semantic constraints were evaluated.</li>'}</ul>
      </section>
    </div>
    <div class="provenance__untrusted">
      <p class="microlabel">EXCLUDED FROM THE EVIDENCE SET</p>
      <p><strong>BUYER-PROVIDED TEXT</strong> is recorded for the audit trail and is never
         resolvable as evidence.</p>
      <p class="provenance__buyertext">${escapeHtml(result.evidence?.buyer_text?.text)}</p>
    </div>
  `;
}

export function renderBoundedScale(config) {
  const resolve = config?.resolve || {};
  const rows = [
    ["Evidence retrieval", `top-k ${resolve.top_k ?? 5}`],
    ["Recovery", `max ${resolve.max_acquisition_rounds ?? 2} rounds`],
    ["New trusted evidence", `max ${resolve.max_new_evidence_items ?? 4} items`],
    ["Execution capability", "single-use"],
    ["External payment call", "only after final ALLOW"],
  ]
    .map(
      ([label, value]) =>
        `<div class="bounded__item"><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`,
    )
    .join("");
  return `<dl class="bounded__list">${rows}</dl>`;
}

export function renderMeasuredEvidence(evaluation = EVALUATION_EVIDENCE, totals = TEST_TOTALS) {
  const outcomes = [
    ["REVIEW to ALLOW", evaluation.allow],
    ["REVIEW to BLOCK", evaluation.block],
    ["REVIEW to REVIEW", evaluation.review],
    ["Safety violations", evaluation.safetyViolations],
  ]
    .map(
      ([label, value]) => `
        <div class="measured__item">
          <dt>${escapeHtml(label)}</dt>
          <dd class="measured__value"${figureAttr(value, { animate: true })}>${escapeHtml(value)}</dd>
        </div>`,
    )
    .join("");
  return `
    <p class="measured__lede">
      <strong${figureAttr(evaluation.cases, { animate: true })}>${escapeHtml(evaluation.cases)}</strong>
      independent synthetic recovery cases.
    </p>
    <dl class="measured__grid">${outcomes}</dl>
    <p class="measured__value-moved">
      ${money(evaluation.valueMoved, evaluation.currency)} of frozen synthetic transaction value
      moved from REVIEW to executable ALLOW.
    </p>
    <p class="measured__caveat">
      Synthetic engineering scenarios. Not merchant traffic, not revenue, not conversion lift,
      and not evidence of generalization. This is not a production throughput claim.
    </p>
    <p class="measured__negative">${escapeHtml(evaluation.negativeResult)}</p>
    <dl class="measured__source">
      <div><dt>Source</dt><dd><code>${escapeHtml(evaluation.source)}</code></dd></div>
      <div><dt>summary.json SHA-256</dt><dd><code class="hash">${escapeHtml(
        evaluation.summarySha256,
      )}</code></dd></div>
    </dl>
  `;
}

/* ------------------------------------------------------------------ */
/* Measured evidence, kept in four separate kinds                      */
/* ------------------------------------------------------------------ */

function figureRow(label, value, note) {
  return `
    <div class="measured__item">
      <dt>${escapeHtml(label)}</dt>
      <dd class="measured__value"${figureAttr(value, { animate: true })}>${escapeHtml(
        value ?? "n/a",
      )}</dd>
      ${note ? `<dd class="measured__note">${escapeHtml(note)}</dd>` : ""}
    </div>`;
}

function megabytes(bytes) {
  const value = Number(bytes);
  return Number.isFinite(value) ? `${(value / 1048576).toFixed(1)} MB` : "n/a";
}

/** SYSTEM SCALE. Catalog and index size, cold start, and query latency. */
export function renderSystemScale(scale) {
  if (!scale || !scale.available) {
    return `<p class="measured__unavailable">${escapeHtml(
      scale?.reason || "No scale benchmark has been recorded for this build.",
    )}</p>`;
  }
  const rows = [
    // "Catalog listings", not "SKUs". 17,702 rows are searchable; only the
    // separately registered merchant products have a merchant SKU behind them.
    figureRow(
      "Catalog listings",
      Number(scale.catalog_listings || 0).toLocaleString("en-IN"),
      "historical marketplace listings, searchable",
    ),
    figureRow("Categories", scale.categories),
    figureRow("Index size", megabytes(scale.index_bytes), "lexical + embedding, on disk"),
    figureRow("Catalog size", megabytes(scale.catalog_bytes), "compressed, committed"),
    figureRow("Cold load", `${Number(scale.cold_load_seconds || 0).toFixed(2)} s`),
    figureRow("Resident memory", `${scale.resident_memory_mb ?? "n/a"} MB`),
    figureRow("Queries executed", Number(scale.queries_executed || 0).toLocaleString("en-IN")),
  ].join("");
  // Two different measurements, two different labels. Retrieval is the
  // BM25 + filters + duplicate-suppression call. The request is that plus intent
  // parsing, classification, mismatch, anomaly and transactability per candidate.
  const retrievalRows = [
    figureRow("Retrieval P50", `${scale.retrieval_p50_ms ?? "n/a"} ms`),
    figureRow("Retrieval P95", `${scale.retrieval_p95_ms ?? "n/a"} ms`),
    figureRow("Retrieval P99", `${scale.retrieval_p99_ms ?? "n/a"} ms`),
    figureRow(
      "Retrieval / second",
      scale.retrieval_queries_per_second,
      "single process, no concurrency",
    ),
  ].join("");
  const requestRows = [
    figureRow("Request P50", `${scale.request_p50_ms ?? "n/a"} ms`),
    figureRow("Request P95", `${scale.request_p95_ms ?? "n/a"} ms`),
    figureRow("Request P99", `${scale.request_p99_ms ?? "n/a"} ms`),
    figureRow(
      "Requests / second",
      scale.queries_per_second,
      "single process, no concurrency",
    ),
  ].join("");
  return `
    <dl class="measured__grid measured__grid--wide">${rows}</dl>
    <div class="quality">
      <section class="quality__col" aria-labelledby="scale-retrieval-latency">
        <h4 class="quality__heading" id="scale-retrieval-latency">Retrieval latency</h4>
        <dl class="measured__grid">${retrievalRows}</dl>
        <p class="quality__split">The retrieval call alone.</p>
      </section>
      <section class="quality__col" aria-labelledby="scale-request-latency">
        <h4 class="quality__heading" id="scale-request-latency">Full discovery request</h4>
        <dl class="measured__grid">${requestRows}</dl>
        <p class="quality__split">
          Retrieval plus intent parsing, classification, mismatch, anomaly and
          transactability for every candidate.
        </p>
      </section>
    </div>
    <p class="measured__caveat">
      Single process · one machine · no concurrency · no network.
      ${escapeHtml(scale.latency_note || "")}
    </p>
    <p class="measured__caveat">${escapeHtml(scale.caveat || "")}</p>
    <dl class="measured__source">
      <div><dt>Source</dt><dd><code>${escapeHtml(scale.source)}</code></dd></div>
      <div><dt>Measured on</dt><dd><code>${escapeHtml(
        scale.environment?.platform || "n/a",
      )}</code></dd></div>
    </dl>`;
}

/** MODEL QUALITY. Never merged with authorization evidence. */
export function renderModelQuality(quality) {
  if (!quality || !quality.available) {
    return `<p class="measured__unavailable">${escapeHtml(
      quality?.reason || "No model evaluation has been recorded for this build.",
    )}</p>`;
  }
  const classifier = quality.classifier || {};
  const retrieval = quality.retrieval || {};
  const negatives = (quality.negative_results || [])
    .map(
      (item) => `
        <li class="negative">
          <p class="negative__finding">${escapeHtml(item.finding)}</p>
          <p class="negative__detail">${escapeHtml(item.detail)}</p>
        </li>`,
    )
    .join("");
  return `
    <div class="quality">
      <section class="quality__col" aria-labelledby="quality-classifier">
        <h4 class="quality__heading" id="quality-classifier">Category classifier</h4>
        <dl class="measured__grid">
          ${figureRow("Macro F1", classifier.macro_f1)}
          ${figureRow("Weighted F1", classifier.weighted_f1)}
          ${figureRow("Accuracy", classifier.accuracy)}
          ${figureRow("Top-2 accuracy", classifier.top_2_accuracy)}
        </dl>
        <p class="quality__split">
          Grouped product-family hold-out ·
          ${escapeHtml(classifier.classes)} classes ·
          ${escapeHtml(Number(classifier.family_groups || 0).toLocaleString("en-IN"))}
          product families ·
          train ${escapeHtml(Number(classifier.train || 0).toLocaleString("en-IN"))} ·
          validation ${escapeHtml(Number(classifier.validation || 0).toLocaleString("en-IN"))} ·
          test ${escapeHtml(Number(classifier.test || 0).toLocaleString("en-IN"))},
          frozen before the test set was scored.
        </p>
        <p class="quality__split">
          Whole product families sit on one side of the split, so no test listing has a
          near-identical twin in training. The earlier row-wise split, which did not
          guarantee that, scored macro F1
          ${escapeHtml(classifier.row_wise?.macro_f1 ?? "n/a")} ·
          accuracy ${escapeHtml(classifier.row_wise?.accuracy ?? "n/a")}. Both are
          reported; the grouped number is the one quoted.
        </p>
        <p class="authority-alert">Advisory. This model cannot allow a payment.</p>
      </section>
      <section class="quality__col" aria-labelledby="quality-retrieval">
        <h4 class="quality__heading" id="quality-retrieval">Catalog retrieval</h4>
        <dl class="measured__grid">
          ${figureRow("Recall@10", retrieval.recall_at_10)}
          ${figureRow("Recall@5", retrieval.recall_at_5)}
          ${figureRow("MRR", retrieval.mrr)}
          ${figureRow(
            "DistinctTitle@8",
            retrieval.distinct_title_at_8,
            "unique display titles among the 8 shown",
          )}
        </dl>
        <p class="quality__split">
          ${escapeHtml(retrieval.queries)} hand-authored queries, a fixed evaluation set
          committed with this engineering milestone. Its digest is recorded in the report
          (<code>query_set_sha256</code>), so a later change to the questions is visible.
          The set and these results landed in one commit, so this is not an independently
          preregistered evaluation and is not described as one.
          Configuration: <code>${escapeHtml(retrieval.configuration || "n/a")}</code>.
        </p>
        <p class="quality__split">${escapeHtml(retrieval.method || "")}</p>
        <p class="authority-alert">${escapeHtml(quality.boundary || "")}</p>
      </section>
    </div>
    <div class="negatives">
      <p class="microlabel">WHAT WE BUILT, MEASURED, AND DID NOT SHIP</p>
      <ul class="negatives__list">${negatives || "<li>No negative results recorded.</li>"}</ul>
    </div>`;
}

/** ENGINEERING QUALITY. Test counts, said plainly as test counts. */
export function renderEngineeringQuality(totals = TEST_TOTALS) {
  return `
    <dl class="measured__grid">
      ${figureRow("Python tests", totals.python)}
      ${figureRow("UI tests", totals.ui)}
    </dl>
    <p class="measured__caveat">
      A passing test suite is evidence that the code does what its authors intended. It is not a
      measurement of scale, of model quality, or of authorization correctness, and it is reported
      here on its own so it cannot be mistaken for any of them.
    </p>`;
}

/** Where the discovery catalog came from, and under what licence. */
export function renderCatalogProvenance(discovery) {
  if (!discovery?.available) {
    return `<p class="measured__unavailable">${escapeHtml(
      discovery?.reason || "No discovery catalog is built into this deployment.",
    )}</p>`;
  }
  const provenance = discovery.provenance || {};
  const catalog = discovery.catalog || {};
  return `
    <div class="catprov">
      <p class="microlabel">DISCOVERY CATALOG PROVENANCE</p>
      <h3 class="catprov__name">${escapeHtml(provenance.display_name || "Imported catalog")}</h3>
      <dl class="datarows">
        <div><dt>Publisher</dt><dd>${escapeHtml(provenance.publisher || "unknown")}</dd></div>
        <div><dt>Licence</dt><dd>${escapeHtml(provenance.licence || "unknown")}</dd></div>
        <div><dt>Listings</dt><dd><code>${escapeHtml(
          Number(catalog.listings || 0).toLocaleString("en-IN"),
        )}</code></dd></div>
        <div><dt>Catalog SHA-256</dt><dd><code class="hash">${escapeHtml(
          provenance.catalog_sha256 || "not recorded",
        )}</code></dd></div>
      </dl>
      <p class="catprov__attribution">${escapeHtml(provenance.attribution || "")}</p>
      <p class="catprov__boundary">
        <strong>${escapeHtml(provenance.trust_tier || "DISCOVERY_LISTING")}.</strong>
        ${escapeHtml(provenance.trust_note || "")}
      </p>
    </div>`;
}

export function renderResearch(research) {
  return `
    <p class="research__status">
      <span>EXPERIMENTAL</span>
      <strong>${escapeHtml(research?.authorization_use)}</strong>
    </p>
    <p class="research__finding">${escapeHtml(research?.finding)}</p>
    <details class="disclosure disclosure--inline">
      <summary class="disclosure__summary"><span>Research scope and source</span></summary>
      <p>${escapeHtml(research?.scope)}</p>
      <code class="sourcepath">${escapeHtml(research?.source)}</code>
    </details>
  `;
}

export function renderRecovery(items) {
  const recoveryStatus = (trigger) => {
    if (trigger === "No trusted evidence retrieved") return "REVIEW";
    if (trigger === "INT-3 artifact serializer failure") return "RECOVERED";
    if (trigger === "Corrupted semantic cache" || trigger === "Capability replay") return "REJECTED";
    return "SAFE STOP";
  };
  return `
    <ul class="recoverylist">
      ${(items || [])
        .map((item) => {
          const callCount = Object.hasOwn(item, "additional_external_calls")
            ? `Additional external calls: ${escapeHtml(item.additional_external_calls)}`
            : Object.hasOwn(item, "external_calls")
              ? `External execution calls: ${escapeHtml(item.external_calls)}`
              : "";
          const status = recoveryStatus(item.trigger);
          const noUnsafeEffect =
            item.retried_failed_request === false
              ? "The failed stochastic request was not retried."
              : callCount || "No unsafe execution side effect was recorded.";
          return `
            <li class="recovery">
              <details class="disclosure disclosure--inline">
                <summary class="disclosure__summary">
                  <span>${escapeHtml(item.trigger)}</span>
                  <strong data-recovery-status="${escapeHtml(status)}">${escapeHtml(status)}</strong>
                </summary>
                <dl class="datarows">
                  <div><dt>What failed</dt><dd>${escapeHtml(item.trigger)}</dd></div>
                  <div><dt>System response</dt><dd>${escapeHtml(item.outcome)}</dd></div>
                  <div><dt>Why no unsafe side effect occurred</dt><dd>${noUnsafeEffect}</dd></div>
                  <div><dt>Source</dt><dd><code>${escapeHtml(item.source)}</code></dd></div>
                </dl>
              </details>
            </li>`;
        })
        .join("")}
    </ul>
  `;
}

function renderAudit(snapshot) {
  const events = (snapshot.audit || [])
    .map(
      (item) => `
        <li>
          <span class="auditlist__seq">${escapeHtml(String(item.sequence).padStart(2, "0"))}</span>
          <div><strong>${escapeHtml(humanize(item.event))}</strong><small>${escapeHtml(
            item.recorded_at,
          )}</small></div>
        </li>`,
    )
    .join("");
  return `
    <ol class="auditlist">${events}</ol>
    <details class="disclosure disclosure--inline">
      <summary class="disclosure__summary"><span>Raw JSON</span></summary>
      <pre class="rawjson">${escapeHtml(JSON.stringify(snapshot, null, 2))}</pre>
    </details>
  `;
}

/* ------------------------------------------------------------------ */
/* Motion                                                              */
/* ------------------------------------------------------------------ */

const EASE = "cubic-bezier(.16,1,.3,1)";

function prefersReducedMotion() {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/**
 * Run one entrance animation.
 *
 * `fill` defaults to "none" and callers animating readable content must animate
 * `transform` only. An animation that faded content in from `opacity: 0` would
 * leave that content invisible for as long as its clock is stalled, and the
 * decision word is the last thing on this page that may ever be unreadable.
 */
function motion(element, keyframes, options) {
  if (!element || prefersReducedMotion() || typeof element.animate !== "function") return null;
  return element.animate(keyframes, { easing: EASE, fill: "none", ...options });
}

/** Play a figure's entrance once. The text is already correct and is never rewritten. */
function revealFigure(element) {
  if (element.dataset.figureRevealed === "true") return;
  element.dataset.figureRevealed = "true";
  motion(element, [{ transform: "translateY(10px)" }, { transform: "translateY(0)" }], {
    duration: 340,
  });
}

function init() {
  const $ = (selector) => document.querySelector(selector);
  const elements = {
    system: $("#system-state"),
    presets: $("#preset-row"),
    intent: $("#purchase-intent"),
    run: $("#run-button"),
    modeNote: $("#mode-note"),
    liveLabel: $("#live-mode-label"),
    journey: $("#journey"),
    runState: $("#run-state"),
    discoveryRegion: $("#discovery-region"),
    discoveryMeta: $("#discovery-meta"),
    discoveryResults: $("#discovery-results"),
    discoveryNote: $("#discovery-note"),
    mandatePanel: $("#mandate-panel"),
    resultRegion: $("#result-region"),
    verdict: $("#verdict"),
    story: $("#story"),
    resolveRegion: $("#resolve-region"),
    reviewRecovery: $("#review-recovery-panel"),
    transactability: $("#transactability-panel"),
    buyer: $("#buyer-panel"),
    evidence: $("#evidence-panel"),
    evidenceCount: $("#evidence-count"),
    authorization: $("#authorization-panel"),
    execution: $("#execution-panel"),
    provenance: $("#provenance"),
    catalogProvenance: $("#catalog-provenance"),
    attackGrid: $("#attack-grid"),
    bounded: $("#bounded-panel"),
    measured: $("#measured-panel"),
    systemScale: $("#system-scale-panel"),
    modelQuality: $("#model-quality-panel"),
    engineering: $("#engineering-panel"),
    research: $("#research-panel"),
    recovery: $("#recovery-panel"),
    auditSection: $("#audit-section"),
    audit: $("#audit-panel"),
    error: $("#form-error"),
    console: $("#console"),
  };
  const searchLock = new SubmissionLock();
  const submitLock = new SubmissionLock();
  const replayLock = new SubmissionLock();
  const recoveryLock = new SubmissionLock();
  const mandateLock = new SubmissionLock();
  let config;
  let currentRunId = null;
  let latestResult = null;
  let latestDiscovery = null;
  let latestSelection = null;
  let previousConsent = null;

  /* ---------------- counters ---------------- */
  const figureObserver =
    typeof IntersectionObserver === "function"
      ? new IntersectionObserver(
          (entries) => {
            entries.forEach((entry) => {
              if (!entry.isIntersecting) return;
              revealFigure(entry.target);
              figureObserver.unobserve(entry.target);
            });
          },
          { threshold: 0.4 },
        )
      : null;

  const observeFigures = (root) => {
    (root || document)
      .querySelectorAll('[data-figure="animate"]')
      .forEach((element) => figureObserver?.observe(element));
  };

  /* ---------------- views ---------------- */
  const tabs = [...document.querySelectorAll(".navtab")];
  const showView = (name, { focus = false } = {}) => {
    const known = tabs.some((tab) => tab.dataset.view === name);
    const target = known ? name : "observe";
    tabs.forEach((tab) => {
      const active = tab.dataset.view === target;
      tab.setAttribute("aria-selected", active ? "true" : "false");
      tab.tabIndex = active ? 0 : -1;
      const panel = document.querySelector(`#view-${tab.dataset.view}`);
      if (panel) panel.hidden = !active;
      if (active) {
        observeFigures(panel);
        if (focus) tab.focus();
      }
    });
    if (target === "evidence") {
      elements.catalogProvenance.innerHTML = renderCatalogProvenance(config?.discovery);
      elements.provenance.innerHTML = renderProvenance(latestResult);
    }
  };

  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => {
      showView(tab.dataset.view);
      window.history.replaceState(null, "", `#${tab.dataset.view}`);
    });
    tab.addEventListener("keydown", (event) => {
      const offset = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
      if (!offset) return;
      event.preventDefault();
      const next = tabs[(index + offset + tabs.length) % tabs.length];
      showView(next.dataset.view, { focus: true });
      window.history.replaceState(null, "", `#${next.dataset.view}`);
    });
  });

  /* ---------------- the nine-stage journey ---------------- */
  const paintJourney = (snapshot) => {
    const stages = buildJourney({
      discovery: latestDiscovery,
      selection: latestSelection,
      snapshot,
    });
    elements.journey.innerHTML = renderJourney(stages);
    const decision = snapshot?.result?.decision || latestSelection?.status || "waiting";
    elements.journey.dataset.decision = String(decision).toLowerCase().replaceAll(" ", "-");
    elements.journey.style.setProperty("--journey-fill", String(journeyProgress(stages)));
    [...elements.journey.querySelectorAll(".journey__step")].forEach((step, index) => {
      if (step.dataset.state === "waiting") return;
      motion(step, [{ transform: "translateY(5px)" }, { transform: "translateY(0)" }], {
        duration: 220,
        delay: Math.min(index * 40, 300),
      });
    });
  };

  /* ---------------- form state ---------------- */
  const selectedMode = () =>
    document.querySelector('input[name="mode"]:checked')?.value || "offline";

  const setBusy = (busy, label) => {
    document.body.classList.toggle("is-running", busy);
    elements.run.disabled = busy;
    elements.run.textContent = busy ? label || "WORKING" : "SEARCH THE CATALOG";
    elements.intent.readOnly = busy;
    document
      .querySelectorAll('input[name="mode"], .presetbtn')
      .forEach((item) => (item.disabled = busy || item.dataset.unavailable === "true"));
  };

  const showError = (message) => {
    elements.error.textContent = message;
    elements.error.hidden = !message;
  };

  /* ---------------- rendering ---------------- */
  const renderSnapshot = (snapshot) => {
    elements.runState.textContent = snapshot.state;
    paintJourney(snapshot);
    if (snapshot.state === "ERROR") {
      showError(snapshot.error?.message || "The run stopped safely.");
      return;
    }
    if (!snapshot.result) return;
    const result = snapshot.result;
    latestResult = result;
    elements.resultRegion.hidden = false;
    elements.resultRegion.dataset.decision = result.decision;

    const nextConsent = result.execution?.consent?.status || null;
    elements.verdict.innerHTML = renderDecisionBanner(result);
    elements.story.innerHTML = renderStory(result);
    elements.resolveRegion.hidden = !result.recovery && !result.transactability;
    elements.reviewRecovery.innerHTML = renderReviewRecovery(result.recovery, result.execution);
    elements.transactability.innerHTML = renderTransactability(result.transactability);
    elements.reviewRecovery.hidden = !elements.reviewRecovery.innerHTML.trim();
    elements.transactability.hidden = !elements.transactability.innerHTML.trim();
    elements.buyer.innerHTML = renderBuyerPanel(result.buyer);
    elements.evidence.innerHTML = renderEvidencePanel(result.evidence);
    elements.evidenceCount.textContent = `${result.evidence.trusted_evidence_count} EVIDENCE ITEMS`;
    elements.authorization.innerHTML = renderAuthorizationPanel(result.authorization);
    elements.execution.innerHTML = renderExecutionPanel(result.execution);
    elements.auditSection.hidden = false;
    elements.audit.innerHTML = renderAudit(snapshot);
    if (!document.querySelector("#view-evidence").hidden) {
      elements.provenance.innerHTML = renderProvenance(result);
    }
    observeFigures(elements.verdict);
    observeFigures(elements.story);

    motion(
      elements.verdict.querySelector(".decision-banner__word"),
      [{ transform: "translateY(12px)" }, { transform: "translateY(0)" }],
      { duration: 320 },
    );

    if (previousConsent === "ACTIVE" && nextConsent && nextConsent !== "ACTIVE") {
      const row = elements.story.querySelector('.consentstrip__row[data-consent="true"]');
      motion(row, [{ transform: "translateX(-10px)" }, { transform: "translateX(0)" }], {
        duration: 300,
      });
    }
    previousConsent = nextConsent;

    elements.evidence
      .querySelectorAll(".evidence-card[data-acquisition='BOUNDED_TRUSTED_ACQUISITION']")
      .forEach((node) =>
        motion(node, [{ transform: "translateY(16px)" }, { transform: "translateY(0)" }], {
          duration: 340,
        }),
      );

    const bind = (selector, handler) => {
      const button = document.querySelector(selector);
      if (button) button.addEventListener("click", handler);
    };
    bind("#replay-button", runReplay);
    bind("#acquire-evidence-button", runRecovery);
    bind("#revoke-mandate-button", runRevocation);
    bind("#attempt-execution-button", runDeferredExecution);
  };

  /* ---------------- network ---------------- */
  const createRequestId = () => {
    if (globalThis.crypto?.randomUUID) return `web_${globalThis.crypto.randomUUID()}`;
    return `web_${Date.now()}_${Math.random().toString(16).slice(2)}`;
  };

  const fetchJson = async (url, options) => {
    const response = await fetch(url, options);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload?.error?.message || "The request failed safely.");
    return payload;
  };

  const postJson = (url, body) =>
    fetchJson(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

  const pollRun = async (initial) => {
    let snapshot = initial;
    renderSnapshot(snapshot);
    while (!terminalStates.has(snapshot.state)) {
      await new Promise((resolve) => setTimeout(resolve, 180));
      snapshot = await fetchJson(`/api/runs/${encodeURIComponent(snapshot.run_id)}`);
      renderSnapshot(snapshot);
    }
    return snapshot;
  };

  const postAction = async (action, buttonId, busyLabel, lock) => {
    if (!currentRunId || !lock.acquire()) return;
    const button = document.querySelector(buttonId);
    if (button) {
      button.disabled = true;
      button.textContent = busyLabel;
    }
    showError("");
    try {
      const snapshot = await fetchJson(`/api/runs/${encodeURIComponent(currentRunId)}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      renderSnapshot(snapshot);
    } catch (error) {
      showError(error.message);
    } finally {
      lock.release();
    }
  };

  async function runReplay() {
    await postAction("replay", "#replay-button", "TESTING REPLAY", replayLock);
  }
  async function runRecovery() {
    await postAction(
      "recover",
      "#acquire-evidence-button",
      "ACQUIRING TRUSTED EVIDENCE",
      recoveryLock,
    );
  }
  async function runRevocation() {
    await postAction("revoke", "#revoke-mandate-button", "REVOKING MANDATE", mandateLock);
  }
  async function runDeferredExecution() {
    await postAction(
      "execute",
      "#attempt-execution-button",
      "CHECKING CURRENT CONSENT",
      mandateLock,
    );
  }

  /* ---------------- stage 1-5: discovery ---------------- */
  const paintDiscovery = (discovery) => {
    latestDiscovery = discovery;
    latestSelection = null;
    elements.discoveryRegion.hidden = false;
    elements.discoveryMeta.textContent = discoverySummaryLine(discovery);
    elements.mandatePanel.innerHTML = renderMandatePanel(discovery);
    elements.discoveryResults.innerHTML = renderDiscoveryResults(discovery);
    const suppressed = discovery.retrieval?.duplicates_suppressed ?? 0;
    elements.discoveryNote.textContent = suppressed
      ? `${suppressed} near-duplicate listings were collapsed so the results are ${
          discovery.candidates?.length ?? 0
        } different products, not the same one repeated.`
      : "";
    elements.discoveryResults.querySelectorAll("[data-select]").forEach((button) => {
      button.addEventListener("click", () => selectListing(button.dataset.select));
    });
    paintJourney(null);
  };

  const searchCatalog = async () => {
    if (!searchLock.acquire()) return;
    showError("");
    const intent = elements.intent.value.trim();
    if (!intent) {
      showError("Type what the agent should buy.");
      searchLock.release();
      return;
    }
    setBusy(true, "SEARCHING");
    elements.resultRegion.hidden = true;
    elements.auditSection.hidden = true;
    currentRunId = null;
    latestResult = null;
    previousConsent = null;
    try {
      if (config?.discovery?.available) {
        paintDiscovery(await postJson("/api/discovery/search", { intent, top_k: 6 }));
      } else {
        // Without the large catalog the product still works: the intent goes
        // straight to the authorization controller over the registered catalog.
        elements.discoveryRegion.hidden = true;
        await authorize(intent);
      }
    } catch (error) {
      showError(error.message);
    } finally {
      setBusy(false);
      searchLock.release();
    }
  };

  /* ---------------- stage 3: choose a listing ---------------- */
  const selectListing = async (catalogProductId) => {
    if (!latestDiscovery) return;
    const intent = latestDiscovery.mandate?.raw_text || elements.intent.value.trim();
    showError("");
    let payload;
    try {
      payload = await postJson("/api/discovery/select", {
        intent,
        catalog_product_id: catalogProductId,
      });
    } catch (error) {
      showError(error.message);
      return;
    }
    const candidate = payload.candidate;
    const selection = payload.selection;
    latestSelection = {
      title: candidate.title,
      transactable: selection.transactable,
      status: selection.status,
    };
    elements.discoveryResults.querySelectorAll(".listing").forEach((node) => {
      node.dataset.selected = node.dataset.product === catalogProductId ? "true" : "false";
    });
    paintJourney(null);
    if (selection.transactable && selection.product_identity) {
      // Send only the selected catalog id. The server repeats the search and
      // resolves the exact registered merchant/SKU; browser text is never the
      // authority for product identity.
      await authorize(intent, catalogProductId);
    } else {
      elements.runState.textContent = selection.status;
      elements.resultRegion.hidden = true;
      elements.discoveryNote.textContent = selection.next_step;
    }
  };

  /* ---------------- stages 6-9: the authorization controller ---------------- */
  const authorize = async (intent, selectedCatalogProductId = null) => {
    if (!submitLock.acquire()) return;
    showError("");
    setBusy(true, "VERIFYING");
    try {
      const snapshot = await postJson("/api/runs", {
        intent,
        mode: selectedMode(),
        preset_id: null,
        request_id: createRequestId(),
        selected_catalog_product_id: selectedCatalogProductId,
      });
      currentRunId = snapshot.run_id;
      await pollRun(snapshot);
      elements.resultRegion.scrollIntoView({
        behavior: prefersReducedMotion() ? "auto" : "smooth",
        block: "start",
      });
    } catch (error) {
      showError(error.message);
    } finally {
      setBusy(false);
      submitLock.release();
    }
  };

  /* ---------------- examples ---------------- */
  const renderExamples = () => {
    const examples = config.discovery?.available
      ? config.discovery.presets
      : config.presets;
    elements.presets.innerHTML = (examples || [])
      .map(
        (preset) => `
          <button type="button" class="presetbtn" data-preset="${escapeHtml(preset.id)}"
                  aria-pressed="false">
            ${escapeHtml(preset.label)}
          </button>`,
      )
      .join("");
    elements.presets.querySelectorAll("button").forEach((button) => {
      button.addEventListener("click", () => {
        const preset = (examples || []).find((item) => item.id === button.dataset.preset);
        if (!preset) return;
        elements.intent.value = preset.intent;
        elements.presets.querySelectorAll("button").forEach((item) => {
          const active = item === button;
          item.classList.toggle("is-active", active);
          item.setAttribute("aria-pressed", active ? "true" : "false");
        });
      });
    });
  };

  /* ---------------- wiring ---------------- */
  elements.intent.addEventListener("input", () => {
    elements.presets.querySelectorAll("button").forEach((item) => {
      item.classList.remove("is-active");
      item.setAttribute("aria-pressed", "false");
    });
  });
  elements.run.addEventListener("click", searchCatalog);
  document.querySelectorAll('input[name="mode"]').forEach((input) => {
    input.addEventListener("change", () => {
      if (config && !config.modes.live.available) return;
      elements.modeNote.textContent =
        selectedMode() === "live"
          ? "Uses configured OpenAI pathways and Razorpay Test Mode after explicit submission."
          : "Deterministic local evaluation. External network calls: 0.";
    });
  });

  elements.attackGrid.innerHTML = renderAttackLab();
  paintJourney(null);
  showView((window.location.hash || "#observe").slice(1));
  observeFigures(document);

  /* Arm the reveal only when we can drive it, and disarm unconditionally once
     the reveal window has passed. Copy stays readable even if the transition
     never runs. */
  if (typeof IntersectionObserver === "function" && !prefersReducedMotion()) {
    document.documentElement.dataset.revealArmed = "true";
    const revealObserver = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          entry.target.dataset.revealed = "true";
          revealObserver.unobserve(entry.target);
        });
      },
      { threshold: 0.2 },
    );
    document.querySelectorAll("[data-reveal]").forEach((node) => revealObserver.observe(node));
    setTimeout(() => {
      revealObserver.disconnect();
      delete document.documentElement.dataset.revealArmed;
    }, 1500);
  }

  /* The above-the-fold pipeline count is read from the loaded catalog, not
     typed into the markup. A literal is a number that keeps its value after the
     thing it described has changed. */
  const paintPipelineCount = (payload) => {
    const node = document.querySelector('[data-figure-target="catalog-listings"]');
    if (!node) return;
    const listings = payload?.discovery?.catalog?.listings;
    node.textContent = Number.isFinite(Number(listings))
      ? Number(listings).toLocaleString("en-IN")
      : "—";
  };

  fetchJson("/api/config")
    .then((payload) => {
      config = payload;
      elements.system.dataset.ready = "true";
      elements.system.querySelector(".sysstate__text").textContent = "SYSTEM READY";
      elements.bounded.innerHTML = renderBoundedScale(config);
      elements.measured.innerHTML = renderMeasuredEvidence();
      elements.systemScale.innerHTML = renderSystemScale(config.system_scale);
      elements.modelQuality.innerHTML = renderModelQuality(config.model_quality);
      elements.engineering.innerHTML = renderEngineeringQuality();
      elements.catalogProvenance.innerHTML = renderCatalogProvenance(config.discovery);
      paintPipelineCount(config);
      elements.research.innerHTML = renderResearch(config.research);
      elements.recovery.innerHTML = renderRecovery(config.failure_recovery);
      observeFigures(document.querySelector("#view-evaluation"));
      const unavailableNote = liveModeStatusNote(config.modes.live);
      if (unavailableNote) {
        const liveInput = elements.liveLabel.querySelector("input");
        liveInput.disabled = true;
        liveInput.dataset.unavailable = "true";
        elements.liveLabel.title = unavailableNote;
        elements.liveLabel.querySelector("span").textContent = "LIVE TEST OFF";
        elements.modeNote.textContent = unavailableNote;
      }
      if (!config.discovery?.available) {
        elements.run.textContent = "RUN THE AI BUYER";
        document.querySelector("#console-sub").textContent =
          "The large discovery catalog is not built into this deployment, so the intent goes " +
          "straight to the authorization controller over the registered merchant catalog.";
      }
      renderExamples();
    })
    .catch((error) => {
      elements.system.querySelector(".sysstate__text").textContent = "SYSTEM UNAVAILABLE";
      showError(error.message);
    });
}

if (typeof document !== "undefined") init();
