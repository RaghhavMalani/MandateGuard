"""The Playground surface: search a sandbox, choose, and be judged for real.

This module composes. It does not decide. Everything it hands back about an
authorization outcome came out of ``run_agentic_checkout`` - the same controller,
the same Tier A/B/C gate, the same capability issuance, the same execution ledger
and the same consent registry that every other run in this product uses. There is
no branch here that reads "sandbox" and returns ALLOW, and there is nowhere for
one to hide: the service never constructs a decision, only a run.

What it *does* own is the framing around the decision - which candidates were
offered, what the mandate was read to say, and why the verdict fell the way it
did in words a person can check against the evidence shown beside them.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from mandateguard.intelligence.models import SelectedProductIdentity
from mandateguard.intelligence.store import TrustedCommerceStore
from mandateguard.models.decision import DecisionAction

from mandateguard.sandbox.intent import (
    MAX_DECLARED_CEILING_MINOR,
    SandboxIntent,
    SandboxIntentError,
    read_intent,
)
from mandateguard.sandbox.onboarding import (
    MerchantDeclaration,
    NeutralDiscoveryAttributes,
    OnboardingError,
    declaration_form,
    onboard,
    onboarded_store,
)
from mandateguard.sandbox.scenarios import (
    SCENARIOS_BY_ID,
    Scenario,
    TRY_THESE,
    public_scenarios,
)
from mandateguard.sandbox.search import (
    Candidate,
    SandboxSearch,
    SearchResult,
    category_directory,
    excluded_summary,
)
from mandateguard.sandbox.session import (
    JudgeSession,
    JudgeSessionRegistry,
    OnboardedMerchant,
    SessionError,
)
from mandateguard.sandbox.store import readiness_for, sandbox_world
from mandateguard.sandbox.templates import BRANDS
from mandateguard.sandbox.universe import SandboxProduct


WORLD_SANDBOX = "SANDBOX"
WORLD_SANDBOX_ONBOARDED = "SANDBOX_ONBOARDED"
WORLD_REGISTERED = "REGISTERED"
WORLD_MARKETPLACE = "HISTORICAL_MARKETPLACE"

MAX_TOP_K = 10
MIN_TOP_K = 1

#: Where `scripts/evaluate_judge_playground.py` writes its measured outcome mix.
HEALTH_REPORT_PATH = Path("data") / "eval" / "judge-playground" / "JUDGE_QUERY_REPORT.json"


def _load_health_report(repository_root: Path | None) -> dict[str, Any] | None:
    """Read the recorded outcome mix, without the per-query rows.

    Absent, malformed or from a different world, it is simply not shown. A
    summary that silently described a different catalogue would be worse than
    no summary at all.
    """

    root = repository_root or Path(__file__).resolve().parents[3]
    try:
        raw = json.loads((root / HEALTH_REPORT_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    if not isinstance(raw, dict):
        return None
    required = ("overall", "ordinary", "insistent_selection", "queries", "world_version")
    if any(key not in raw for key in required):
        return None
    return {
        "queries": raw["queries"],
        "query_set_version": raw.get("query_set_version"),
        "world_version": raw["world_version"],
        "products_sha256": raw.get("products_sha256"),
        "overall": raw["overall"],
        "ordinary": raw["ordinary"],
        "insistent_selection": raw["insistent_selection"],
        "by_cohort": raw.get("by_cohort", {}),
        "latency_ms": raw.get("latency_ms", {}),
        "experience_targets": raw.get("experience_targets", {}),
    }


class PlaygroundError(ValueError):
    """A Playground request could not be served as asked."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.public_message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class SandboxRunPlan:
    """Everything a sandbox authorization run needs, resolved server-side.

    The plan is built from the server's own catalogue after re-reading the
    person's instruction. Nothing the browser sent about a product - its price,
    its merchant, its billing model - survives into it.
    """

    world: str
    store: TrustedCommerceStore
    intent: SandboxIntent
    product: SandboxProduct


class PlaygroundSurface:
    """Sandbox retrieval, session scoping, and simulated merchant onboarding."""

    __slots__ = ("_universe", "_store", "_manifest", "_search", "sessions", "_health")

    def __init__(
        self,
        *,
        sessions: JudgeSessionRegistry | None = None,
        repository_root: Path | None = None,
    ) -> None:
        universe, store, manifest = sandbox_world()
        self._universe = universe
        self._store = store
        self._manifest = manifest
        self._search = SandboxSearch(universe)
        self.sessions = sessions or JudgeSessionRegistry()
        self._health = _load_health_report(repository_root)

    # -- configuration ----------------------------------------------------

    @property
    def store(self) -> TrustedCommerceStore:
        return self._store

    @property
    def manifest(self) -> dict[str, Any]:
        return dict(self._manifest)

    def public_config(self) -> dict[str, Any]:
        return {
            "world": WORLD_SANDBOX,
            "badge": "SIMULATED MERCHANT SANDBOX",
            "headline": "Test MandateGuard with arbitrary buying instructions.",
            "explanation": [
                "This sandbox contains simulated merchants that publish the trusted "
                "evidence MandateGuard requires.",
                "Nothing here represents a live marketplace or real money.",
            ],
            "catalog": {
                "products": self._manifest["product_count"],
                "merchants": self._manifest["merchant_count"],
                "categories": self._manifest["category_count"],
                "evidence_records": self._manifest["evidence_count"],
                "world_version": self._manifest["world_version"],
                "products_sha256": self._manifest["products_sha256"],
                "evidence_sha256": self._manifest["evidence_sha256"],
                "currency": self._manifest["currency"],
                "price_minor_range": list(self._manifest["price_minor_range"]),
                "synthetic": True,
            },
            "categories": category_directory(),
            "scenarios": public_scenarios(),
            "try_these": [dict(item) for item in TRY_THESE],
            "max_top_k": MAX_TOP_K,
            "max_declared_ceiling_minor": MAX_DECLARED_CEILING_MINOR,
            "execution": {
                "adapter": "OFFLINE_RAZORPAY_TEST_ADAPTER",
                "external_calls": 0,
                "label": "SIMULATED OFFLINE ORDER",
            },
            "session": {
                "purpose": "DEMO_SCOPING_NOT_AUTHENTICATION",
                "live_sessions": self.sessions.live_count(),
            },
            "outcome_health": (
                self._health
                if self._health is not None
                and self._health["world_version"] == self._manifest["world_version"]
                else None
            ),
        }

    # -- sessions ---------------------------------------------------------

    def open_session(self, session_id: object = None) -> tuple[JudgeSession, bool]:
        return self.sessions.resolve_or_create(session_id)

    def require_session(self, session_id: object) -> JudgeSession:
        try:
            return self.sessions.get(session_id)
        except SessionError as error:
            raise PlaygroundError("SESSION_UNAVAILABLE", str(error)) from error

    # -- search -----------------------------------------------------------

    def read(
        self, intent_text: str, *, declared_ceiling_minor: int | None = None
    ) -> SandboxIntent:
        try:
            return read_intent(
                intent_text,
                known_brands=BRANDS,
                declared_ceiling_minor=declared_ceiling_minor,
            )
        except SandboxIntentError as error:
            raise PlaygroundError(error.code, error.public_message) from error

    def search(
        self,
        *,
        intent_text: str,
        top_k: int = 8,
        declared_ceiling_minor: int | None = None,
        session: JudgeSession | None = None,
    ) -> dict[str, Any]:
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not MIN_TOP_K <= top_k <= MAX_TOP_K:
            raise PlaygroundError(
                "INVALID_TOP_K", f"top_k must be between {MIN_TOP_K} and {MAX_TOP_K}."
            )
        intent = self.read(intent_text, declared_ceiling_minor=declared_ceiling_minor)
        result = self._search.search(intent, limit=top_k)
        onboarded = self._matching_onboarded(session, intent)
        # A person's newly onboarded record belongs at the front of their own
        # results, but the response remains within the same public 1-10 bound.
        # Without this cap, eight session records plus eight generated results
        # could turn the promised candidate set into a 16-item wall.
        candidates = (
            onboarded
            + [self._candidate_mapping(item) for item in result.candidates]
        )[:top_k]
        return {
            "world": WORLD_SANDBOX,
            "mandate": intent.to_mapping(),
            "mandate_plain_english": intent.plain_english(),
            "spending_limit_required": intent.max_total_minor is None,
            "retrieval": {
                "method": "LEXICAL_FIELD_WEIGHTED_PLUS_CATEGORY_SYNONYM",
                "catalog_products": self._manifest["product_count"],
                "considered": result.considered,
                "matched_categories": list(result.matched_categories),
                "returned": len(candidates),
            },
            "candidates": candidates,
            "near_misses": [item.to_mapping() for item in result.near_misses],
            "constraints_applied": excluded_summary(intent),
            "no_match_message": (
                None
                if candidates
                else "No suitable sandbox product matched all of your constraints."
            ),
            "authority": "RETRIEVAL_IS_ADVISORY_AND_DECIDES_NOTHING",
        }

    def _matching_onboarded(
        self, session: JudgeSession | None, intent: SandboxIntent
    ) -> list[dict[str, Any]]:
        """Surface this visitor's own onboarded listings alongside the catalogue."""

        if session is None:
            return []
        results: list[dict[str, Any]] = []
        query_terms = {word for word in intent.search_text.split() if len(word) > 2}
        for merchant in self.sessions.onboarded(session):
            haystack = f"{merchant.product.name} {merchant.product.category_label}".lower()
            if query_terms and not any(term in haystack for term in query_terms):
                continue
            store = onboarded_store(merchant)
            mapping = merchant.public_mapping()
            mapping["readiness"] = readiness_for(store, merchant.product)
            mapping["why_found"] = {
                "category_match": merchant.product.category_label,
                "matched_terms": sorted(
                    term for term in query_terms if term in haystack
                )[:6],
                "brand_match": None,
                "within_budget": (
                    intent.max_total_minor is None
                    or merchant.product.price_minor * intent.quantity
                    <= intent.max_total_minor
                ),
                "source": "SIMULATED_MERCHANT_ONBOARDING_IN_THIS_SESSION",
            }
            results.append(mapping)
        return results

    def _candidate_mapping(self, candidate: Candidate) -> dict[str, Any]:
        product = candidate.product
        mapping = product.public_mapping()
        mapping["why_found"] = candidate.signal.to_mapping()
        mapping["readiness"] = readiness_for(self._store, product)
        return mapping

    # -- selection --------------------------------------------------------

    def plan_for(
        self,
        *,
        intent_text: str,
        catalog_product_id: str,
        declared_ceiling_minor: int | None = None,
        session: JudgeSession | None = None,
    ) -> SandboxRunPlan:
        """Resolve a browser selection against the server's own catalogue.

        The client sends an identifier and nothing else that matters. Price,
        merchant, billing model and evidence are all re-read here, so a client
        that mutates any of them changes only what it shows itself.
        """

        if not isinstance(catalog_product_id, str) or not catalog_product_id:
            raise PlaygroundError(
                "PRODUCT_NOT_FOUND", "No sandbox listing was selected."
            )
        intent = self.read(intent_text, declared_ceiling_minor=declared_ceiling_minor)
        if intent.max_total_minor is None:
            raise PlaygroundError(
                "SPENDING_LIMIT_REQUIRED",
                "Your instruction states no spending limit. Set one before "
                "MandateGuard checks this purchase.",
            )
        product = self._universe.by_catalog_id(catalog_product_id)
        if product is not None:
            return SandboxRunPlan(
                world=WORLD_SANDBOX,
                store=self._store,
                intent=intent,
                product=product,
            )
        merchant = self._onboarded_by_catalog_id(session, catalog_product_id)
        if merchant is None:
            raise PlaygroundError(
                "PRODUCT_NOT_FOUND",
                "That listing is not in this sandbox, or not in this session.",
            )
        return SandboxRunPlan(
            world=WORLD_SANDBOX_ONBOARDED,
            store=onboarded_store(merchant),
            intent=intent,
            product=merchant.product,
        )

    def _onboarded_by_catalog_id(
        self, session: JudgeSession | None, catalog_product_id: str
    ) -> OnboardedMerchant | None:
        if session is None:
            return None
        for merchant in self.sessions.onboarded(session):
            if merchant.product.catalog_product_id == catalog_product_id:
                return merchant
        return None

    def selected_identity(self, plan: SandboxRunPlan) -> SelectedProductIdentity:
        return SelectedProductIdentity(
            merchant_id=plan.product.merchant_id,
            sku=plan.product.sku,
            catalog_product_id=plan.product.catalog_product_id,
            source="mandateguard-sandbox",
            source_product_id=f"{plan.product.merchant_id}/{plan.product.sku}",
        )

    def product_panel(self, plan: SandboxRunPlan) -> dict[str, Any]:
        """The product and its trusted evidence, before any decision is made."""

        entries = plan.store.evidence_for_product(
            merchant_id=plan.product.merchant_id, sku=plan.product.sku
        )
        return {
            "world": plan.world,
            "product": plan.product.public_mapping(),
            "readiness": readiness_for(plan.store, plan.product),
            "trusted_evidence": [
                {
                    "evidence_id": entry.evidence_id,
                    "source_kind": entry.source_kind,
                    "scope": "PRODUCT" if entry.sku is not None else "MERCHANT",
                    "text": entry.text,
                }
                for entry in entries
            ],
            "mandate": plan.intent.to_mapping(),
            "mandate_plain_english": plan.intent.plain_english(),
            "notice": "SIMULATED MERCHANT SANDBOX. No real money moves.",
        }

    # -- scenarios --------------------------------------------------------

    def scenario(self, scenario_id: object) -> Scenario:
        if not isinstance(scenario_id, str) or scenario_id not in SCENARIOS_BY_ID:
            raise PlaygroundError("SCENARIO_NOT_FOUND", "That scenario is not registered.")
        return SCENARIOS_BY_ID[scenario_id]

    def scenario_selection(self, scenario: Scenario) -> tuple[SandboxIntent, SandboxProduct]:
        """Pick the listing a scenario is about, from the live catalogue.

        The rules below choose a listing by a property of the *world* - what it
        costs, what its merchant published - never by an outcome. A scenario
        cannot select "a product that will BLOCK", because nothing here knows
        what will block.
        """

        intent = self.read(
            scenario.intent, declared_ceiling_minor=scenario.declared_ceiling_minor
        )
        result: SearchResult = self._search.search(intent, limit=MAX_TOP_K)
        if scenario.selection == "TOP_CANDIDATE":
            if not result.candidates:
                raise PlaygroundError(
                    "SCENARIO_UNAVAILABLE", "This scenario found no sandbox listing."
                )
            return intent, result.candidates[0].product
        if scenario.selection == "CATEGORY_ABOVE_BUDGET":
            for miss in result.near_misses:
                if (
                    miss.excluded_by == "MAX_TOTAL"
                    and miss.product.category_id == scenario.selection_argument
                ):
                    return intent, miss.product
            for product in self._universe.products:
                if (
                    product.category_id == scenario.selection_argument
                    and intent.max_total_minor is not None
                    and product.price_minor > intent.max_total_minor
                ):
                    return intent, product
            raise PlaygroundError(
                "SCENARIO_UNAVAILABLE",
                "No sandbox listing in that category is above this budget.",
            )
        if scenario.selection == "EVIDENCE_FAMILY":
            for candidate in result.candidates:
                if candidate.product.evidence_family.value == scenario.selection_argument:
                    return intent, candidate.product
            # Widen beyond the ranked page rather than fail the scenario: the
            # family is a property of the world, and the world contains one.
            for product in self._universe.products:
                if product.evidence_family.value != scenario.selection_argument:
                    continue
                if (
                    intent.max_total_minor is not None
                    and product.price_minor > intent.max_total_minor
                ):
                    continue
                if product.recurring and not intent.recurring_allowed:
                    continue
                return intent, product
            raise PlaygroundError(
                "SCENARIO_UNAVAILABLE",
                "No sandbox listing with that published evidence shape is in budget.",
            )
        raise PlaygroundError("SCENARIO_UNAVAILABLE", "That scenario cannot be resolved.")

    # -- simulated merchant onboarding ------------------------------------

    def onboarding_form(self, listing: Mapping[str, Any]) -> dict[str, Any]:
        try:
            attributes = NeutralDiscoveryAttributes.from_listing(listing)
        except OnboardingError as error:
            raise PlaygroundError(error.code, error.public_message) from error
        return declaration_form(attributes)

    def onboard_listing(
        self,
        *,
        session: JudgeSession,
        listing: Mapping[str, Any],
        declaration: object,
    ) -> OnboardedMerchant:
        try:
            attributes = NeutralDiscoveryAttributes.from_listing(listing)
            declared = MerchantDeclaration.from_mapping(declaration)
        except OnboardingError as error:
            raise PlaygroundError(error.code, error.public_message) from error
        merchant = onboard(
            attributes=attributes, declaration=declared, session_id=session.session_id
        )
        try:
            self.sessions.add_onboarded(session, merchant)
        except SessionError as error:
            raise PlaygroundError("ONBOARDING_LIMIT_REACHED", str(error)) from error
        return merchant

    def onboarded_panel(self, merchant: OnboardedMerchant) -> dict[str, Any]:
        store = onboarded_store(merchant)
        return {
            "world": WORLD_SANDBOX_ONBOARDED,
            "simulation": True,
            "notice": (
                "SIMULATION. A new synthetic sandbox merchant record was created. "
                "The original marketplace listing was not modified and did not "
                "become trusted."
            ),
            "merchant": {
                "merchant_id": merchant.merchant_id,
                "display_name": merchant.display_name,
                "sku": merchant.sku,
                "created_at": merchant.created_at,
            },
            "product": merchant.product.public_mapping(),
            "readiness": readiness_for(store, merchant.product),
            "source_listing": {
                "listing_id": merchant.source_listing_id,
                "title": merchant.source_listing_title,
                "still_untrusted": True,
                "note": (
                    "The marketplace listing is unchanged. It remains "
                    "discovery-only and still cannot be authorized."
                ),
            },
            "trusted_evidence": [
                {
                    "evidence_id": entry.evidence_id,
                    "source_kind": entry.source_kind,
                    "scope": "PRODUCT" if entry.sku is not None else "MERCHANT",
                    "text": entry.text,
                }
                for entry in merchant.evidence
            ],
        }


# ---------------------------------------------------------------------------
# Decision narration
#
# These functions read a finished run's own output. They never compute a
# verdict, and they never reach a conclusion the run did not already record.
# ---------------------------------------------------------------------------

_HEADLINES = {
    "ALLOW": "Your mandate permits this purchase.",
    "BLOCK": "MandateGuard stopped this before payment.",
    "REVIEW": "MandateGuard refused to guess.",
}


def explain_decision(result: Mapping[str, Any], intent: SandboxIntent) -> dict[str, Any]:
    """Say why the recorded verdict happened, in checkable lines."""

    decision = str(result.get("decision", ""))
    authorization = result.get("authorization")
    authorization = authorization if isinstance(authorization, Mapping) else {}
    deterministic = authorization.get("deterministic")
    deterministic = deterministic if isinstance(deterministic, Mapping) else {}
    semantic = authorization.get("semantic")
    semantic = semantic if isinstance(semantic, Mapping) else {}
    buyer = result.get("buyer")
    buyer = buyer if isinstance(buyer, Mapping) else {}
    execution = result.get("execution")
    execution = execution if isinstance(execution, Mapping) else {}

    reasons: list[str] = []
    failed_constraints: list[str] = []
    price_minor = buyer.get("price_minor")
    currency = buyer.get("currency", "INR")

    if isinstance(price_minor, int) and intent.max_total_minor is not None:
        comparator = "<=" if price_minor <= intent.max_total_minor else ">"
        reasons.append(
            f"{currency} {price_minor / 100:,.2f} {comparator} "
            f"{currency} {intent.max_total_minor / 100:,.2f} stated limit"
        )
    for row in deterministic.get("tier_a", []) or []:
        if isinstance(row, Mapping) and row.get("status") not in {"PASS", None}:
            failed_constraints.append(str(row.get("family", "")))
            reasons.append(f"{row.get('label')}: {row.get('reason')}")
    for row in deterministic.get("tier_b", []) or []:
        if isinstance(row, Mapping) and row.get("status") == "FAIL":
            failed_constraints.append(str(row.get("family", "")))
            reasons.append(f"{row.get('label')}: {row.get('reason')}")
    for row in semantic.get("checks", []) or []:
        if not isinstance(row, Mapping):
            continue
        status = row.get("status")
        if status == "PASS":
            reasons.append(f"{row.get('constraint')} — {row.get('reason')}")
        elif status in {"VIOLATION", "ABSTAIN"}:
            failed_constraints.append(str(row.get("constraint_id", "")))
            reasons.append(f"{status}: {row.get('constraint')} — {row.get('reason')}")

    if decision == "ALLOW":
        if not failed_constraints:
            reasons.append("Merchant identity and SKU evidence verified")
            reasons.append("Consent ACTIVE at the moment of decision")
    return {
        "headline": _HEADLINES.get(decision, "MandateGuard reached a decision."),
        "decision": decision,
        "why": reasons[:10],
        "failed_constraints": [item for item in failed_constraints if item][:6],
        "provider_calls": execution.get("razorpay_calls", 0),
        "external_network_calls": execution.get("external_network_calls", 0),
        # An ALLOW reaches the execution gate, but a deferred or revoked run
        # may still make no adapter call, and the public offline adapter never
        # reaches an external payment provider. Keep this literal.
        "payment_reached": bool(execution.get("external_network_calls", 0)),
        "offline_adapter_reached": bool(execution.get("razorpay_calls", 0)),
        "controller": "EXISTING_FROZEN_MANDATEGUARD_CONTROLLER",
    }


def decision_is(result: Mapping[str, Any], action: DecisionAction) -> bool:
    return str(result.get("decision")) == action.value
