"""Integrity checks for the retrieval-v2 UX prompt freeze.

The expectations in this fixture have discovery authority only. They are never
read by the application or the authorization controller.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FREEZE_PATH = ROOT / "fixtures" / "playground" / "retrieval_v2_queries.json"


def test_retrieval_v2_freezes_120_existing_and_50_ood_prompts() -> None:
    freeze = json.loads(FREEZE_PATH.read_text(encoding="utf-8"))
    base = freeze["base_query_set"]
    base_path = ROOT / base["path"]
    raw = base_path.read_bytes()
    old = json.loads(raw)

    assert freeze["evaluation_version"] == "judge-playground-retrieval-v2"
    assert freeze["status"] == "FROZEN_BEFORE_EXECUTION"
    assert sha256(raw).hexdigest() == base["sha256"]
    assert old["query_set_version"] == base["query_set_version"]
    assert len(old["queries"]) == base["count"] == 120

    referenced = [
        query_id
        for group in freeze["base_expectations"]
        for query_id in group["query_ids"]
    ]
    expected_ids = [entry["id"] for entry in old["queries"]]
    assert len(referenced) == len(set(referenced)) == 120
    assert sorted(referenced) == sorted(expected_ids)

    ood = freeze["ood_queries"]
    assert len(ood) == 50
    assert len({entry["id"] for entry in ood}) == 50
    assert all(entry["text"].strip() for entry in ood)
    assert all(entry["expected_result"] in {"DIRECT_MATCH", "NO_DIRECT_MATCH"} for entry in ood)
    assert all(
        bool(entry["expected_category_ids"])
        == (entry["expected_result"] == "DIRECT_MATCH")
        for entry in ood
    )
    assert sum(entry["expected_result"] == "NO_DIRECT_MATCH" for entry in ood) >= 10


def test_retrieval_v2_expectations_cannot_encode_authorization_outcomes() -> None:
    raw = FREEZE_PATH.read_text(encoding="utf-8").casefold()
    for forbidden in ('"decision"', '"verdict"', '"expected_allow"', '"expected_block"'):
        assert forbidden not in raw
