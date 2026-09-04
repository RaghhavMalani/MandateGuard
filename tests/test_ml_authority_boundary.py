"""The permanent invariant: ML understands, it never authorizes.

This milestone added a pretrained sentence encoder to the discovery side. That
is a new class of model output, and a new model output is a new opportunity for
something advisory to be mistaken for something authoritative.

The rule, stated once:

    ML MAY   retrieve, rank, classify, detect mismatch, explain, prioritize
             evidence.

    ML MAY NOT create trusted evidence, convert REVIEW to ALLOW, override BLOCK,
             issue a capability, override exact merchant/SKU identity, override a
             budget, override revocation, override replay protection, or execute
             a provider call.

These tests hold that line structurally rather than by assertion. The point is
not that today's code behaves; it is that a future change which gives a model
authority has to delete a test to do it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src" / "mandateguard"

#: Every module that produces, loads, or serves a model output.
ML_MODULES = (
    "discovery/classifier.py",
    "discovery/mismatch.py",
    "discovery/anomaly.py",
    "discovery/search.py",
    "discovery/index/embedding.py",
    "discovery/index/hybrid.py",
    "ml/semantic_v2_eval.py",
    "ml/retrieval_eval.py",
    "ml/classifier_train.py",
    "ml/anomaly_eval.py",
    "ml/embedding_train.py",
)

#: Names that mint authority. A model module that can reach one of these can, in
#: principle, be argued into using it.
AUTHORITY_NAMES = (
    "issue_execution_authorization",
    "validate_and_reserve_execution",
    "execute_razorpay_order",
    "build_razorpay_order_request",
    "HMACSHA256Signer",
    "authorize_transaction",
    "evaluate_tier_a",
    "evaluate_tier_b",
    "MandateStateRegistry",
    "TrustedExecutionConfig",
    "SignedExecutionAuthorization",
    "RazorpayTestOrdersAdapter",
)

AUTHORITY_MODULE_PREFIXES = (
    "mandateguard.execution",
    "mandateguard.policy",
    "mandateguard.semantic.orchestration",
)


def _existing(relative: str) -> Path | None:
    path = SOURCE_ROOT / relative
    return path if path.exists() else None


@pytest.mark.parametrize("relative", ML_MODULES)
def test_no_ml_module_imports_the_money_path(relative: str) -> None:
    """Structural: a model module cannot even name the authorization path."""

    path = _existing(relative)
    if path is None:
        pytest.skip(f"{relative} is not present in this checkout")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith(AUTHORITY_MODULE_PREFIXES):
                offenders.append(f"from {node.module}")
            for alias in node.names:
                if alias.name in AUTHORITY_NAMES:
                    offenders.append(f"from {node.module} import {alias.name}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(AUTHORITY_MODULE_PREFIXES):
                    offenders.append(f"import {alias.name}")
    assert not offenders, f"{relative} reaches the money path: {offenders}"


@pytest.mark.parametrize("relative", ML_MODULES)
def test_no_ml_module_can_name_a_decision_action(relative: str) -> None:
    """ALLOW is not a value a retrieval or classification module may produce."""

    path = _existing(relative)
    if path is None:
        pytest.skip(f"{relative} is not present in this checkout")
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "models.decision" not in node.module, (
                f"{relative} imports DecisionAction"
            )
            for alias in node.names:
                assert alias.name != "DecisionAction", f"{relative} imports DecisionAction"


def test_the_semantic_encoder_never_reaches_the_execution_path() -> None:
    """The new pretrained model is held to the same line as the old ones."""

    path = _existing("ml/semantic_v2_eval.py")
    if path is None:
        pytest.skip("the semantic v2 evaluator is not present in this checkout")
    source = path.read_text(encoding="utf-8")
    for banned in (
        "issue_execution_authorization",
        "execute_razorpay_order",
        "validate_and_reserve_execution",
        "DecisionAction",
        "TrustedCommerceStore",
        "SemanticEvidence",
        "capability",
    ):
        assert banned not in source, (
            f"the semantic evaluator references {banned}; a retrieval model must "
            "not touch trust or execution"
        )


def test_the_authorization_scale_benchmark_uses_no_model_output() -> None:
    """Authorization is measured without a single learned signal in the loop."""

    path = (
        SOURCE_ROOT / "engineering" / "authscale" / "benchmark.py"
    )
    source = path.read_text(encoding="utf-8")
    for banned in (
        "CategoryClassifier",
        "EmbeddingIndex",
        "HybridDiscoveryRetriever",
        "semantic_v2",
        "onnxruntime",
        "DiscoveryEngine",
    ):
        assert banned not in source, f"the benchmark references {banned}"


def test_the_boundary_declaration_enumerates_what_ml_may_not_do() -> None:
    """The prohibition is an explicit list, not a sentence someone can soften."""

    from mandateguard.discovery.trust import boundary_declaration

    declaration = boundary_declaration()
    forbidden = set(declaration["ml_may_not"])
    # Every clause of the milestone invariant, named.
    assert {
        "ISSUE_EXECUTION_CAPABILITY",
        "OVERRIDE_DETERMINISTIC_BLOCK",
        "SATISFY_MISSING_TRUSTED_EVIDENCE",
        "OVERRIDE_REVOCATION",
        "OVERRIDE_REQUEST_BINDING",
        "AUTHORIZE_PAYMENT",
    } <= forbidden
    # Nothing on the permitted list is an authority.
    permitted = set(declaration["ml_may"])
    assert permitted <= {
        "RETRIEVE",
        "RANK",
        "CLASSIFY",
        "DETECT_ANOMALY",
        "SUGGEST_EVIDENCE_GAP",
        "EXPLAIN",
    }
    assert not permitted & forbidden
    assert declaration["discovery_catalog_is_trusted_evidence"] is False
    assert declaration["authoritative_component"] == (
        "EXISTING_FROZEN_MANDATEGUARD_CONTROLLER"
    )
    assert "controls money" in declaration["statement"]


def test_a_retrieval_score_cannot_be_read_as_a_decision() -> None:
    """RetrievalOutcome carries scores and no action field of any kind."""

    from mandateguard.discovery.index.hybrid import RetrievalOutcome, RetrievedListing

    for cls in (RetrievalOutcome, RetrievedListing):
        fields = set(getattr(cls, "__annotations__", {}))
        for forbidden in ("action", "decision", "allow", "authorized", "capability"):
            assert not any(forbidden in name.lower() for name in fields), (
                f"{cls.__name__} exposes a {forbidden}-shaped field"
            )


def test_the_classifier_declares_no_authorization_authority() -> None:
    import json

    report = REPOSITORY_ROOT / "data" / "models" / "training_report.json"
    if not report.exists():
        pytest.skip("the models are not built in this checkout")
    classifier = json.loads(report.read_text(encoding="utf-8"))["category_classifier"]
    assert classifier["advisory_only"] is True
    assert classifier["authorization_authority"] == "NONE"


def test_the_anomaly_evaluation_states_the_advisory_boundary() -> None:
    import json

    path = (
        REPOSITORY_ROOT / "artifacts" / "engineering" / "discovery"
        / "anomaly_evaluation.json"
    )
    if not path.exists():
        pytest.skip("the anomaly evaluation is not built in this checkout")
    boundary = json.loads(path.read_text(encoding="utf-8"))["advisory_boundary"]
    for phrase in ("cannot ALLOW", "cannot issue a capability", "override"):
        assert phrase in boundary


def test_the_semantic_evaluation_records_zero_external_model_calls() -> None:
    """A retrieval model that phoned home would also be a runtime dependency."""

    import json

    path = (
        REPOSITORY_ROOT / "artifacts" / "engineering" / "semantic-v2" / "evaluation.json"
    )
    if not path.exists():
        pytest.skip("the semantic evaluation is not built in this checkout")
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["external_calls_during_inference"] == {
        "openai": 0,
        "hugging_face_api": 0,
        "razorpay_http": 0,
    }
    for candidate in report["candidates"]:
        assert candidate["external_model_calls"] == 0
