"""Pre-inference features for the INT-3 evidence-sufficiency experiment.

Every feature here is computable *before* any semantic model call for the
subset being described.  Nothing downstream of the semantic verifier may enter
this module: no subset verdict, no final action, no engineering expectation,
and no full-evidence reference result.  ``assert_no_target_leakage`` and the
strict ``SubsetFeatureInput`` constructor enforce that boundary mechanically
rather than by convention.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from types import MappingProxyType
from typing import Mapping

from mandateguard.engineering.int3.models import (
    CaseFamily,
    Int3ExperimentError,
    case_family_for_constraint_kinds,
)
from mandateguard.semantic.evidence import SemanticEvidenceEntry


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

# The INT-2 production condition ranks with this fixed, untuned blend.  INT-3
# reuses it verbatim so the retrieval channel is the one the frozen
# full-evidence reference actually used; it is not re-tuned here.
REFERENCE_RETRIEVAL_ALPHA = 0.4

# Frozen Stage-A surface that carries both score channels for every document.
REFERENCE_SURFACE_CONFIGURATION_ID = "hybrid.alpha-0.00.k-5"


#: Field names that are only knowable after semantic inference, or that encode
#: the engineering answer directly.  None of these may ever become a feature.
FORBIDDEN_TARGET_FIELDS = frozenset(
    {
        "decision_stable",
        "engineering_expectation",
        "expectation_reason",
        "expected_action",
        "expected_final_action",
        "expected_semantic_behavior",
        "final_action",
        "full_reference_action",
        "full_reference_semantic_behavior",
        "observed_action",
        "observed_final_action",
        "observed_semantic_behavior",
        "semantic_verdict",
        "subset_final_action",
        "subset_semantic_behavior",
        "sufficient",
        "transition",
        "verdict",
    }
)

_LEAKY_SUBSTRINGS = (
    "verdict",
    "final_action",
    "expectation",
    "decision_stable",
    "observed_",
    "ground_truth",
    "label",
    "target",
)


FEATURE_NAMES: tuple[str, ...] = (
    "evidence_count",
    "evidence_fraction",
    "sku_scoped_evidence_count",
    "sku_scoped_evidence_fraction",
    "merchant_scope_evidence_present",
    "product_scope_evidence_present",
    "retrieval_scores_available",
    "max_score",
    "mean_score",
    "min_score",
    "score_margin",
    "lexical_max_score",
    "lexical_mean_score",
    "lexical_min_score",
    "lexical_score_margin",
    "semantic_max_score",
    "semantic_mean_score",
    "semantic_min_score",
    "semantic_score_margin",
    "hybrid_max_score",
    "hybrid_mean_score",
    "hybrid_min_score",
    "hybrid_score_margin",
    "source_kind_count",
    "source_kind_diversity",
    "required_annotation_fraction",
    "relevant_annotation_fraction",
    "constraint_count",
    "constraint_family_purpose",
    "constraint_family_exclusion",
    "case_family_purpose_and_exclusion",
    "case_family_exclusion_only",
    "case_family_purpose_only",
    "case_family_other",
    "evidence_text_kchars_total",
    "evidence_text_kchars_mean",
)

# Explicit role alias: the complete 36-field extractor is retained for
# analysis/artifacts, while model_manifest.MODEL_FEATURE_NAMES is the only
# deployable learning input order.
DIAGNOSTIC_FEATURE_NAMES = FEATURE_NAMES


FEATURE_DEFINITIONS: Mapping[str, str] = MappingProxyType(
    {
        "evidence_count": "Number of trusted evidence items in the subset.",
        "evidence_fraction": "Subset size divided by the eligible evidence size.",
        "sku_scoped_evidence_count": (
            "Subset items that survive the verifier's transaction-SKU scoping."
        ),
        "sku_scoped_evidence_fraction": (
            "SKU-scoped item count divided by the subset size."
        ),
        "merchant_scope_evidence_present": (
            "1.0 when the subset carries a merchant-wide (SKU-null) item."
        ),
        "product_scope_evidence_present": (
            "1.0 when the subset carries an item scoped to the transaction SKU."
        ),
        "retrieval_scores_available": (
            "1.0 when the frozen retrieval surface scores every subset item."
        ),
        "max_score": (
            "Maximum reference ranking (hybrid) score over the subset. The "
            "reference ranking channel is the hybrid channel, so the generic "
            "score statistics are the hybrid_score statistics and are reported "
            "once instead of as duplicate collinear columns."
        ),
        "mean_score": "Mean reference ranking (hybrid) score over the subset.",
        "min_score": "Minimum reference ranking (hybrid) score over the subset.",
        "score_margin": (
            "Reference ranking score gap between the best and second-best "
            "subset item; 0.0 for single-item subsets."
        ),
        "lexical_max_score": "Maximum lexical retrieval score over the subset.",
        "lexical_mean_score": "Mean lexical retrieval score over the subset.",
        "lexical_min_score": "Minimum lexical retrieval score over the subset.",
        "lexical_score_margin": (
            "Lexical score gap between the best and second-best subset item."
        ),
        "semantic_max_score": (
            "Maximum semantic (embedding) retrieval score over the subset."
        ),
        "semantic_mean_score": "Mean semantic retrieval score over the subset.",
        "semantic_min_score": "Minimum semantic retrieval score over the subset.",
        "semantic_score_margin": (
            "Semantic score gap between the best and second-best subset item."
        ),
        "hybrid_max_score": "Maximum INT-2 production-blend score over the subset.",
        "hybrid_mean_score": "Mean INT-2 production-blend score over the subset.",
        "hybrid_min_score": "Minimum INT-2 production-blend score over the subset.",
        "hybrid_score_margin": (
            "Production-blend score gap between the best and second-best item."
        ),
        "source_kind_count": "Distinct evidence source_kind values in the subset.",
        "source_kind_diversity": (
            "Distinct source_kind count divided by the subset size."
        ),
        "required_annotation_fraction": (
            "Fraction of the query's annotated required evidence in the subset."
        ),
        "relevant_annotation_fraction": (
            "Fraction of the query's annotated relevant evidence in the subset."
        ),
        "constraint_count": "Declared semantic constraints on the mandate.",
        "constraint_family_purpose": (
            "1.0 when the mandate declares a purpose-kind constraint."
        ),
        "constraint_family_exclusion": (
            "1.0 when the mandate declares an exclusion-kind constraint."
        ),
        "case_family_purpose_and_exclusion": (
            "1.0 when the case family is PURPOSE_AND_EXCLUSION."
        ),
        "case_family_exclusion_only": (
            "1.0 when the case family is EXCLUSION_ONLY."
        ),
        "case_family_purpose_only": (
            "1.0 when the case family is PURPOSE_ONLY."
        ),
        "case_family_other": "1.0 when the case family is OTHER.",
        "evidence_text_kchars_total": (
            "Total subset evidence text length in thousands of characters."
        ),
        "evidence_text_kchars_mean": (
            "Mean subset evidence text length in thousands of characters."
        ),
    }
)


def assert_no_target_leakage(feature_names: tuple[str, ...]) -> None:
    """Refuse any feature name that names or resembles a post-inference field."""

    if not isinstance(feature_names, tuple) or not feature_names:
        raise Int3ExperimentError("feature_names must be a non-empty tuple")
    if len(feature_names) != len(set(feature_names)):
        raise Int3ExperimentError("feature names must be unique")
    for name in feature_names:
        if not isinstance(name, str) or not _IDENTIFIER_RE.fullmatch(name):
            raise Int3ExperimentError("feature names must be bounded identifiers")
        if name in FORBIDDEN_TARGET_FIELDS:
            raise Int3ExperimentError(
                f"feature {name!r} is a post-inference target field"
            )
        lowered = name.lower()
        for fragment in _LEAKY_SUBSTRINGS:
            if fragment in lowered:
                raise Int3ExperimentError(
                    f"feature {name!r} resembles a post-inference field ({fragment!r})"
                )


assert_no_target_leakage(FEATURE_NAMES)

if frozenset(FEATURE_DEFINITIONS) != frozenset(FEATURE_NAMES):
    raise Int3ExperimentError("every feature must carry an explicit definition")


@dataclass(frozen=True, slots=True)
class EvidenceScoreRecord:
    """One frozen retrieval surface score triple for a single evidence item."""

    evidence_id: str
    lexical_score: float
    semantic_score: float

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_id, str) or not _IDENTIFIER_RE.fullmatch(
            self.evidence_id
        ):
            raise Int3ExperimentError("evidence_id must be a bounded identifier")
        for value, name in (
            (self.lexical_score, "lexical_score"),
            (self.semantic_score, "semantic_score"),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0.0 <= float(value) <= 1.0
            ):
                raise Int3ExperimentError(f"{name} must be within [0, 1]")

    @property
    def reference_score(self) -> float:
        """Blend the two channels exactly as the INT-2 production path does."""

        return (
            REFERENCE_RETRIEVAL_ALPHA * float(self.lexical_score)
            + (1.0 - REFERENCE_RETRIEVAL_ALPHA) * float(self.semantic_score)
        )


@dataclass(frozen=True, slots=True, init=False)
class RetrievalScoreSurface:
    """Immutable per-query retrieval scores read from a frozen Stage-A run."""

    configuration_id: str
    alpha: float
    _scores: Mapping[str, Mapping[str, EvidenceScoreRecord]]

    def __init__(
        self,
        *,
        configuration_id: str,
        alpha: float,
        scores: Mapping[str, Mapping[str, EvidenceScoreRecord]],
    ) -> None:
        if not isinstance(configuration_id, str) or not configuration_id:
            raise Int3ExperimentError("configuration_id must be non-empty")
        if (
            isinstance(alpha, bool)
            or not isinstance(alpha, (int, float))
            or not 0.0 <= float(alpha) <= 1.0
        ):
            raise Int3ExperimentError("alpha must be within [0, 1]")
        if not isinstance(scores, Mapping) or not scores:
            raise Int3ExperimentError("scores must be a non-empty mapping")
        frozen: dict[str, Mapping[str, EvidenceScoreRecord]] = {}
        for query_id, records in scores.items():
            if not isinstance(query_id, str) or not query_id:
                raise Int3ExperimentError("score query IDs must be non-empty")
            if not isinstance(records, Mapping) or not records:
                raise Int3ExperimentError("each query needs a non-empty score map")
            inner: dict[str, EvidenceScoreRecord] = {}
            for evidence_id, record in records.items():
                if not isinstance(record, EvidenceScoreRecord):
                    raise Int3ExperimentError("scores must be EvidenceScoreRecord")
                if record.evidence_id != evidence_id:
                    raise Int3ExperimentError("score key must match its evidence ID")
                inner[evidence_id] = record
            frozen[query_id] = MappingProxyType(inner)
        object.__setattr__(self, "configuration_id", configuration_id)
        object.__setattr__(self, "alpha", float(alpha))
        object.__setattr__(self, "_scores", MappingProxyType(frozen))

    def records_for(
        self, *, query_id: str, evidence_ids: tuple[str, ...]
    ) -> tuple[EvidenceScoreRecord, ...] | None:
        """Return scores for every requested item, or None when incomplete."""

        records = self._scores.get(query_id)
        if records is None:
            return None
        resolved: list[EvidenceScoreRecord] = []
        for evidence_id in evidence_ids:
            record = records.get(evidence_id)
            if record is None:
                return None
            resolved.append(record)
        return tuple(resolved)

    @property
    def query_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._scores))


@dataclass(frozen=True, slots=True)
class SubsetFeatureInput:
    """Exactly the pre-inference facts the extractor is allowed to read.

    Constructing this type is the leakage boundary: it has no field that can
    carry a semantic verdict, a final action, an engineering expectation, or
    the full-evidence reference result, so an extractor that only reads this
    object cannot see the target even by accident.
    """

    query_id: str
    eligible_evidence: tuple[SemanticEvidenceEntry, ...]
    subset_evidence: tuple[SemanticEvidenceEntry, ...]
    transaction_skus: tuple[str, ...]
    constraint_kinds: tuple[str, ...]
    required_evidence_ids: tuple[str, ...]
    relevant_evidence_ids: tuple[str, ...]
    score_surface: RetrievalScoreSurface | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.query_id, str) or not self.query_id:
            raise Int3ExperimentError("query_id must be non-empty")
        for values, name, nonempty in (
            (self.eligible_evidence, "eligible_evidence", True),
            (self.subset_evidence, "subset_evidence", True),
        ):
            if not isinstance(values, tuple) or (nonempty and not values):
                raise Int3ExperimentError(f"{name} must be a non-empty tuple")
            if not all(isinstance(item, SemanticEvidenceEntry) for item in values):
                raise Int3ExperimentError(f"{name} contains an invalid entry")
        eligible_ids = [item.evidence_id for item in self.eligible_evidence]
        if len(eligible_ids) != len(set(eligible_ids)):
            raise Int3ExperimentError("eligible evidence IDs must be unique")
        subset_ids = [item.evidence_id for item in self.subset_evidence]
        if len(subset_ids) != len(set(subset_ids)):
            raise Int3ExperimentError("subset evidence IDs must be unique")
        if not set(subset_ids).issubset(eligible_ids):
            raise Int3ExperimentError("subset evidence must be eligible evidence")
        if not isinstance(self.transaction_skus, tuple) or not self.transaction_skus:
            raise Int3ExperimentError("transaction_skus must be a non-empty tuple")
        if not all(
            isinstance(item, str) and item for item in self.transaction_skus
        ):
            raise Int3ExperimentError("transaction_skus must contain non-empty strings")
        case_family_for_constraint_kinds(self.constraint_kinds)
        for values, name in (
            (self.required_evidence_ids, "required_evidence_ids"),
            (self.relevant_evidence_ids, "relevant_evidence_ids"),
        ):
            if not isinstance(values, tuple):
                raise Int3ExperimentError(f"{name} must be a tuple")
            if not all(isinstance(item, str) and item for item in values):
                raise Int3ExperimentError(f"{name} must contain non-empty strings")
            if len(values) != len(set(values)):
                raise Int3ExperimentError(f"{name} must be unique")
        if not set(self.required_evidence_ids).issubset(self.relevant_evidence_ids):
            raise Int3ExperimentError(
                "required evidence must be a subset of relevant evidence"
            )
        if self.score_surface is not None and not isinstance(
            self.score_surface, RetrievalScoreSurface
        ):
            raise Int3ExperimentError(
                "score_surface must be RetrievalScoreSurface or None"
            )

    @property
    def case_family(self) -> CaseFamily:
        return case_family_for_constraint_kinds(self.constraint_kinds)


def _statistics(values: tuple[float, ...]) -> tuple[float, float, float, float]:
    """Return (max, mean, min, margin) for a non-empty score channel."""

    ordered = sorted(values, reverse=True)
    maximum = ordered[0]
    minimum = ordered[-1]
    mean = sum(values) / len(values)
    margin = ordered[0] - ordered[1] if len(ordered) > 1 else 0.0
    return maximum, mean, minimum, margin


def extract_subset_features(value: SubsetFeatureInput) -> Mapping[str, float]:
    """Compute the frozen pre-inference feature mapping for one subset."""

    if not isinstance(value, SubsetFeatureInput):
        raise TypeError("value must be SubsetFeatureInput")

    subset = value.subset_evidence
    subset_size = len(subset)
    eligible_size = len(value.eligible_evidence)
    subset_ids = tuple(item.evidence_id for item in subset)
    skus = frozenset(value.transaction_skus)

    sku_scoped = tuple(
        item for item in subset if item.sku is None or item.sku in skus
    )
    merchant_scope_present = any(item.sku is None for item in subset)
    product_scope_present = any(
        item.sku is not None and item.sku in skus for item in subset
    )

    records = (
        None
        if value.score_surface is None
        else value.score_surface.records_for(
            query_id=value.query_id, evidence_ids=subset_ids
        )
    )
    if records is None:
        scores_available = 0.0
        reference_stats = (0.0, 0.0, 0.0, 0.0)
        lexical_stats = (0.0, 0.0, 0.0, 0.0)
        semantic_stats = (0.0, 0.0, 0.0, 0.0)
    else:
        scores_available = 1.0
        reference_stats = _statistics(
            tuple(item.reference_score for item in records)
        )
        lexical_stats = _statistics(
            tuple(float(item.lexical_score) for item in records)
        )
        semantic_stats = _statistics(
            tuple(float(item.semantic_score) for item in records)
        )

    source_kinds = {item.source_kind for item in subset}
    required = frozenset(value.required_evidence_ids)
    relevant = frozenset(value.relevant_evidence_ids)
    selected = frozenset(subset_ids)
    constraint_kinds = frozenset(value.constraint_kinds)
    family = value.case_family
    characters = tuple(float(len(item.text)) for item in subset)

    features = {
        "evidence_count": float(subset_size),
        "evidence_fraction": float(subset_size) / float(eligible_size),
        "sku_scoped_evidence_count": float(len(sku_scoped)),
        "sku_scoped_evidence_fraction": float(len(sku_scoped)) / float(subset_size),
        "merchant_scope_evidence_present": 1.0 if merchant_scope_present else 0.0,
        "product_scope_evidence_present": 1.0 if product_scope_present else 0.0,
        "retrieval_scores_available": scores_available,
        "max_score": reference_stats[0],
        "mean_score": reference_stats[1],
        "min_score": reference_stats[2],
        "score_margin": reference_stats[3],
        "lexical_max_score": lexical_stats[0],
        "lexical_mean_score": lexical_stats[1],
        "lexical_min_score": lexical_stats[2],
        "lexical_score_margin": lexical_stats[3],
        "semantic_max_score": semantic_stats[0],
        "semantic_mean_score": semantic_stats[1],
        "semantic_min_score": semantic_stats[2],
        "semantic_score_margin": semantic_stats[3],
        # These explicit names make the requested lexical/semantic/hybrid
        # channels independently discoverable.  The generic score fields above
        # intentionally alias this frozen production hybrid channel.
        "hybrid_max_score": reference_stats[0],
        "hybrid_mean_score": reference_stats[1],
        "hybrid_min_score": reference_stats[2],
        "hybrid_score_margin": reference_stats[3],
        "source_kind_count": float(len(source_kinds)),
        "source_kind_diversity": float(len(source_kinds)) / float(subset_size),
        "required_annotation_fraction": (
            float(len(selected & required)) / float(len(required))
            if required
            else 0.0
        ),
        "relevant_annotation_fraction": (
            float(len(selected & relevant)) / float(len(relevant))
            if relevant
            else 0.0
        ),
        "constraint_count": float(len(value.constraint_kinds)),
        "constraint_family_purpose": 1.0 if "purpose" in constraint_kinds else 0.0,
        "constraint_family_exclusion": (
            1.0 if "exclusion" in constraint_kinds else 0.0
        ),
        "case_family_purpose_and_exclusion": (
            1.0 if family is CaseFamily.PURPOSE_AND_EXCLUSION else 0.0
        ),
        "case_family_exclusion_only": (
            1.0 if family is CaseFamily.EXCLUSION_ONLY else 0.0
        ),
        "case_family_purpose_only": (
            1.0 if family is CaseFamily.PURPOSE_ONLY else 0.0
        ),
        "case_family_other": 1.0 if family is CaseFamily.OTHER else 0.0,
        "evidence_text_kchars_total": sum(characters) / 1000.0,
        "evidence_text_kchars_mean": (sum(characters) / len(characters)) / 1000.0,
    }
    if frozenset(features) != frozenset(FEATURE_NAMES):
        raise Int3ExperimentError("extractor produced an unexpected feature set")
    for name, computed in features.items():
        if isinstance(computed, bool) or not math.isfinite(float(computed)):
            raise Int3ExperimentError(f"feature {name} must be a finite number")
    return MappingProxyType({name: float(features[name]) for name in FEATURE_NAMES})


def feature_vector(features: Mapping[str, float]) -> tuple[float, ...]:
    """Order a feature mapping into the single frozen model input order."""

    if not isinstance(features, Mapping):
        raise TypeError("features must be a mapping")
    if frozenset(features) != frozenset(FEATURE_NAMES):
        raise Int3ExperimentError("features must cover exactly FEATURE_NAMES")
    return tuple(float(features[name]) for name in FEATURE_NAMES)
