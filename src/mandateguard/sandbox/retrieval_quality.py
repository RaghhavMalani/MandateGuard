"""Fixed retrieval-v2 UX evaluation for the judge Playground.

This module measures discovery quality. Its expected categories have authority
NONE and are never imported by the application, buyer, controller, or execution
gate. Authorization is invoked only to count whether an ordinary top candidate
can traverse the existing controller; its verdict is recorded, never predicted.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Mapping
from uuid import uuid4


FREEZE_PATH = Path("fixtures") / "playground" / "retrieval_v2_queries.json"


def _load_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def load_retrieval_v2(repository_root: Path) -> tuple[Mapping[str, Any], list[dict[str, Any]]]:
    freeze_path = repository_root / FREEZE_PATH
    freeze_bytes = freeze_path.read_bytes()
    freeze = json.loads(freeze_bytes)
    base = freeze["base_query_set"]
    base_path = repository_root / base["path"]
    base_bytes = base_path.read_bytes()
    if sha256(base_bytes).hexdigest() != base["sha256"]:
        raise ValueError("retrieval-v2 base query set no longer matches its freeze")
    base_queries = json.loads(base_bytes)["queries"]

    expected_by_id: dict[str, list[str]] = {}
    for group in freeze["base_expectations"]:
        for query_id in group["query_ids"]:
            expected_by_id[query_id] = list(group["expected_category_ids"])
    expanded = [
        {
            "id": entry["id"],
            "cohort": f"BASE_{entry['cohort']}",
            "text": entry["text"],
            "expected_result": "DIRECT_MATCH",
            "expected_category_ids": expected_by_id[entry["id"]],
            "understood_family": None,
            "source": "EXISTING_120",
        }
        for entry in base_queries
    ]
    expanded.extend({**entry, "source": "NEW_OOD_50"} for entry in freeze["ood_queries"])
    return freeze, expanded


def _baseline_outcomes(repository_root: Path, freeze: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    base = freeze["base_query_set"]
    path = repository_root / base["baseline_report_path"]
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != base["baseline_report_sha256"]:
        raise ValueError("the v2 baseline report no longer matches its frozen digest")
    report = json.loads(raw)
    return {str(item["query_id"]): item for item in report["outcomes"]}


def evaluate_retrieval_v2(service: Any, repository_root: Path) -> dict[str, Any]:
    freeze, prompts = load_retrieval_v2(repository_root)
    baseline = _baseline_outcomes(repository_root, freeze)
    session_id = service.open_judge_session()["session_id"]
    outcomes: list[dict[str, Any]] = []
    external_calls = 0
    provider_adapter_calls = 0

    for entry in prompts:
        started = perf_counter()
        search = service.playground_search(
            intent=entry["text"], top_k=8, session_id=session_id
        )
        search_ms = (perf_counter() - started) * 1000.0
        candidates = search["candidates"]
        categories = [item["category_id"] for item in candidates]
        expected = set(entry["expected_category_ids"])
        wants_direct = entry["expected_result"] == "DIRECT_MATCH"
        found = bool(candidates)
        top1_correct = bool(found and categories[0] in expected) if wants_direct else False
        top5_correct = bool(expected.intersection(categories[:5])) if wants_direct else False
        correct_no_match = bool(not found) if not wants_direct else False
        wrong_match = bool(
            (found and not wants_direct)
            or (found and wants_direct and not top1_correct)
        )

        decision = None
        authorization_ms = 0.0
        authorization_state = "NOT_RUN_NO_DIRECT_CANDIDATE"
        if found and not search.get("clarification_required"):
            ceiling = (
                None
                if search["mandate"]["max_total_minor"] is not None
                else int(candidates[0]["price_minor"])
            )
            started = perf_counter()
            run, _deduplicated, _session = service.playground_authorize(
                intent=entry["text"],
                catalog_product_id=candidates[0]["catalog_product_id"],
                request_id="retrieval_v2_" + uuid4().hex,
                session_id=session_id,
                max_total_minor=ceiling,
            )
            if not run.completion.wait(60):
                raise TimeoutError(f"authorization timed out for {entry['id']}")
            authorization_ms = (perf_counter() - started) * 1000.0
            snapshot = run.snapshot()
            authorization_state = snapshot["state"]
            result = snapshot.get("result")
            if isinstance(result, dict):
                decision = result.get("decision")
                execution = result.get("execution") or {}
                external_calls += int(execution.get("external_network_calls", 0))
                provider_adapter_calls += int(execution.get("razorpay_calls", 0))

        outcomes.append(
            {
                "query_id": entry["id"],
                "source": entry["source"],
                "cohort": entry["cohort"],
                "text": entry["text"],
                "expected_result": entry["expected_result"],
                "expected_category_ids": entry["expected_category_ids"],
                "understood_family": entry.get("understood_family"),
                "returned_category_ids": categories,
                "top_candidate": candidates[0]["name"] if found else None,
                "direct_match": found,
                "top1_correct": top1_correct,
                "top5_correct": top5_correct,
                "correct_no_match": correct_no_match,
                "wrong_match": wrong_match,
                "no_match": search.get("no_match"),
                "inferred_family": search["retrieval"].get("product_family"),
                "decision": decision,
                "authorization_state": authorization_state,
                "search_ms": round(search_ms, 3),
                "authorization_ms": round(authorization_ms, 3),
            }
        )

    present = [item for item in outcomes if item["expected_result"] == "DIRECT_MATCH"]
    missing = [item for item in outcomes if item["expected_result"] == "NO_DIRECT_MATCH"]
    old = [item for item in outcomes if item["source"] == "EXISTING_120"]
    old_stability: list[dict[str, Any]] = []
    for item in old:
        previous = baseline[item["query_id"]]
        previous_found = int(previous.get("candidates_found", 0)) > 0
        stable = previous_found == item["direct_match"] and previous.get("decision") == item["decision"]
        old_stability.append(
            {
                "query_id": item["query_id"],
                "stable": stable,
                "previous_candidate_found": previous_found,
                "current_candidate_found": item["direct_match"],
                "previous_decision": previous.get("decision"),
                "current_decision": item["decision"],
            }
        )

    def rate(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 4) if denominator else 0.0

    report = {
        "evaluation_version": freeze["evaluation_version"],
        "status": "EXECUTED_AFTER_SEPARATE_FREEZE_COMMIT",
        "prompt_count": len(outcomes),
        "existing_prompt_count": len(old),
        "ood_prompt_count": len(outcomes) - len(old),
        "present_category_prompt_count": len(present),
        "missing_category_prompt_count": len(missing),
        "world_version": service.playground.manifest["world_version"],
        "products_sha256": service.playground.manifest["products_sha256"],
        "retrieval_method": "LEXICAL_FIELD_WEIGHTED_PLUS_CATEGORY_INTENT_GUARD",
        "semantic_model_shipped": False,
        "authorization_authority_of_expectations": "NONE",
        "metrics": {
            "direct_match_rate": rate(sum(item["direct_match"] for item in outcomes), len(outcomes)),
            "correct_category_at_1": rate(sum(item["top1_correct"] for item in present), len(present)),
            "correct_category_at_5": rate(sum(item["top5_correct"] for item in present), len(present)),
            "no_result_correctness": rate(
                sum(
                    (item["direct_match"] and item["expected_result"] == "DIRECT_MATCH")
                    or (not item["direct_match"] and item["expected_result"] == "NO_DIRECT_MATCH")
                    for item in outcomes
                ),
                len(outcomes),
            ),
            "correct_no_match_rate": rate(sum(item["correct_no_match"] for item in missing), len(missing)),
            "wrong_match_rate": rate(sum(item["wrong_match"] for item in outcomes), len(outcomes)),
            "candidate_found_rate": rate(sum(item["direct_match"] for item in outcomes), len(outcomes)),
            "ordinary_allow_capable_rate": rate(sum(item["decision"] == "ALLOW" for item in present), len(present)),
        },
        "old_120_outcome_stability": {
            "stable": sum(item["stable"] for item in old_stability),
            "total": len(old_stability),
            "rate": rate(sum(item["stable"] for item in old_stability), len(old_stability)),
            "definition": "Candidate presence and real-controller decision both match the v2 baseline.",
            "changes": [item for item in old_stability if not item["stable"]],
        },
        "external_network_calls": external_calls,
        "offline_provider_adapter_calls": provider_adapter_calls,
        "outcomes": outcomes,
    }
    return report
