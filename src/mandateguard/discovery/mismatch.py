"""Listing / classifier disagreement as an investigation signal.

A listing that files itself under *Education* while its own text reads like
*Trading / Betting* has told us something. What it has told us is that this
listing deserves a closer look - not that the purchase is forbidden.

So a mismatch may:

* raise investigation priority,
* trigger acquisition of additional trusted evidence, or
* surface ``REVIEW``.

It may never authorize, and it may never turn a ``BLOCK`` into anything else.
The classifier is a model over crawled marketing text; treating its opinion as
authority would put a linear model in the payment path, which is the exact thing
MandateGuard exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mandateguard.discovery.classifier import CategoryClassifier, CategoryPrediction
from mandateguard.discovery.schema import DiscoveryProduct
from mandateguard.discovery.trust import AdvisorySignal


SIGNAL_ID = "listing_category_mismatch"

#: Severities, in order. Only the top two are worth an analyst's attention.
SEVERITIES = ("NONE", "LOW", "MEDIUM", "HIGH")

#: Pairs a marketplace taxonomy routinely conflates. Disagreement inside a pair
#: is a taxonomy artefact, not a signal, so it is capped at LOW.
BENIGN_CONFUSIONS: frozenset[frozenset[str]] = frozenset(
    {
        frozenset({"Clothing", "Footwear"}),
        frozenset({"Bags, Wallets & Belts", "Clothing"}),
        frozenset({"Home Decor & Festive Needs", "Home Furnishing"}),
        frozenset({"Home Furnishing", "Kitchen & Dining"}),
        frozenset({"Computers", "Mobiles & Accessories"}),
        frozenset({"Toys & School Supplies", "Pens & Stationery"}),
        frozenset({"Baby Care", "Toys & School Supplies"}),
        frozenset({"Beauty and Personal Care", "Health & Personal Care Appliances"}),
        frozenset({"Sports & Fitness", "Toys & School Supplies"}),
    }
)

#: How far down the model's ranking the listing's own claim may appear before
#: the disagreement stops being interesting, and the smallest taxonomy on which
#: that test means anything.
RUNNER_UP_DEPTH = 3
MIN_CLASSES_FOR_RUNNER_UP = 6

#: Categories where a wrong listing claim has a *money* consequence rather than
#: a merchandising one: a recurring or regulated product presented as something
#: ordinary is what a mandate exclusion is usually written against.
SENSITIVE_PREDICTIONS: frozenset[str] = frozenset(
    {"Health & Personal Care Appliances", "Beauty and Personal Care"}
)


@dataclass(frozen=True, slots=True)
class MismatchSignal:
    """One advisory disagreement between a listing's claim and the model."""

    listing_category: str
    predicted_category: str
    severity: str
    agrees: bool
    prediction: CategoryPrediction
    rationale: str

    @property
    def raises_investigation_priority(self) -> bool:
        return self.severity in {"MEDIUM", "HIGH"}

    @property
    def may_authorize(self) -> bool:
        """Always false. Present so the answer is in the type, not a comment."""

        return False

    def as_signal(self) -> AdvisorySignal:
        return AdvisorySignal(
            signal_id=SIGNAL_ID,
            value=self.severity,
            produced_by=self.prediction.model_id,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "signal_id": SIGNAL_ID,
            "listing_claim": self.listing_category,
            "model_prediction": self.predicted_category,
            "severity": self.severity,
            "agrees": self.agrees,
            "rationale": self.rationale,
            "confidence_band": self.prediction.confidence_band,
            "model_id": self.prediction.model_id,
            "permitted_effects": [
                "RAISE_INVESTIGATION_PRIORITY",
                "REQUEST_ADDITIONAL_TRUSTED_EVIDENCE",
                "SURFACE_REVIEW",
            ],
            "forbidden_effects": [
                "AUTHORIZE_PAYMENT",
                "OVERRIDE_DETERMINISTIC_BLOCK",
                "SATISFY_MISSING_TRUSTED_EVIDENCE",
            ],
            "authorization_authority": "NONE",
        }


def evaluate_mismatch(
    product: DiscoveryProduct, classifier: CategoryClassifier
) -> MismatchSignal:
    """Compare a listing's declared category with the model's reading of it."""

    prediction = classifier.predict(
        f"{product.title}\n{product.description}", top_n=3
    )
    listing = product.top_category
    agrees = listing == prediction.label
    if prediction.matched_terms == 0:
        return MismatchSignal(
            listing_category=listing,
            predicted_category=prediction.label,
            severity="NONE",
            agrees=agrees,
            prediction=prediction,
            rationale=(
                "The listing carries no text the model recognizes, so its "
                "opinion is not evidence of anything."
            ),
        )
    if listing not in classifier.classes:
        return MismatchSignal(
            listing_category=listing,
            predicted_category=prediction.label,
            severity="LOW",
            agrees=False,
            rationale=(
                f"The source filed this listing under {listing!r}, which is not "
                "a category the model was trained on. That is a taxonomy gap, "
                "not a claim about the product."
            ),
            prediction=prediction,
        )
    if agrees:
        return MismatchSignal(
            listing_category=listing,
            predicted_category=prediction.label,
            severity="NONE",
            agrees=True,
            prediction=prediction,
            rationale="The listing's declared category matches its own text.",
        )
    # "The model ranked your claim second" only says something when there are
    # enough classes for second place to be selective. On a small taxonomy every
    # label is near the top, and applying the rule there would quietly disable
    # the signal.
    shortlist = (
        [label for label, _ in prediction.ranked[:RUNNER_UP_DEPTH]]
        if len(classifier.classes) > MIN_CLASSES_FOR_RUNNER_UP
        else []
    )
    if frozenset({listing, prediction.label}) in BENIGN_CONFUSIONS:
        severity = "LOW"
        rationale = (
            f"{listing} and {prediction.label} overlap in this marketplace's "
            "taxonomy. The disagreement is a shelving artefact."
        )
    elif listing in shortlist:
        severity = "LOW"
        rationale = (
            f"The model's first choice is {prediction.label}, but it ranked the "
            f"listing's own claim ({listing}) in its top {RUNNER_UP_DEPTH}."
        )
    elif prediction.confidence_band == "HIGH" or prediction.label in SENSITIVE_PREDICTIONS:
        severity = "HIGH"
        rationale = (
            f"The listing claims {listing}; the model reads its title and "
            f"description as {prediction.label} and is not close between them."
        )
    else:
        severity = "MEDIUM"
        rationale = (
            f"The listing claims {listing}; the model reads it as "
            f"{prediction.label}, without a decisive margin."
        )
    return MismatchSignal(
        listing_category=listing,
        predicted_category=prediction.label,
        severity=severity,
        agrees=False,
        prediction=prediction,
        rationale=rationale,
    )
