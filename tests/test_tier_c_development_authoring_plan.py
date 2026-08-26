"""D8-B0 pre-authoring freeze integrity checks.

These tests inspect procedure artifacts only. They contain no Tier C semantic
scenario and do not call an authoring model or detector.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re

import pytest

from mandateguard.benchmark.tier_c.models import (
    GroundTruth,
    Provenance,
    Split,
    TierCCaseError,
    allocation_for_split,
)
from scripts.import_tier_c_case import require_registered_external_source_pin


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TIER_C_ROOT = REPOSITORY_ROOT / "benchmark" / "tier_c"
PLAN_PATH = TIER_C_ROOT / "DEVELOPMENT_AUTHORING_PLAN.md"
SOURCE_REGISTRY_PATH = TIER_C_ROOT / "development_sources.json"
MODEL_REGISTRY_PATH = TIER_C_ROOT / "SEPARATE_MODEL_AUTHORING.md"
PROMPT_PATH = TIER_C_ROOT / "prompts" / "separate_model_dev_v1.txt"
PROMPT_SHA_PATH = TIER_C_ROOT / "prompts" / "separate_model_dev_v1.sha256"
EXPECTED_PROMPT_SHA256 = (
    "aa2753cd249cb78e6af06784e52d5dc9a3cbfb7c817b715b190b5cacbd57c402"
)


def test_separate_model_prompt_bytes_are_frozen() -> None:
    digest = sha256(PROMPT_PATH.read_bytes()).hexdigest()
    assert digest == EXPECTED_PROMPT_SHA256
    assert PROMPT_SHA_PATH.read_text(encoding="utf-8") == f"{digest}\n"
    model_registry = MODEL_REGISTRY_PATH.read_text(encoding="utf-8")
    assert f"prompt_sha256: {digest}" in model_registry
    assert "authoring_model_id: TO_BE_PINNED_BEFORE_FIRST_GENERATION" in model_registry
    assert "| **Total** | **36** | **30** | **66** |" in model_registry


def test_development_source_registry_is_metadata_only_and_totals_66() -> None:
    registry = json.loads(SOURCE_REGISTRY_PATH.read_text(encoding="utf-8"))
    assert set(registry) == {
        "registry_schema_version",
        "registry_kind",
        "d8_a_frozen_sha",
        "content_policy",
        "sources",
    }
    assert registry["content_policy"] == {
        "registry_only": True,
        "individual_source_items_permitted": False,
        "source_passages_permitted": False,
        "held_out_sources_permitted": False,
        "pin_required_before_external_case_import": True,
        "later_version_requires_new_registry_entry": True,
    }

    expected_source_fields = {
        "source_id",
        "case_source_name",
        "project_name",
        "canonical_repository_url",
        "suite_domain",
        "intended_version_tag_commit",
        "selected_commit_sha",
        "source_selected_at",
        "intended_development_families",
        "source_purpose",
        "prohibited_uses",
        "notes",
    }
    expected_urls = {
        "AgentDojo": "https://github.com/sequrity-ai/agentdojo",
        "τ³-bench": "https://github.com/sierra-research/tau2-bench",
    }
    total = 0
    for source in registry["sources"]:
        assert set(source) == expected_source_fields
        assert source["canonical_repository_url"] == expected_urls[
            source["project_name"]
        ]
        assert source["selected_commit_sha"] is None
        assert source["source_selected_at"] is None
        for allocation in source["intended_development_families"]:
            assert set(allocation) == {
                "family_id",
                "violation_intended",
                "benign_intended",
            }
            assert allocation["family_id"].startswith("C-DEV-")
            total += allocation["violation_intended"]
            total += allocation["benign_intended"]
    assert total == 66
    assert "C-HOLD" not in json.dumps(registry, sort_keys=True)


def test_frozen_plan_matches_registered_development_allocation() -> None:
    allocation = allocation_for_split(Split.DEV)
    provenance_totals = {
        provenance: sum(
            count
            for (_, _, item_provenance), count in allocation.items()
            if item_provenance is provenance
        )
        for provenance in Provenance
    }
    ground_truth_totals = {
        ground_truth: sum(
            count
            for (_, item_ground_truth, _), count in allocation.items()
            if item_ground_truth is ground_truth
        )
        for ground_truth in GroundTruth
    }
    family_totals = {
        family_id: sum(
            count
            for (item_family, _, _), count in allocation.items()
            if item_family == family_id
        )
        for family_id in (
            "C-DEV-RECURRENCE",
            "C-DEV-EXCLUSION",
            "C-DEV-PURPOSE",
        )
    }
    assert provenance_totals == {
        Provenance.DEVELOPER_AUTHORED: 88,
        Provenance.EXTERNAL_DEFENSIVE_CORPUS_ADAPTED: 66,
        Provenance.SEPARATE_MODEL_ADVERSARIAL: 66,
    }
    assert ground_truth_totals == {
        GroundTruth.VIOLATION: 120,
        GroundTruth.BENIGN: 100,
    }
    assert family_totals == {
        "C-DEV-RECURRENCE": 74,
        "C-DEV-EXCLUSION": 73,
        "C-DEV-PURPOSE": 73,
    }
    assert sum(allocation.values()) == 220

    plan = PLAN_PATH.read_text(encoding="utf-8")
    assert "daed88178d0ff0308a2dc44e9bf996ba8f1abe6b" in plan
    assert "Claude Code, Codex, or another\nLLM must not generate" in plan
    assert "| **Total** | **48** | **40** | **88** |" in plan
    assert "| **Total** | **120** | **100** | **220** |" in plan
    assert "A model does not satisfy the requirement" in plan
    assert "expected deterministic minimum of 120" in plan


def test_external_import_gate_refuses_unpinned_registry_metadata() -> None:
    metadata_only_record = {
        "family_id": "C-DEV-RECURRENCE",
        "provenance": "external_defensive_corpus_adapted",
        "provenance_origin": {
            "source_name": "AgentDojo Banking",
            "source_version": None,
            "source_selected_at": None,
        },
    }
    with pytest.raises(TierCCaseError, match="is not pinned"):
        require_registered_external_source_pin(
            metadata_only_record, SOURCE_REGISTRY_PATH
        )


def test_zero_case_and_manifest_guarantees_remain_true() -> None:
    corpus_root = REPOSITORY_ROOT / "benchmark" / "cases" / "tier_c"
    assert not corpus_root.exists() or list(corpus_root.rglob("*.jsonl")) == []

    manifest = (REPOSITORY_ROOT / "benchmark" / "MANIFEST.yaml").read_text(
        encoding="utf-8"
    )
    assert len(re.findall(r"^  - case_id:", manifest, flags=re.MULTILINE)) == 1008
    assert 'evidence_tier: "C"' not in manifest
