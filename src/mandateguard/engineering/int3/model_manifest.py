"""Preregistered runtime feature set and model pipeline for INT-3.

This module is intentionally label-free.  The ordered feature list, runtime
derivations, and pipeline hyperparameters were frozen at the expected INT-3A
base commit before any subset semantic execution or stability label existed.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from mandateguard.core.hashing import sha256_canonical
from mandateguard.engineering.int3.features import FEATURE_NAMES
from mandateguard.engineering.int3.models import Int3ExperimentError


MODEL_FEATURE_MANIFEST_SCHEMA_VERSION = "1.0"
MODEL_FEATURE_MANIFEST_CREATED_AT = "2026-08-31T03:52:24.870960Z"
MODEL_FEATURE_MANIFEST_BASE_COMMIT = (
    "7fe60059d857399b4a40a0d85317459d00c3f7ec"
)

# Fourteen compact, non-oracle, runtime-derived inputs.  Their order is the
# model matrix order and is committed by MODEL_FEATURE_MANIFEST_SHA256.
MODEL_FEATURE_NAMES: tuple[str, ...] = (
    "evidence_count",
    "evidence_fraction",
    "sku_scoped_evidence_fraction",
    "merchant_scope_evidence_present",
    "product_scope_evidence_present",
    "max_score",
    "mean_score",
    "score_margin",
    "source_kind_count",
    "source_kind_diversity",
    "constraint_count",
    "constraint_family_purpose",
    "constraint_family_exclusion",
    "evidence_text_kchars_mean",
)

ORACLE_DIAGNOSTIC_ONLY_FEATURE_NAMES: tuple[str, ...] = (
    "required_annotation_fraction",
    "relevant_annotation_fraction",
)

DIAGNOSTIC_ONLY_FEATURE_NAMES: tuple[str, ...] = tuple(
    name for name in FEATURE_NAMES if name not in MODEL_FEATURE_NAMES
)

_RUNTIME_DERIVATIONS: Mapping[str, str] = MappingProxyType(
    {
        "evidence_count": "count of trusted evidence items currently held",
        "evidence_fraction": "current count divided by eligible trusted-evidence count",
        "sku_scoped_evidence_fraction": (
            "fraction surviving transaction-SKU scoping using runtime evidence metadata"
        ),
        "merchant_scope_evidence_present": (
            "whether current evidence contains a runtime SKU-null merchant item"
        ),
        "product_scope_evidence_present": (
            "whether current evidence contains an item matching a transaction SKU"
        ),
        "max_score": "maximum fixed production retrieval score in current evidence",
        "mean_score": "mean fixed production retrieval score in current evidence",
        "score_margin": "top minus second fixed production retrieval score",
        "source_kind_count": "distinct runtime evidence source_kind count",
        "source_kind_diversity": "source_kind count divided by evidence count",
        "constraint_count": "count of semantic constraints in the runtime mandate",
        "constraint_family_purpose": (
            "indicator derived from kind=purpose in runtime mandate constraints"
        ),
        "constraint_family_exclusion": (
            "indicator derived from kind=exclusion in runtime mandate constraints"
        ),
        "evidence_text_kchars_mean": "mean runtime evidence text length in kchars",
    }
)

# Values are represented without JSON floating-point ambiguity so the project
# canonical SHA implementation can commit this preregistration exactly.
MODEL_PIPELINE_SPEC: Mapping[str, object] = MappingProxyType(
    {
        "steps": ("StandardScaler", "LogisticRegression"),
        "standard_scaler": MappingProxyType(
            {
                "with_mean": True,
                "with_std": True,
            }
        ),
        "logistic_regression": MappingProxyType(
            {
                "regularization": "L2",
                "C": 1,
                "solver": "lbfgs",
                "max_iter": 2000,
                "fit_intercept": True,
                "random_state": 0,
                "class_weight": None,
                "tolerance_decimal": "0.0001",
            }
        ),
        "hyperparameter_tuning_after_labels": False,
    }
)


def _plain_pipeline_spec() -> dict[str, object]:
    return {
        "steps": list(MODEL_PIPELINE_SPEC["steps"]),
        "standard_scaler": dict(MODEL_PIPELINE_SPEC["standard_scaler"]),
        "logistic_regression": dict(
            MODEL_PIPELINE_SPEC["logistic_regression"]
        ),
        "hyperparameter_tuning_after_labels": False,
    }


def model_feature_manifest_payload() -> dict[str, object]:
    """Return a fresh canonical payload, excluding its self-hash field."""

    return {
        "schema_version": MODEL_FEATURE_MANIFEST_SCHEMA_VERSION,
        "status": "FROZEN_BEFORE_INT3_SUBSET_LABELS",
        "created_at": MODEL_FEATURE_MANIFEST_CREATED_AT,
        "base_commit": MODEL_FEATURE_MANIFEST_BASE_COMMIT,
        "diagnostic_feature_count": len(FEATURE_NAMES),
        "model_feature_count": len(MODEL_FEATURE_NAMES),
        "ordered_model_features": [
            {
                "position": index,
                "name": name,
                "availability": "RUNTIME_PRE_SEMANTIC_INFERENCE",
                "derivation": _RUNTIME_DERIVATIONS[name],
            }
            for index, name in enumerate(MODEL_FEATURE_NAMES)
        ],
        "diagnostic_only_features": list(DIAGNOSTIC_ONLY_FEATURE_NAMES),
        "oracle_diagnostic_only_features": list(
            ORACLE_DIAGNOSTIC_ONLY_FEATURE_NAMES
        ),
        "case_family_note": (
            "Case-family diagnostics are derived only from runtime mandate "
            "semantic constraint kinds, never query/case identity; they are "
            "excluded from the compact model as redundant with the purpose/"
            "exclusion indicators."
        ),
        "pipeline": _plain_pipeline_spec(),
        "target": {
            "column": "decision_stable",
            "terminology": "SINGLE_EXECUTION_ACTION_STABILITY",
            "definition": (
                "subset_final_action == frozen_full_evidence_final_action"
            ),
        },
    }


MODEL_FEATURE_MANIFEST_SHA256 = sha256_canonical(
    model_feature_manifest_payload()
)

MODEL_FEATURE_MANIFEST: Mapping[str, object] = MappingProxyType(
    {
        **model_feature_manifest_payload(),
        "manifest_sha256": MODEL_FEATURE_MANIFEST_SHA256,
    }
)


def model_feature_vector(features: Mapping[str, float]) -> tuple[float, ...]:
    """Select only preregistered deployable inputs from diagnostic features."""

    if not isinstance(features, Mapping):
        raise TypeError("features must be a mapping")
    missing = tuple(name for name in MODEL_FEATURE_NAMES if name not in features)
    if missing:
        raise Int3ExperimentError(
            "features lack preregistered model inputs: " + ",".join(missing)
        )
    return tuple(float(features[name]) for name in MODEL_FEATURE_NAMES)


if not set(MODEL_FEATURE_NAMES).issubset(FEATURE_NAMES):
    raise Int3ExperimentError("model features must be diagnostic extractor outputs")
if set(MODEL_FEATURE_NAMES) & set(ORACLE_DIAGNOSTIC_ONLY_FEATURE_NAMES):
    raise Int3ExperimentError("oracle annotations may not enter the model manifest")
if len(MODEL_FEATURE_NAMES) != len(set(MODEL_FEATURE_NAMES)):
    raise Int3ExperimentError("model feature names must be unique")
