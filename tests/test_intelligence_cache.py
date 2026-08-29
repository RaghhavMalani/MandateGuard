from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import sqlite3

import pytest

from mandateguard.intelligence.cache import SQLiteSemanticCache
from mandateguard.intelligence.models import CacheStatus
from mandateguard.semantic.cache import (
    SemanticCacheIntegrityError,
    SemanticCacheRecord,
)
from mandateguard.semantic.models import (
    ConstraintResult,
    ConstraintStatus,
    NormalizedSemanticOutput,
    semantic_input_sha256,
    semantic_output_sha256,
)
from mandateguard.semantic.verifier import SemanticMode, SemanticVerifier
from tests.intelligence_factories import ALLOW_INTENT, run_offline
from tests.semantic_factories import (
    ScriptedSemanticModel,
    make_semantic_evidence,
    model_output,
    valid_authorization_inputs,
)


def _request():
    inputs = valid_authorization_inputs()
    model = ScriptedSemanticModel(response=model_output("PASS", "PASS"))
    verifier = SemanticVerifier(model=model, cache=SQLiteSemanticCache(":memory:"))
    request = verifier.make_request(
        mandate=inputs["mandate"],
        transaction=inputs["transaction"],
        catalog_snapshot=inputs["catalog_snapshot"],
        semantic_evidence=make_semantic_evidence(),
    )
    verifier.cache.close()
    return request


def _record(request) -> SemanticCacheRecord:
    output = NormalizedSemanticOutput(
        constraint_results=tuple(
            ConstraintResult(
                constraint_id=constraint.constraint_id,
                status=ConstraintStatus.PASS,
                reason="bounded pass reason",
            )
            for constraint in request.constraints
        )
    )
    return SemanticCacheRecord(
        semantic_input_sha256=semantic_input_sha256(request),
        model_id=request.model_id,
        prompt_version=request.prompt_version,
        structured_model_result=output,
        semantic_output_sha256=semantic_output_sha256(output),
    )


def test_sqlite_cache_exact_repeat_is_hit(tmp_path):
    request = _request()
    cache = SQLiteSemanticCache(
        tmp_path / "cache.sqlite3",
        clock=lambda: datetime(2026, 8, 29, tzinfo=timezone.utc),
    )
    cache.put(request, _record(request))
    assert cache.last_status is CacheStatus.MISS
    assert cache.get(request) == _record(request)
    assert cache.last_status is CacheStatus.HIT
    cache.close()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda request: replace(request, semantic_evidence_sha256="1" * 64),
        lambda request: replace(request, mandate_payload_sha256="2" * 64),
        lambda request: replace(request, transaction_body_sha256="3" * 64),
        lambda request: replace(request, model_id="other-semantic-model"),
        lambda request: replace(request, prompt_version="2.0"),
    ],
    ids=["evidence", "mandate", "transaction", "model", "prompt"],
)
def test_cache_key_mutations_are_misses(tmp_path, mutation):
    request = _request()
    cache = SQLiteSemanticCache(tmp_path / "cache.sqlite3")
    cache.put(request, _record(request))
    changed = mutation(request)
    assert semantic_input_sha256(changed) != semantic_input_sha256(request)
    assert cache.get(changed) is None
    assert cache.last_status is CacheStatus.MISS
    cache.close()


def test_cache_persists_only_normalized_result_and_integrity_metadata(tmp_path):
    path = tmp_path / "cache.sqlite3"
    request = _request()
    cache = SQLiteSemanticCache(path)
    cache.put(request, _record(request))
    cache.close()
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            """
            SELECT semantic_input_sha256, model_id, prompt_version, verdict,
                   structured_model_result_json, semantic_output_sha256,
                   created_at, record_sha256
            FROM semantic_decision_cache
            """
        ).fetchone()
    assert row is not None
    serialized = " ".join(str(value) for value in row).lower()
    assert "constraint_results" in serialized
    assert "chain-of-thought" not in serialized
    assert "api_key" not in serialized
    assert row[3] == "PASS"
    assert len(row[0]) == len(row[5]) == len(row[7]) == 64


def test_corrupt_cache_record_forces_review_without_live_model_reuse(tmp_path):
    path = tmp_path / "cache.sqlite3"
    first, cache, model = run_offline(tmp_path, ALLOW_INTENT, cache_path=path)
    assert first.trace.decision == "ALLOW"
    assert len(model.calls) == 1
    cache.close()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE semantic_decision_cache SET structured_model_result_json = '{}'"
        )
        connection.commit()
    second, second_cache, _ = run_offline(
        tmp_path,
        ALLOW_INTENT,
        cache_path=path,
        semantic_model=model,
    )
    assert second.trace.decision == "REVIEW"
    assert second.trace.cache["status"] == "MISS"
    assert second.trace.cache["integrity_failure"] is True
    assert len(model.calls) == 1
    second_cache.close()


def test_direct_corrupt_lookup_is_miss_not_allow(tmp_path):
    path = tmp_path / "cache.sqlite3"
    request = _request()
    cache = SQLiteSemanticCache(path)
    cache.put(request, _record(request))
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE semantic_decision_cache SET record_sha256 = ?",
            ("f" * 64,),
        )
        connection.commit()
    with pytest.raises(SemanticCacheIntegrityError, match="integrity"):
        cache.get(request)
    assert cache.last_status is CacheStatus.MISS
    assert cache.last_integrity_failure is True
    cache.close()
