const terminalStates = new Set(["COMPLETE", "ERROR"]);
const liveRazorpayEvidenceUrl =
  "https://github.com/RaghhavMalani/MandateGuard/blob/b104488ba92fd7b2802b4e053e48e3d398d5f65f/artifacts/engineering/agentic_commerce/int1-razorpay-exec-20260830T074115Z-507323be/RUN.md";

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

export function renderDecisionBanner(result) {
  const decision = result?.decision || "ERROR";
  const resolution = {
    ALLOW: "Mandate verified. Execution capability issued.",
    BLOCK: "Execution prevented before Razorpay.",
    REVIEW: "Human/evidence review required before execution.",
    ERROR: "The run stopped safely before execution.",
  }[decision] || "The controller returned a bounded result.";
  const restraint =
    decision === "REVIEW"
      ? '<p class="decision-restraint">MandateGuard refused to guess. No payment was attempted.</p>'
      : "";
  return `
    <div class="decision-banner decision-banner--${escapeHtml(decision.toLowerCase())}">
      <div class="decision-banner__state">
        <span class="decision-orbit" aria-hidden="true"></span>
        <p>FINAL CONTROLLER</p>
        <strong>${escapeHtml(decision)}</strong>
      </div>
      <div class="decision-banner__message">
        <span>RESOLUTION</span>
        <h3>${escapeHtml(resolution)}</h3>
        <p><strong>Exact reason</strong>${escapeHtml(
          result?.decision_reason || "No controller result is available.",
        )}</p>
        ${restraint}
      </div>
    </div>
  `;
}

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
    <div class="buyer-layout">
      <div class="product-record">
        <div class="product-record__topline">
          <p class="record-label">SELECTED PRODUCT</p>
          <strong class="product-price">${money(buyer?.price_minor, buyer?.currency)}</strong>
        </div>
        <h3>${escapeHtml(buyer?.product)}</h3>
        <p class="product-description">${escapeHtml(buyer?.product_description)}</p>
        <dl class="record-grid">
          <div><dt>Merchant</dt><dd><code>${escapeHtml(buyer?.merchant)}</code></dd></div>
          <div><dt>SKU</dt><dd><code>${escapeHtml(buyer?.sku)}</code></dd></div>
          <div><dt>Currency</dt><dd>${escapeHtml(buyer?.currency)}</dd></div>
          <div><dt>Quantity</dt><dd>${escapeHtml(buyer?.quantity)}</dd></div>
        </dl>
      </div>
      <div class="buyer-trace">
        <p class="record-label">BUYER TOOL CALLS</p>
        <ol class="tool-list">${tools || "<li>No tool calls recorded.</li>"}</ol>
      </div>
    </div>
    <div class="mandate-record">
      <div>
        <p class="record-label">USER MANDATE</p>
        <p>${escapeHtml(buyer?.mandate)}</p>
      </div>
      <div>
        <p class="record-label">CONCISE BUYER RATIONALE</p>
        <p>${escapeHtml(buyer?.rationale)}</p>
      </div>
    </div>
    <p class="authority-alert">${escapeHtml(buyer?.authority_notice)}</p>
  `;
}

export function renderEvidencePanel(evidence) {
  const cards = (evidence?.cards || [])
    .map(
      (card) => `
        <article class="evidence-card">
          <div class="evidence-card__topline">
            <span class="trusted-label"><i aria-hidden="true"></i>TRUSTED</span>
            <code>${escapeHtml(card.evidence_id)}</code>
          </div>
          <p>${escapeHtml(card.text)}</p>
          <dl>
            <div><dt>Source</dt><dd>${escapeHtml(card.source_kind)}</dd></div>
            <div><dt>Scope</dt><dd>${escapeHtml(card.scope)}</dd></div>
            <div><dt>Score</dt><dd><code>${escapeHtml(card.retrieval_score)}</code></dd></div>
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
    <div class="retrieval-meta">
      <div><span>Method</span><strong>${escapeHtml(evidence?.retrieval_method)}</strong></div>
      <div><span>Top-k</span><strong>${escapeHtml(evidence?.top_k)}</strong></div>
      <div><span>Trusted count</span><strong>${escapeHtml(evidence?.trusted_evidence_count)}</strong></div>
    </div>
    <div class="evidence-grid">${cards || empty}</div>
    <div class="buyer-text-box">
      <div>
        <strong>BUYER-PROVIDED TEXT</strong>
        <span>NOT TRUSTED EVIDENCE</span>
      </div>
      <p>${escapeHtml(evidence?.buyer_text?.text)}</p>
    </div>
  `;
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
    <div class="authorization-matrix">
      <div class="authorization-layer">
        <div class="layer-heading">
          <span>A</span>
          <div><strong>DETERMINISTIC</strong><small>Tier A/B checks</small></div>
          ${statusBadge(deterministic.action === "ALLOW" ? "PASS" : deterministic.action)}
        </div>
        <ul class="check-list">${renderCheckRows(deterministic.tier_a)}</ul>
        <details class="secondary-checks">
          <summary>VIEW TIER B CONSISTENCY CHECKS</summary>
          <ul class="check-list">${renderCheckRows(deterministic.tier_b)}</ul>
        </details>
      </div>
      <div class="authorization-layer">
        <div class="layer-heading">
          <span>B</span>
          <div><strong>SEMANTIC</strong><small>Purpose, exclusions, recurrence</small></div>
          ${statusBadge(semantic.verdict)}
        </div>
        <ul class="check-list check-list--semantic">${
          renderCheckRows(semantic.checks) ||
          '<li class="not-evaluated">No semantic constraints were evaluated.</li>'
        }</ul>
        <div class="cache-strip">
          <span>SEMANTIC CACHE</span>
          <strong>${escapeHtml(cache.status || "NOT USED")}</strong>
          <code>${escapeHtml(cache.key_prefix || "NO KEY")}</code>
        </div>
      </div>
    </div>
    <div class="final-controller final-controller--${escapeHtml(
      String(authorization?.final_controller || "ERROR").toLowerCase(),
    )}">
      <span>C&nbsp;&nbsp;FINAL CONTROLLER</span>
      <strong>${escapeHtml(authorization?.final_controller)}</strong>
      <small>${escapeHtml(authorization?.controller_source)}</small>
    </div>
  `;
}

export function renderExecutionPanel(execution) {
  if (!execution || execution.status !== "ORDER_CREATED") {
    const decisionClass = execution?.reason?.toLowerCase().includes("review") ? "review" : "block";
    return `
      <div class="execution-stop execution-stop--${decisionClass}">
        <div class="execution-stop__count">
          <span>RAZORPAY CALLS</span>
          <strong>${escapeHtml(execution?.razorpay_calls ?? 0)}</strong>
        </div>
        <div>
          <h3>Execution stopped at MandateGuard.</h3>
          <p>${escapeHtml(execution?.reason || "Execution did not run.")}</p>
        </div>
      </div>
      <div class="network-line">
        <span>External network calls</span><strong>${escapeHtml(execution?.external_network_calls ?? 0)}</strong>
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
  const environmentDetail = isOfflineDemo
    ? "NO LIVE RAZORPAY REQUEST"
    : execution.environment;
  const resultLabel = isOfflineDemo
    ? "SIMULATED EXECUTION RECEIPT"
    : "PAYMENT ORDER";
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
        <li><span>${escapeHtml(label)}</span><strong data-valid="${value ? "true" : "false"}">${yesNo(value)}</strong></li>`,
    )
    .join("");
  const replay = execution.replay
    ? `
      <div class="replay-result">
        <span class="replay-result__mark" aria-hidden="true"></span>
        <div>
          <p>REPLAY SECURITY RESULT</p>
          <strong>${escapeHtml(String(execution.replay.status).replaceAll("_", " "))}</strong>
          <span>${escapeHtml(humanize(execution.replay.reason))}</span>
        </div>
        <p>Razorpay additional calls: ${escapeHtml(execution.replay.razorpay_additional_calls)}</p>
      </div>`
    : `<button class="button button--secondary" id="replay-button" type="button">TEST CAPABILITY REPLAY</button>`;
  return `
    <div class="execution-environment">
      <span class="execution-environment__signal" aria-hidden="true"></span>
      <span>${environmentLabel}</span>
      <code>${escapeHtml(environmentDetail)}</code>
    </div>
    <div class="capability-block">
      <div class="capability-block__heading">
        <span>SIGNED CAPABILITY</span>
        <strong>BOUND AND VERIFIED</strong>
      </div>
      <ul class="binding-list">${bindingRows}</ul>
    </div>
    <div class="order-result">
      <div class="order-result__heading">
        <span>${resultLabel}</span><strong><i aria-hidden="true"></i>${resultStatus}</strong>
      </div>
      <dl>
        <div><dt>Order ID</dt><dd><code title="${escapeHtml(order.order_id)}">${escapeHtml(shortId(order.order_id))}</code></dd></div>
        <div><dt>Amount</dt><dd>${money(order.amount, order.currency)}</dd></div>
        <div><dt>Currency</dt><dd>${escapeHtml(order.currency)}</dd></div>
        <div><dt>Receipt</dt><dd><code title="${escapeHtml(order.receipt)}">${escapeHtml(shortId(order.receipt))}</code></dd></div>
        <div><dt>Status</dt><dd>${escapeHtml(String(order.status || "").toUpperCase())}</dd></div>
      </dl>
    </div>
    <div class="call-accounting">
      <span>Adapter calls <strong>${escapeHtml(execution.razorpay_calls)}</strong></span>
      <span>External calls <strong>${escapeHtml(execution.external_network_calls)}</strong></span>
    </div>
    ${liveEvidence}
    ${replay}
  `;
}

function renderTimeline(timeline) {
  return (timeline || [])
    .map(
      (item) => `
        <li data-status="${escapeHtml(item.status)}">
          <div class="timeline__marker" aria-hidden="true"></div>
          <span class="timeline__label">${escapeHtml(item.label)}</span>
          ${statusBadge(item.status)}
          <small>${escapeHtml(item.detail || "Awaiting backend state")}</small>
        </li>`,
    )
    .join("");
}

function renderAudit(snapshot) {
  const events = (snapshot.audit || [])
    .map(
      (item) => `
        <li>
          <span class="audit-list__sequence">${escapeHtml(String(item.sequence).padStart(2, "0"))}</span>
          <span class="audit-list__marker" aria-hidden="true"></span>
          <div><strong>${escapeHtml(humanize(item.event))}</strong><small>${escapeHtml(item.recorded_at)}</small></div>
        </li>`,
    )
    .join("");
  return `
    <ol class="audit-list">${events}</ol>
    <details class="raw-json">
      <summary>VIEW RAW JSON</summary>
      <pre>${escapeHtml(JSON.stringify(snapshot, null, 2))}</pre>
    </details>
  `;
}

function renderResearch(research) {
  return `
    <div class="research-status">
      <span>EXPERIMENTAL</span>
      <strong>${escapeHtml(research?.authorization_use)}</strong>
    </div>
    <p class="research-finding">${escapeHtml(research?.finding)}</p>
    <details class="research-evidence">
      <summary>VIEW RESEARCH EVIDENCE</summary>
      <p>${escapeHtml(research?.scope)}</p>
      <code class="source-path">${escapeHtml(research?.source)}</code>
    </details>
  `;
}

function renderRecovery(items) {
  const recoveryStatus = (trigger) => {
    if (trigger === "No trusted evidence retrieved") return "REVIEW";
    if (trigger === "INT-3 artifact serializer failure") return "RECOVERED";
    if (trigger === "Corrupted semantic cache" || trigger === "Capability replay") return "REJECTED";
    return "SAFE STOP";
  };
  return `
    <div class="recovery-list">
      ${(items || [])
        .map(
          (item) => {
            const callCount = Object.hasOwn(item, "additional_external_calls")
              ? `Additional external calls: ${escapeHtml(item.additional_external_calls)}`
              : Object.hasOwn(item, "external_calls")
                ? `External execution calls: ${escapeHtml(item.external_calls)}`
                : "";
            const status = recoveryStatus(item.trigger);
            const noUnsafeEffect = item.retried_failed_request === false
              ? "The failed stochastic request was not retried."
              : callCount || "No unsafe execution side effect was recorded.";
            return `
              <details class="recovery-item">
                <summary>
                  <span>${escapeHtml(item.trigger)}</span>
                  <strong data-recovery-status="${escapeHtml(status)}">${escapeHtml(status)}</strong>
                </summary>
                <dl>
                  <div><dt>WHAT FAILED</dt><dd>${escapeHtml(item.trigger)}</dd></div>
                  <div><dt>SYSTEM RESPONSE</dt><dd>${escapeHtml(item.outcome)}</dd></div>
                  <div><dt>WHY NO UNSAFE SIDE EFFECT OCCURRED</dt><dd>${noUnsafeEffect}</dd></div>
                </dl>
                <details class="recovery-source">
                  <summary>VIEW ENGINEERING SOURCE</summary>
                  <code>${escapeHtml(item.source)}</code>
                </details>
              </details>`;
          },
        )
        .join("")}
    </div>
  `;
}

function createRequestId() {
  if (globalThis.crypto?.randomUUID) return `web_${globalThis.crypto.randomUUID()}`;
  return `web_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload?.error?.message || "The request failed safely.");
  }
  return payload;
}

function init() {
  const elements = {
    system: document.querySelector("#system-state"),
    presets: document.querySelector("#preset-row"),
    intent: document.querySelector("#purchase-intent"),
    run: document.querySelector("#run-button"),
    modeNote: document.querySelector("#mode-note"),
    liveLabel: document.querySelector("#live-mode-label"),
    timeline: document.querySelector("#timeline"),
    runState: document.querySelector("#run-state"),
    resultRegion: document.querySelector("#result-region"),
    decision: document.querySelector("#decision-banner"),
    buyer: document.querySelector("#buyer-panel"),
    evidence: document.querySelector("#evidence-panel"),
    evidenceCount: document.querySelector("#evidence-count"),
    authorization: document.querySelector("#authorization-panel"),
    execution: document.querySelector("#execution-panel"),
    research: document.querySelector("#research-panel"),
    recovery: document.querySelector("#recovery-panel"),
    auditSection: document.querySelector("#audit-section"),
    audit: document.querySelector("#audit-panel"),
    error: document.querySelector("#form-error"),
  };
  const submitLock = new SubmissionLock();
  const replayLock = new SubmissionLock();
  let config;
  let selectedPreset = null;
  let currentRunId = null;

  const applyPanelDisclosure = () => {
    const mobile = window.matchMedia("(max-width: 720px)").matches;
    const mode = mobile ? "mobile" : "desktop";
    document.querySelectorAll(".result-panel").forEach((panel) => {
      if (panel.dataset.disclosureMode === mode) return;
      panel.open = !mobile || panel.id === "authorization-card" || panel.id === "execution-card";
      panel.dataset.disclosureMode = mode;
    });
  };

  const selectedMode = () =>
    document.querySelector('input[name="mode"]:checked')?.value || "offline";

  const setBusy = (busy) => {
    document.body.classList.toggle("is-running", busy);
    elements.run.disabled = busy;
    elements.run.textContent = busy ? "BUYER RUNNING" : "RUN AI BUYER";
    elements.intent.readOnly = busy;
    document
      .querySelectorAll('input[name="mode"], .preset-button')
      .forEach((item) => (item.disabled = busy || item.dataset.unavailable === "true"));
  };

  const showError = (message) => {
    elements.error.textContent = message;
    elements.error.hidden = !message;
  };

  const renderSnapshot = (snapshot) => {
    elements.timeline.innerHTML = renderTimeline(snapshot.timeline);
    elements.runState.textContent = snapshot.state;
    if (snapshot.state === "ERROR") {
      showError(snapshot.error?.message || "The run stopped safely.");
      return;
    }
    if (!snapshot.result) return;
    const result = snapshot.result;
    elements.resultRegion.hidden = false;
    elements.resultRegion.dataset.decision = result.decision;
    elements.decision.innerHTML = renderDecisionBanner(result);
    elements.buyer.innerHTML = renderBuyerPanel(result.buyer);
    elements.evidence.innerHTML = renderEvidencePanel(result.evidence);
    elements.evidenceCount.textContent = `${result.evidence.trusted_evidence_count} ITEMS`;
    elements.authorization.innerHTML = renderAuthorizationPanel(result.authorization);
    elements.execution.innerHTML = renderExecutionPanel(result.execution);
    elements.auditSection.hidden = false;
    elements.audit.innerHTML = renderAudit(snapshot);
    applyPanelDisclosure();
    const replayButton = document.querySelector("#replay-button");
    if (replayButton) replayButton.addEventListener("click", runReplay);
  };

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

  const runReplay = async () => {
    if (!currentRunId || !replayLock.acquire()) return;
    const button = document.querySelector("#replay-button");
    if (button) {
      button.disabled = true;
      button.textContent = "TESTING REPLAY";
    }
    try {
      const snapshot = await fetchJson(
        `/api/runs/${encodeURIComponent(currentRunId)}/replay`,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
      );
      renderSnapshot(snapshot);
    } catch (error) {
      showError(error.message);
    } finally {
      replayLock.release();
    }
  };

  const submit = async () => {
    if (!submitLock.acquire()) return;
    showError("");
    const intent = elements.intent.value.trim();
    if (!intent) {
      showError("Enter a bounded purchase mandate.");
      submitLock.release();
      return;
    }
    setBusy(true);
    elements.resultRegion.hidden = true;
    delete elements.resultRegion.dataset.decision;
    elements.auditSection.hidden = true;
    currentRunId = null;
    try {
      const snapshot = await fetchJson("/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          intent,
          mode: selectedMode(),
          preset_id: selectedPreset,
          request_id: createRequestId(),
        }),
      });
      currentRunId = snapshot.run_id;
      await pollRun(snapshot);
    } catch (error) {
      showError(error.message);
    } finally {
      setBusy(false);
      submitLock.release();
    }
  };

  const renderPresets = () => {
    elements.presets.innerHTML = config.presets
      .map(
        (preset) => `
          <button type="button" class="preset-button" data-preset="${escapeHtml(preset.id)}">
            ${escapeHtml(preset.label)}
          </button>`,
      )
      .join("");
    elements.presets.querySelectorAll("button").forEach((button) => {
      button.addEventListener("click", () => {
        const preset = config.presets.find((item) => item.id === button.dataset.preset);
        selectedPreset = preset.id;
        elements.intent.value = preset.intent;
        elements.presets
          .querySelectorAll("button")
          .forEach((item) => item.classList.toggle("is-active", item === button));
      });
    });
    elements.presets.querySelector("button")?.click();
  };

  elements.intent.addEventListener("input", () => {
    selectedPreset = null;
    elements.presets
      .querySelectorAll("button")
      .forEach((item) => item.classList.remove("is-active"));
  });
  elements.run.addEventListener("click", submit);
  document.querySelectorAll('input[name="mode"]').forEach((input) => {
    input.addEventListener("change", () => {
      const live = selectedMode() === "live";
      elements.modeNote.textContent = live
        ? "Uses configured OpenAI pathways and Razorpay Test Mode after explicit submission."
        : "Deterministic local evaluation. External network calls: 0.";
    });
  });

  const waitingTimeline = [
    "User mandate",
    "AI buyer",
    "Product",
    "Evidence retrieval",
    "Deterministic verification",
    "Semantic verification",
    "Authorization",
    "Execution",
  ].map((label) => ({ label, status: "WAITING", detail: null }));
  elements.timeline.innerHTML = renderTimeline(waitingTimeline);
  window.matchMedia("(max-width: 720px)").addEventListener("change", applyPanelDisclosure);

  fetchJson("/api/config")
    .then((payload) => {
      config = payload;
      elements.system.classList.add("system-state--ready");
      elements.system.querySelector("span:last-child").textContent = "SYSTEM READY";
      elements.research.innerHTML = renderResearch(config.research);
      elements.recovery.innerHTML = renderRecovery(config.failure_recovery);
      if (!config.modes.live.available) {
        const liveInput = elements.liveLabel.querySelector("input");
        liveInput.disabled = true;
        liveInput.dataset.unavailable = "true";
        elements.liveLabel.title = "Live Test Mode is unavailable on this server.";
      }
      renderPresets();
    })
    .catch((error) => {
      elements.system.querySelector("span:last-child").textContent = "SYSTEM UNAVAILABLE";
      showError(error.message);
    });
}

if (typeof document !== "undefined") init();
