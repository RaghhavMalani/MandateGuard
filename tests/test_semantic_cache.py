from __future__ import annotations

from dataclasses import replace
import json

import pytest

from mandateguard.models.decision import DecisionAction
from mandateguard.semantic.cache import (
    FileSemanticCache,
    InMemorySemanticCache,
    SemanticCacheIntegrityError,
    SemanticCacheRecord,
    SemanticReplayMissError,
)
from mandateguard.semantic.models import (
    ConstraintResult,
    ConstraintStatus,
    NormalizedSemanticOutput,
    semantic_input_sha256,
    semantic_output_sha256,
)
from mandateguard.semantic.orchestration import authorize_transaction
from mandateguard.semantic.verifier import SemanticMode, SemanticVerifier
from tests.semantic_factories import (
    ScriptedSemanticModel,
    make_semantic_evidence,
    model_output,
    valid_authorization_inputs,
)


def _verifier(cache, *, model_id: str = "semantic-test-model-v1", prompt: str = "1.0"):
    model = ScriptedSemanticModel(
        response=model_output("PASS", "PASS"), model_id=model_id
    )
    return SemanticVerifier(model=model, cache=cache, prompt_version=prompt), model


def _run(verifier: SemanticVerifier, *, mode: SemanticMode = SemanticMode.LIVE):
    return authorize_transaction(
        **valid_authorization_inputs(),
        semantic_evidence=make_semantic_evidence(),
        semantic_verifier=verifier,
        semantic_mode=mode,
    )


def test_live_miss_hit_and_replay_have_exact_call_counts() -> None:
    verifier, model = _verifier(InMemorySemanticCache())

    first = _run(verifier)
    assert len(model.calls) == 1
    second = _run(verifier)
    assert len(model.calls) == 1
    replay = _run(verifier, mode=SemanticMode.REPLAY)

    assert first == second == replay
    assert len(model.calls) == 1


def test_replay_cache_miss_is_hard_error_and_never_calls_model() -> None:
    verifier, model = _verifier(InMemorySemanticCache())

    with pytest.raises(SemanticReplayMissError, match="required"):
        _run(verifier, mode=SemanticMode.REPLAY)

    assert len(model.calls) == 0


def test_wrong_model_and_prompt_versions_cannot_reuse_a_record() -> None:
    cache = InMemorySemanticCache()
    first_verifier, first_model = _verifier(cache)
    _run(first_verifier)
    other_model_verifier, other_model = _verifier(cache, model_id="semantic-test-model-v2")
    other_prompt_verifier, other_prompt_model = _verifier(cache, prompt="1.1")

    with pytest.raises(SemanticReplayMissError):
        _run(other_model_verifier, mode=SemanticMode.REPLAY)
    with pytest.raises(SemanticReplayMissError):
        _run(other_prompt_verifier, mode=SemanticMode.REPLAY)

    assert len(first_model.calls) == 1
    assert len(other_model.calls) == 0
    assert len(other_prompt_model.calls) == 0


def test_altered_cache_input_hash_is_rejected() -> None:
    verifier, model = _verifier(InMemorySemanticCache())
    _run(verifier)
    request = model.calls[0]
    original = verifier.cache.records[semantic_input_sha256(request)]
    other_request = replace(request, prompt_version="1.1")
    other_record = replace(
        original,
        semantic_input_sha256=semantic_input_sha256(other_request),
        prompt_version="1.1",
    )
    corrupt = InMemorySemanticCache({semantic_input_sha256(request): other_record})

    with pytest.raises(SemanticCacheIntegrityError, match="input hash"):
        corrupt.get(request)


def test_altered_cached_output_is_rejected_even_with_recomputed_output_hash() -> None:
    verifier, model = _verifier(InMemorySemanticCache())
    _run(verifier)
    request = model.calls[0]
    wrong_output = NormalizedSemanticOutput(
        constraint_results=(
            ConstraintResult(
                constraint_id="unknown-1",
                status=ConstraintStatus.PASS,
                reason="not requested",
            ),
        )
    )
    record = SemanticCacheRecord(
        semantic_input_sha256=semantic_input_sha256(request),
        model_id=request.model_id,
        prompt_version=request.prompt_version,
        structured_model_result=wrong_output,
        semantic_output_sha256=semantic_output_sha256(wrong_output),
    )
    corrupt = InMemorySemanticCache({semantic_input_sha256(request): record})

    with pytest.raises(SemanticCacheIntegrityError, match="cover"):
        corrupt.get(request)


def test_file_cache_persists_exact_result_across_instances(tmp_path) -> None:
    directory = tmp_path / "semantic-cache"
    first_verifier, first_model = _verifier(FileSemanticCache(directory))
    first = _run(first_verifier)
    second_verifier, second_model = _verifier(FileSemanticCache(directory))

    replay = _run(second_verifier, mode=SemanticMode.REPLAY)

    assert first == replay
    assert first.final_action is DecisionAction.ALLOW
    assert len(first_model.calls) == 1
    assert len(second_model.calls) == 0


def test_file_cache_rejects_wrong_output_hash(tmp_path) -> None:
    directory = tmp_path / "semantic-cache"
    verifier, model = _verifier(FileSemanticCache(directory))
    _run(verifier)
    request = model.calls[0]
    path = directory / f"{semantic_input_sha256(request)}.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["semantic_output_sha256"] = "0" * 64
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(SemanticCacheIntegrityError, match="structural validation"):
        verifier.cache.get(request)


def test_file_cache_rejects_altered_output_content(tmp_path) -> None:
    directory = tmp_path / "semantic-cache"
    verifier, model = _verifier(FileSemanticCache(directory))
    _run(verifier)
    request = model.calls[0]
    path = directory / f"{semantic_input_sha256(request)}.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["structured_model_result"]["constraint_results"][0]["reason"] = "altered"
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(SemanticCacheIntegrityError, match="structural validation"):
        verifier.cache.get(request)
