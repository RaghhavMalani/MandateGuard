"""Defensive analytics over a proposed transaction.

Each feature answers one question about a proposal that a human reviewer would
ask, and each is computed deterministically from data the system already holds.
None of them decides anything: the output is a priority ordering and a list of
reasons, handed to the surface that shows a human what to look at.

The learned-detector question is answered in `mandateguard.ml.anomaly_eval`,
against a frozen evaluation set, by comparing this deterministic scorer with a
trained alternative. Whatever that comparison found is reported there, including
if it found nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Any, Mapping

from mandateguard.discovery.catalog import DiscoveryCatalog
from mandateguard.discovery.index.analyzer import analyze
from mandateguard.discovery.intent import ParsedIntent
from mandateguard.discovery.mismatch import MismatchSignal
from mandateguard.discovery.schema import DiscoveryProduct
from mandateguard.discovery.trust import AdvisorySignal


ANALYTICS_VERSION = "discovery-proposal-anomaly-v1"

#: Feature identifiers, each with the plain-English question it answers.
FEATURE_QUESTIONS: Mapping[str, str] = {
    "price_vs_category": "Is this priced like other products in its category?",
    "price_changed_after_authorization": "Did the amount move after we authorized it?",
    "category_listing_mismatch": "Does the listing's declared category match its own text?",
    "title_description_mismatch": "Does the description describe the product in the title?",
    "sku_semantic_mismatch": "Does the identifier match the product it claims to be?",
    "merchant_mismatch": "Is the seller the one the mandate expects?",
    "stale_evidence": "Is the evidence recent enough to still be true?",
    "recurrence_cues": "Does the text hint at a recurring charge?",
    "missing_trusted_evidence": "Is there any authoritative evidence at all?",
    "consent_state": "Is consent currently active for this mandate?",
    "replay_attempt": "Has this exact authorization been presented before?",
}

#: Weights are ordinal, not calibrated probabilities. They order what a reviewer
#: looks at first; they never cross a threshold that permits a payment.
FEATURE_WEIGHTS: Mapping[str, float] = {
    "missing_trusted_evidence": 3.0,
    "price_changed_after_authorization": 3.0,
    "replay_attempt": 3.0,
    "consent_state": 3.0,
    "merchant_mismatch": 2.5,
    "sku_semantic_mismatch": 2.0,
    "recurrence_cues": 2.0,
    "category_listing_mismatch": 1.5,
    "stale_evidence": 1.5,
    "price_vs_category": 1.0,
    "title_description_mismatch": 1.0,
}

_MISMATCH_SEVERITY_VALUE = {"NONE": 0.0, "LOW": 0.25, "MEDIUM": 0.6, "HIGH": 1.0}
_RECURRENCE_CUES = (
    "subscription",
    "subscriptions",
    "auto-renew",
    "auto renew",
    "renews",
    "renewal",
    "recurring",
    "per month",
    "monthly plan",
    "membership",
    "billed monthly",
    "billed annually",
)
#: An interquartile-range multiplier. 1.5 is the conventional outlier fence and
#: is used here rather than a tuned value so the number means what it usually
#: means.
_IQR_FENCE = 1.5


@dataclass(frozen=True, slots=True)
class CategoryPriceProfile:
    """Robust price statistics for one category, computed from the catalog."""

    category: str
    count: int
    median_minor: float
    lower_fence: float
    upper_fence: float

    def deviation(self, price_minor: int) -> float:
        """0.0 inside the fences, rising toward 1.0 as the price leaves them."""

        if self.count < 8:
            return 0.0
        if self.lower_fence <= price_minor <= self.upper_fence:
            return 0.0
        span = max(self.upper_fence - self.lower_fence, 1.0)
        distance = (
            price_minor - self.upper_fence
            if price_minor > self.upper_fence
            else self.lower_fence - price_minor
        )
        return min(1.0, distance / span)


def build_price_profiles(
    catalog: DiscoveryCatalog,
) -> dict[str, CategoryPriceProfile]:
    """Per-category price fences. Median and IQR, so a few 5-lakh listings
    cannot drag the notion of "normal" with them."""

    grouped: dict[str, list[int]] = {}
    for product in catalog:
        if product.price_minor is None:
            continue
        grouped.setdefault(product.top_category, []).append(product.price_minor)
    profiles: dict[str, CategoryPriceProfile] = {}
    for category, prices in grouped.items():
        prices.sort()
        size = len(prices)
        lower_quartile = prices[size // 4]
        upper_quartile = prices[(3 * size) // 4]
        spread = upper_quartile - lower_quartile
        profiles[category] = CategoryPriceProfile(
            category=category,
            count=size,
            median_minor=float(median(prices)),
            lower_fence=float(lower_quartile - _IQR_FENCE * spread),
            upper_fence=float(upper_quartile + _IQR_FENCE * spread),
        )
    return profiles


@dataclass(frozen=True, slots=True)
class AnomalyFeature:
    feature_id: str
    value: float
    question: str
    finding: str
    triggered: bool

    def to_mapping(self) -> dict[str, Any]:
        return {
            "feature_id": self.feature_id,
            "question": self.question,
            "value": round(self.value, 4),
            "finding": self.finding,
            "triggered": self.triggered,
            "weight": FEATURE_WEIGHTS.get(self.feature_id, 0.0),
        }


@dataclass(frozen=True, slots=True)
class AnomalyAssessment:
    """An ordered set of reasons to look closer. Never a decision."""

    score: float
    band: str
    features: tuple[AnomalyFeature, ...] = field(default_factory=tuple)

    @property
    def triggered(self) -> tuple[AnomalyFeature, ...]:
        return tuple(item for item in self.features if item.triggered)

    def as_signal(self) -> AdvisorySignal:
        return AdvisorySignal(
            signal_id="proposal_anomaly_assessment",
            value=self.band,
            produced_by=ANALYTICS_VERSION,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "analytics_version": ANALYTICS_VERSION,
            "score": round(self.score, 4),
            "band": self.band,
            "triggered_count": len(self.triggered),
            "features": [item.to_mapping() for item in self.features],
            "effect": "INVESTIGATION_PRIORITY_ONLY",
            "authorization_authority": "NONE",
        }


@dataclass(frozen=True, slots=True)
class ProposalContext:
    """Everything the analytics is allowed to look at."""

    product: DiscoveryProduct
    intent: ParsedIntent
    price_profile: CategoryPriceProfile | None
    mismatch: MismatchSignal | None = None
    trusted_evidence_count: int = 0
    authorized_price_minor: int | None = None
    presented_price_minor: int | None = None
    expected_merchant: str | None = None
    evidence_age_days: float | None = None
    consent_active: bool | None = None
    replay_seen: bool = False


def _token_overlap(left: str, right: str) -> float:
    left_terms = set(analyze(left))
    right_terms = set(analyze(right))
    if not left_terms or not right_terms:
        return 0.0
    return len(left_terms & right_terms) / len(left_terms)


def assess(context: ProposalContext) -> AnomalyAssessment:
    """Compute every registered feature for one proposal."""

    product = context.product
    features: list[AnomalyFeature] = []

    def add(feature_id: str, value: float, finding: str) -> None:
        features.append(
            AnomalyFeature(
                feature_id=feature_id,
                value=max(0.0, min(1.0, float(value))),
                question=FEATURE_QUESTIONS[feature_id],
                finding=finding,
                triggered=value > 0.0,
            )
        )

    profile = context.price_profile
    if product.price_minor is None:
        add("price_vs_category", 0.0, "This listing publishes no price to compare.")
    elif profile is None or profile.count < 8:
        add(
            "price_vs_category",
            0.0,
            "Too few priced listings in this category to say what normal is.",
        )
    else:
        deviation = profile.deviation(product.price_minor)
        multiple = product.price_minor / max(profile.median_minor, 1.0)
        add(
            "price_vs_category",
            deviation,
            (
                f"{multiple:.1f}x the {profile.category} median "
                f"({profile.median_minor / 100:,.0f}); outside the usual range."
                if deviation > 0.0
                else (
                    f"{multiple:.1f}x the {profile.category} median "
                    f"({profile.median_minor / 100:,.0f}); within the usual range."
                )
            ),
        )

    authorized = context.authorized_price_minor
    presented = context.presented_price_minor
    if authorized is None or presented is None:
        add(
            "price_changed_after_authorization",
            0.0,
            "No authorized amount exists yet, so nothing could have moved.",
        )
    elif authorized == presented:
        add(
            "price_changed_after_authorization",
            0.0,
            "The presented amount is the authorized amount, to the paisa.",
        )
    else:
        add(
            "price_changed_after_authorization",
            1.0,
            (
                f"Authorized {authorized / 100:,.2f}, presented "
                f"{presented / 100:,.2f}. The execution gate refuses this on its "
                "own; the analytics only names it."
            ),
        )

    mismatch = context.mismatch
    if mismatch is None:
        add("category_listing_mismatch", 0.0, "The classifier was not consulted.")
    else:
        add(
            "category_listing_mismatch",
            _MISMATCH_SEVERITY_VALUE[mismatch.severity],
            mismatch.rationale,
        )

    overlap = _token_overlap(product.title, product.description)
    if not product.description:
        add(
            "title_description_mismatch",
            0.5,
            "The listing has a title and no description to corroborate it.",
        )
    else:
        add(
            "title_description_mismatch",
            max(0.0, 1.0 - overlap * 2.5),
            (
                f"{overlap:.0%} of the title's words appear in the description."
                if overlap > 0.0
                else "The description shares no words with the title."
            ),
        )

    identifier_overlap = _token_overlap(
        product.source_product_id.replace("-", " "), product.title
    )
    if not any(character.isalpha() for character in product.source_product_id):
        add(
            "sku_semantic_mismatch",
            0.0,
            "The identifier is opaque by design, so it cannot disagree with the title.",
        )
    else:
        add(
            "sku_semantic_mismatch",
            0.0 if identifier_overlap > 0.0 else 0.3,
            (
                "The identifier shares wording with the title."
                if identifier_overlap > 0.0
                else "The identifier is not derived from the product name."
            ),
        )

    expected = context.expected_merchant
    if expected is None:
        add(
            "merchant_mismatch",
            0.0,
            "The mandate names no expected merchant, so there is nothing to contradict.",
        )
    elif product.merchant_or_seller == expected:
        add("merchant_mismatch", 0.0, f"Seller is {expected}, as the mandate expects.")
    else:
        add(
            "merchant_mismatch",
            1.0,
            (
                f"The mandate expects {expected}; this listing is sold by "
                f"{product.merchant_or_seller or 'an unidentified seller'}."
            ),
        )

    age = context.evidence_age_days
    if age is None:
        add("stale_evidence", 0.0, "No dated trusted evidence is attached yet.")
    else:
        add(
            "stale_evidence",
            min(1.0, max(0.0, (age - 30.0) / 335.0)),
            (
                f"The most recent trusted evidence is {age:.0f} days old."
                if age > 30
                else f"Trusted evidence is {age:.0f} days old."
            ),
        )

    haystack = f"{product.title} {product.description}".casefold()
    cues = [cue for cue in _RECURRENCE_CUES if cue in haystack]
    if not cues:
        add("recurrence_cues", 0.0, "The listing text suggests a one-time purchase.")
    elif context.intent.recurring_allowed is False:
        add(
            "recurrence_cues",
            1.0,
            (
                f"The mandate forbids recurring charges and the listing says "
                f"{cues[0]!r}. Whether that is a real subscription needs "
                "authoritative merchant terms, not this text."
            ),
        )
    else:
        add(
            "recurrence_cues",
            0.4,
            f"The listing text mentions {cues[0]!r} and the mandate did not rule it out.",
        )

    if context.trusted_evidence_count > 0:
        add(
            "missing_trusted_evidence",
            0.0,
            f"{context.trusted_evidence_count} trusted evidence items resolved.",
        )
    else:
        add(
            "missing_trusted_evidence",
            1.0,
            (
                "No merchant-controlled evidence exists for this listing. A "
                "crawled catalog row is a claim, not an authorization."
            ),
        )

    if context.consent_active is None:
        add("consent_state", 0.0, "No mandate has been registered for this listing yet.")
    elif context.consent_active:
        add("consent_state", 0.0, "Consent is currently active.")
    else:
        add("consent_state", 1.0, "Consent is no longer active for this mandate.")

    add(
        "replay_attempt",
        1.0 if context.replay_seen else 0.0,
        (
            "This authorization has already been presented once."
            if context.replay_seen
            else "This authorization has not been presented before."
        ),
    )

    total = sum(
        item.value * FEATURE_WEIGHTS.get(item.feature_id, 0.0) for item in features
    )
    ceiling = sum(FEATURE_WEIGHTS.values())
    score = total / ceiling if ceiling else 0.0
    return AnomalyAssessment(score=score, band=band_for(score), features=tuple(features))


def band_for(score: float) -> str:
    if score >= 0.30:
        return "HIGH"
    if score >= 0.15:
        return "ELEVATED"
    if score > 0.0:
        return "LOW"
    return "NONE"


def feature_vector(assessment: AnomalyAssessment) -> list[float]:
    """Stable ordered vector, for the learned-detector comparison."""

    by_id = {item.feature_id: item.value for item in assessment.features}
    return [by_id.get(name, 0.0) for name in sorted(FEATURE_QUESTIONS)]


def feature_names() -> list[str]:
    return sorted(FEATURE_QUESTIONS)
