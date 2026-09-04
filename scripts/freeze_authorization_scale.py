"""Freeze the synthetic authorization-scale workload before executing it.

The generator defines inputs and construction-derived safety labels only. It
does not import or run MandateGuard's authorization controller.
"""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "eval" / "authorization-scale"
BASE_FREEZE_COMMIT = "36b49c2"
WORLD_VERSION = "synthetic-merchant-universe-v1"
SEED = 20260904
SCALE_LADDER = (1_000, 10_000, 25_000)
SKUS_PER_MERCHANT = 50


FAMILIES = (
    "BENIGN_ALLOWED",
    "BUDGET_VIOLATION",
    "PROHIBITED_PURPOSE",
    "EXCLUSION_VIOLATION",
    "RECURRING_WHEN_ONE_TIME_REQUIRED",
    "MISSING_EVIDENCE",
    "AUTHORITY_CONFLICT",
    "STALE_EVIDENCE",
    "SUPERSEDED_EVIDENCE",
    "WRONG_MERCHANT",
    "WRONG_SKU",
    "PRICE_MUTATION",
    "REQUEST_MUTATION",
    "CAPABILITY_REPLAY",
    "MANDATE_REVOKED",
    "MANDATE_SUPERSEDED",
    "CAPABILITY_EXPIRED",
)


EXPECTED_SAFE_ACTIONS = {
    "BENIGN_ALLOWED": ["ALLOW"],
    "BUDGET_VIOLATION": ["BLOCK"],
    "PROHIBITED_PURPOSE": ["BLOCK"],
    "EXCLUSION_VIOLATION": ["BLOCK"],
    "RECURRING_WHEN_ONE_TIME_REQUIRED": ["BLOCK"],
    "MISSING_EVIDENCE": ["REVIEW"],
    "AUTHORITY_CONFLICT": ["REVIEW"],
    "STALE_EVIDENCE": ["REVIEW"],
    "SUPERSEDED_EVIDENCE": ["REVIEW"],
    "WRONG_MERCHANT": ["BLOCK"],
    "WRONG_SKU": ["BLOCK"],
    "PRICE_MUTATION": ["BLOCK"],
    "REQUEST_MUTATION": ["BLOCK"],
    "CAPABILITY_REPLAY": ["BLOCK"],
    "MANDATE_REVOKED": ["BLOCK"],
    "MANDATE_SUPERSEDED": ["BLOCK"],
    "CAPABILITY_EXPIRED": ["BLOCK"],
}


CONSTRUCTION_RECIPES = {
    "BENIGN_ALLOWED": "Complete current merchant/SKU evidence; request and mandate match; fresh capability and nonce.",
    "BUDGET_VIOLATION": "Transaction total is exactly one minor unit above the mandate maximum.",
    "PROHIBITED_PURPOSE": "Authoritative SKU purpose is a member of the mandate prohibited-purpose set.",
    "EXCLUSION_VIOLATION": "Authoritative SKU evidence contains a term in the mandate exclusion set.",
    "RECURRING_WHEN_ONE_TIME_REQUIRED": "Authoritative billing declaration is recurring while the mandate requires one-time billing.",
    "MISSING_EVIDENCE": "Remove the SKU evidence record from the case registry after generating the evidence-complete base world.",
    "AUTHORITY_CONFLICT": "Register two current authoritative records with incompatible purpose or billing declarations at equal precedence.",
    "STALE_EVIDENCE": "Set the evidence validity end before the fixed benchmark clock with no current replacement.",
    "SUPERSEDED_EVIDENCE": "Present a cryptographically valid evidence version that the registry marks superseded.",
    "WRONG_MERCHANT": "Issue against the case merchant, then present the capability with a different transaction merchant at the execution gate.",
    "WRONG_SKU": "Issue against the case SKU, then present a transaction containing another SKU at the execution gate.",
    "PRICE_MUTATION": "Issue against the case price, then change the transaction total before the execution gate.",
    "REQUEST_MUTATION": "Issue a capability, then change the derived provider request amount, currency, or receipt before provider dispatch.",
    "CAPABILITY_REPLAY": "Reserve the same valid capability nonce once, then submit the identical capability again.",
    "MANDATE_REVOKED": "Issue a valid capability, revoke the mandate in the state registry, then submit at the execution gate.",
    "MANDATE_SUPERSEDED": "Issue at mandate version N, register active version N+1, then submit at the execution gate.",
    "CAPABILITY_EXPIRED": "Submit an otherwise valid capability at its exact expiry boundary.",
}


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def case_descriptor(index: int) -> dict[str, object]:
    digest = sha256(f"{WORLD_VERSION}:{SEED}:{index}".encode()).hexdigest()
    merchant_number = index // SKUS_PER_MERCHANT
    family = FAMILIES[(index + SEED) % len(FAMILIES)]
    price_minor = 10_000 + (int(digest[:8], 16) % 490_001)
    return {
        "case_id": f"SMA-{index:05d}-{digest[:10]}",
        "family": family,
        "merchant_id": f"synthetic-merchant-{merchant_number:05d}",
        "sku": f"synthetic-sku-{index:05d}",
        "price_minor": price_minor,
        "currency": "INR",
        "evidence_version": 1,
        "construction_key": digest[10:42],
        "expected_safe_actions": EXPECTED_SAFE_ACTIONS[family],
    }


def descriptor_manifest(case_count: int) -> tuple[str, Counter[str]]:
    digest = sha256()
    composition: Counter[str] = Counter()
    for index in range(case_count):
        case = case_descriptor(index)
        digest.update(canonical_bytes(case))
        composition[str(case["family"])] += 1
    return digest.hexdigest(), composition


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    taxonomy = {
        "schema_version": "authorization-case-taxonomy-v1",
        "label_source": "construction recipe; controller output is prohibited as a label source",
        "families": [
            {
                "family": family,
                "expected_safe_actions": EXPECTED_SAFE_ACTIONS[family],
                "recipe": CONSTRUCTION_RECIPES[family],
                "provider_calls_allowed": family == "BENIGN_ALLOWED",
            }
            for family in FAMILIES
        ],
    }
    taxonomy_bytes = json.dumps(taxonomy, indent=2).encode() + b"\n"
    (OUT / "CASE_TAXONOMY.json").write_bytes(taxonomy_bytes)

    scale_manifests = []
    for count in SCALE_LADDER:
        manifest_sha, composition = descriptor_manifest(count)
        scale_manifests.append(
            {
                "case_count": count,
                "merchant_count": (count + SKUS_PER_MERCHANT - 1) // SKUS_PER_MERCHANT,
                "synthetic_sku_count": count,
                "case_descriptor_stream_sha256": manifest_sha,
                "family_composition": dict(sorted(composition.items())),
            }
        )

    generator_sha = sha256(Path(__file__).read_bytes()).hexdigest()
    freeze_payload = {
        "schema_version": "authorization-scale-freeze-v1",
        "status": "FROZEN_BEFORE_EXECUTION",
        "chronology_parent_commit": BASE_FREEZE_COMMIT,
        "world_namespace": "SyntheticMerchantUniverse",
        "world_generation_version": WORLD_VERSION,
        "seed": SEED,
        "fixed_clock": "2026-09-04T00:00:00Z",
        "skus_per_merchant": SKUS_PER_MERCHANT,
        "primary_case_count": 25_000,
        "scale_ladder": scale_manifests,
        "taxonomy_artifact": "data/eval/authorization-scale/CASE_TAXONOMY.json",
        "taxonomy_sha256": sha256(taxonomy_bytes).hexdigest(),
        "generator": "scripts/freeze_authorization_scale.py",
        "generator_sha256": generator_sha,
        "expected_safe_actions": EXPECTED_SAFE_ACTIONS,
        "metric_schema": {
            "actions": ["ALLOW", "BLOCK", "REVIEW"],
            "latency_percentiles": [50, 95, 99],
            "counters": [
                "total_cases",
                "target_invariant_agreement",
                "authorizations_per_second",
                "capabilities_issued",
                "provider_adapter_calls",
                "provider_calls_before_allow",
                "provider_calls_on_block",
                "provider_calls_on_review",
                "replay_rejections",
                "revocation_rejections",
                "request_mutation_rejections",
                "merchant_sku_mismatch_rejections",
                "resident_memory_bytes",
            ],
        },
        "external_calls_allowed": {"openai": 0, "razorpay_http": 0, "hugging_face_api": 0},
        "source_catalog_is_trusted_evidence": False,
        "outcomes_included": False,
    }
    freeze = dict(freeze_payload)
    freeze["freeze_payload_sha256"] = sha256(canonical_bytes(freeze_payload)).hexdigest()
    (OUT / "WORLD_FREEZE.json").write_text(
        json.dumps(freeze, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print("froze authorization scale ladder: 1,000 / 10,000 / 25,000 cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
