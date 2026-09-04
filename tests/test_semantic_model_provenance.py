"""Every evaluated encoder must be identified, licensed, and pinned.

A pretrained model is somebody else's artifact under somebody else's terms. The
rule for this milestone is the same one the dataset got: the licence is recorded
from the authoritative source, never assumed, and the exact revision is pinned so
"we evaluated MiniLM" means one specific set of bytes.

The evaluation is bound to the frozen query set the same way an index is bound to
its catalog: by digest, checked, refused on mismatch.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROVENANCE_DIR = REPOSITORY_ROOT / "data" / "provenance" / "semantic-models"
FREEZE_PATH = REPOSITORY_ROOT / "data" / "eval" / "semantic-v2" / "FREEZE.json"
QUERIES_PATH = REPOSITORY_ROOT / "data" / "eval" / "semantic-v2" / "queries.json"
EVALUATION_PATH = (
    REPOSITORY_ROOT / "artifacts" / "engineering" / "semantic-v2" / "evaluation.json"
)

#: Verified against the Hugging Face model API for each pinned revision. These
#: are permissive, redistributable licences; anything else would need a decision
#: rather than a record.
EXPECTED_LICENSES = {
    "sentence-transformers/all-MiniLM-L6-v2": "apache-2.0",
    "BAAI/bge-small-en-v1.5": "mit",
    "intfloat/e5-small-v2": "mit",
}

PROVENANCE_FILES = sorted(PROVENANCE_DIR.glob("*.json")) if PROVENANCE_DIR.exists() else []


def _records() -> list[dict]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in PROVENANCE_FILES]


def test_a_provenance_record_exists_for_every_candidate() -> None:
    assert PROVENANCE_FILES, "no semantic model provenance was committed"
    recorded = {record["model_id"] for record in _records()}
    assert recorded == set(EXPECTED_LICENSES)


@pytest.mark.parametrize("record", _records(), ids=lambda r: r["model_id"])
def test_every_record_pins_the_fields_a_reader_needs(record: dict) -> None:
    for field in (
        "model_id",
        "revision",
        "license",
        "dimension",
        "maximum_sequence_length",
        "tokenizer",
        "intended_usage",
        "model_card_url",
        "license_source",
        "model_sha256",
    ):
        assert record.get(field), f"{record['model_id']} is missing {field}"
    # A revision must be a full commit sha, not a branch name.
    assert len(record["revision"]) == 40
    assert set(record["revision"]) <= set("0123456789abcdef")
    assert len(record["model_sha256"]) == 64


@pytest.mark.parametrize("record", _records(), ids=lambda r: r["model_id"])
def test_the_recorded_licence_matches_the_authoritative_source(record: dict) -> None:
    """Recorded, then checked against what the publisher actually declares."""

    assert record["license"].lower() == EXPECTED_LICENSES[record["model_id"]]
    assert record["verified_before_evaluation"] is True
    assert record["revision"] in record["license_source"]


@pytest.mark.parametrize("record", _records(), ids=lambda r: r["model_id"])
def test_the_tokenizer_identity_is_pinned_by_digest(record: dict) -> None:
    identity = record["tokenizer_identity"]
    assert identity["description"]
    assert len(identity["tokenizer_json_sha256"]) == 64


@pytest.mark.parametrize("record", _records(), ids=lambda r: r["model_id"])
def test_the_embedding_dimension_is_plausible_and_declared(record: dict) -> None:
    assert record["dimension"] == 384
    assert 128 <= record["maximum_sequence_length"] <= 8192


# --------------------------------------------------------------------------
# The evaluation is bound to the freeze
# --------------------------------------------------------------------------


def _freeze() -> dict:
    return json.loads(FREEZE_PATH.read_text(encoding="utf-8"))


def test_the_freeze_contains_no_outcomes() -> None:
    freeze = _freeze()
    assert freeze["outcomes_included"] is False
    assert freeze["status"] == "FROZEN_BEFORE_MEASUREMENT"
    # The metric *names* are frozen; their values must not be.
    payload = json.loads(FREEZE_PATH.read_text(encoding="utf-8"))
    assert payload["metrics"] == [
        "recall_at_1",
        "recall_at_5",
        "recall_at_10",
        "mrr",
        "ndcg_at_10",
    ]
    for banned in ("results", "outcomes", "winner", "selected_model", "scores"):
        assert banned not in payload, f"the freeze leaks an outcome key: {banned}"
    queries = json.loads(QUERIES_PATH.read_text(encoding="utf-8"))
    assert queries["outcomes_included"] is False
    assert queries["authored_before_model_evaluation"] is True
    for query in queries["queries"]:
        for banned in ("expected_documents", "gold_ranking", "recall", "score"):
            assert banned not in query, f"{query['query_id']} leaks {banned}"


def test_the_query_artifact_matches_its_frozen_digest() -> None:
    live = sha256(QUERIES_PATH.read_bytes()).hexdigest()
    assert live == _freeze()["query_artifact_sha256"]


def test_the_frozen_query_set_is_large_enough_to_mean_something() -> None:
    freeze = _freeze()
    queries = json.loads(QUERIES_PATH.read_text(encoding="utf-8"))["queries"]
    assert len(queries) == freeze["query_count"] >= 100
    # Literal and paraphrase are balanced, so the paraphrase headline is not a
    # handful of queries.
    classes = [q["intent_class"] for q in queries]
    assert classes.count("literal") == freeze["literal_count"] >= 50
    assert classes.count("paraphrase") == freeze["paraphrase_count"] >= 50
    # Every query carries its own relevance predicate and slice memberships, so
    # the report can be sliced without re-authoring anything after the fact.
    for query in queries:
        assert query["relevance"]
        assert query["groups"]
        assert query["intent_class"] in {"literal", "paraphrase"}


def test_the_freeze_states_the_model_selection_rule_before_any_result() -> None:
    rule = _freeze()["model_selection_rule"]
    assert rule["primary"]
    assert rule["materiality_threshold"]
    assert rule["no_repeated_test_tuning"] is True
    thresholds = rule["runtime_thresholds"]
    assert thresholds["runtime_external_model_calls_max"] == 0
    assert thresholds["warm_full_discovery_p95_ms_max"] > 0


def test_the_evaluation_is_bound_to_the_frozen_query_set() -> None:
    if not EVALUATION_PATH.exists():
        pytest.skip("the semantic evaluation has not been run")
    report = json.loads(EVALUATION_PATH.read_text(encoding="utf-8"))
    freeze = _freeze()
    assert report["query_artifact_sha256"] == freeze["query_artifact_sha256"]
    assert report["freeze_payload_sha256"] == freeze["freeze_payload_sha256"]
    catalog = REPOSITORY_ROOT / "data" / "processed" / "discovery_catalog.jsonl.gz"
    if catalog.exists():
        assert report["catalog_sha256"] == sha256(catalog.read_bytes()).hexdigest()


def test_every_evaluated_model_has_a_committed_provenance_record() -> None:
    if not EVALUATION_PATH.exists():
        pytest.skip("the semantic evaluation has not been run")
    report = json.loads(EVALUATION_PATH.read_text(encoding="utf-8"))
    recorded = {record["model_id"]: record for record in _records()}
    for candidate in report["candidates"]:
        record = recorded.get(candidate["model_id"])
        assert record is not None, candidate["model_id"]
        # The bytes evaluated are the bytes recorded.
        assert candidate["revision"] == record["revision"]
        assert candidate["model_sha256"] == record["model_sha256"]
        assert candidate["dimension"] == record["dimension"]


def test_every_frozen_configuration_and_slice_was_reported() -> None:
    if not EVALUATION_PATH.exists():
        pytest.skip("the semantic evaluation has not been run")
    report = json.loads(EVALUATION_PATH.read_text(encoding="utf-8"))
    freeze = _freeze()
    for candidate in report["candidates"]:
        assert set(candidate["configurations"]) == set(freeze["fusion_candidates"])
        for block in candidate["configurations"].values():
            assert set(block["slices"]) == set(freeze["report_slices"])
            for slice_metrics in block["slices"].values():
                assert set(freeze["metrics"]) <= set(slice_metrics)


def test_the_evaluation_used_the_frozen_sequence_length_and_representation() -> None:
    if not EVALUATION_PATH.exists():
        pytest.skip("the semantic evaluation has not been run")
    report = json.loads(EVALUATION_PATH.read_text(encoding="utf-8"))
    frozen_length = _freeze()["dense_document_representation"]["maximum_sequence_length"]
    for candidate in report["candidates"]:
        assert candidate["evaluation_sequence_length"] == frozen_length
        assert candidate["normalization"] == "L2"
