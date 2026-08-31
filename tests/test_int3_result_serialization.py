from __future__ import annotations

import json

import pytest

from mandateguard.engineering.int3.result_serialization import (
    ResultArtifactSerializationError,
    result_artifact_json_bytes,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (12.5, 12.5),
        (-12.5, -12.5),
        (0.0, 0.0),
        (-0.0, 0.0),
        (5e-324, 5e-324),
        (1.7976931348623157e308, 1.7976931348623157e308),
    ],
)
def test_result_artifact_serializes_finite_floats_without_rounding(
    value: float, expected: float
) -> None:
    encoded = result_artifact_json_bytes({"value": value})

    assert json.loads(encoded) == {"value": expected}
    assert result_artifact_json_bytes(json.loads(encoded)) == encoded


def test_result_artifact_normalizes_negative_zero() -> None:
    assert result_artifact_json_bytes({"value": -0.0}) == b'{"value":0.0}'


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_result_artifact_rejects_non_finite_floats(value: float) -> None:
    with pytest.raises(ResultArtifactSerializationError, match="non-finite float"):
        result_artifact_json_bytes({"nested": [value]})


def test_result_artifact_serialization_is_byte_identical_and_key_sorted() -> None:
    first = {"z": [0.25, -7.0], "a": {"small": 5e-324, "zero": -0.0}}
    second = {"a": {"zero": 0.0, "small": 5e-324}, "z": [0.25, -7.0]}

    encoded = result_artifact_json_bytes(first)

    assert result_artifact_json_bytes(first) == encoded
    assert result_artifact_json_bytes(second) == encoded
    assert encoded == (
        b'{"a":{"small":5e-324,"zero":0.0},"z":[0.25,-7.0]}'
    )


def test_representative_int3_model_feature_result_row_serializes() -> None:
    row = {
        "observation_id": "INT3:INT2-Q-STUDYGLOW:m1000",
        "model_features": {
            "constraint_count": 2.0,
            "constraint_family_exclusion": 1.0,
            "constraint_family_purpose": 1.0,
            "evidence_count": 1.0,
            "evidence_fraction": 0.25,
            "evidence_text_kchars_mean": 0.143,
            "max_score": 0.27940802904666096,
            "mean_score": 0.27940802904666096,
            "merchant_scope_evidence_present": 1.0,
            "product_scope_evidence_present": 0.0,
            "score_margin": 0.0,
            "sku_scoped_evidence_fraction": 1.0,
            "source_kind_count": 1.0,
            "source_kind_diversity": 1.0,
        },
        "decision_stable": False,
    }

    encoded = result_artifact_json_bytes(row)

    assert json.loads(encoded) == row
    assert result_artifact_json_bytes(row) == encoded
