"""Freeze records and the execution gate for the Resolve recovery evaluation.

Everything here is deterministic and offline. It loads the frozen plan, the
freeze record, and the commit binding, re-derives every hash the freeze claims,
and refuses execution unless every preregistered condition holds. It produces no
outcomes and evaluates no transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import subprocess
from typing import Any, Mapping

from mandateguard.core.hashing import sha256_canonical
from mandateguard.engineering.resolve_eval.metrics import (
    EVALUATION_METRIC_NAMES,
    METRIC_SCHEMA_VERSION,
    OBSERVED_COUNTER_NAMES,
    PREREGISTERED_OBSERVED_METRIC_NAMES,
    validate_preregistered_observed_metrics,
)
from mandateguard.engineering.resolve_eval.worlds import (
    EXPECTED_CASE_COUNT,
    FIXTURE_ROOT,
    ResolveCaseWorld,
    WorldFixtureError,
    build_registry,
    load_worlds,
    read_strict_json,
)
from mandateguard.product.evidence_policy import (
    PRODUCT_EVIDENCE_POLICY,
    TRUST_SENSITIVE_FIELDS,
)
from mandateguard.recovery import validate_metric_names, validate_observed_counters


PLAN_SCHEMA = "RESOLVE_PREREGISTRATION_PLAN_V1"
FREEZE_SCHEMA = "RESOLVE_PREREGISTRATION_FREEZE_V1"
COMMIT_SCHEMA = "RESOLVE_PREREGISTRATION_COMMIT_V1"
FROZEN_STATUS = "FROZEN"
PREREGISTERED_NO_OUTCOMES = "PREREGISTERED_NO_OUTCOMES"
OUTCOMES_EXIST = "OUTCOMES_EXIST"

PLAN_PATH = FIXTURE_ROOT / "preregistration_plan.json"
FREEZE_PATH = FIXTURE_ROOT / "preregistration_freeze.json"
COMMIT_PATH = FIXTURE_ROOT / "preregistration_commit.json"
OUTPUT_ROOT = (
    Path("artifacts")
    / "engineering"
    / "resolve_recovery"
    / "resolve-recovery-20-case-v1"
)

#: Case-level keys that would silently change which trusted evidence reaches
#: authorization. A case declaring any of them is refused before the freeze.
FORBIDDEN_CASE_OVERRIDE_KEYS: frozenset[str] = frozenset(
    set(TRUST_SENSITIVE_FIELDS)
    | {
        "retrieval_mode",
        "retrieval_alpha",
        "alpha",
        "top_k",
        "semantic_mode",
        "scope_semantics",
        "conflict_semantics",
        "controller_precedence",
        "evidence_policy_override",
        "trust_configuration",
    }
)

#: The twelve preregistered architecture invariants, by identifier.
SAFETY_INVARIANT_IDS: tuple[str, ...] = tuple(f"S{index}" for index in range(1, 13))

FINAL_ACTIONS: frozenset[str] = frozenset({"ALLOW", "BLOCK", "REVIEW"})


class PreregistrationError(RuntimeError):
    """The preregistration is absent, unfrozen, drifted, or unbound."""


@dataclass(frozen=True, slots=True)
class FrozenPreregistration:
    """The frozen plan together with every hash the freeze record commits."""

    plan: Mapping[str, Any]
    plan_canonical_sha256: str
    plan_raw_file_sha256: str
    freeze: Mapping[str, Any]
    freeze_raw_file_sha256: str
    worlds: tuple[ResolveCaseWorld, ...]
    registry_sha256: str

    @property
    def case_ids(self) -> tuple[str, ...]:
        return tuple(case["case_id"] for case in self.plan["cases"])


def _raw_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _require(condition: object, message: str) -> None:
    if not condition:
        raise PreregistrationError(message)


def canonical_plan_sha256(plan: Mapping[str, Any]) -> str:
    """Commit the decoded plan independently of its file formatting."""

    return sha256_canonical(plan)


def _validate_evidence_policy(plan: Mapping[str, Any]) -> None:
    policy = plan.get("evidence_policy") or {}
    _require(
        policy.get("policy_id") == PRODUCT_EVIDENCE_POLICY.policy_id,
        "plan does not declare the product evidence policy",
    )
    _require(
        policy.get("top_k") == PRODUCT_EVIDENCE_POLICY.top_k,
        "plan top_k differs from the product evidence policy",
    )
    _require(
        policy.get("alpha") == f"{PRODUCT_EVIDENCE_POLICY.alpha}",
        "plan alpha differs from the product evidence policy",
    )
    _require(
        policy.get("retrieval_mode") == PRODUCT_EVIDENCE_POLICY.retrieval_mode.value,
        "plan retrieval mode differs from the product evidence policy",
    )
    _require(
        policy.get("max_acquisition_rounds")
        == PRODUCT_EVIDENCE_POLICY.max_acquisition_rounds,
        "plan max_acquisition_rounds differs from the product evidence policy",
    )
    _require(
        policy.get("max_new_evidence_items")
        == PRODUCT_EVIDENCE_POLICY.max_new_evidence_items,
        "plan max_new_evidence_items differs from the product evidence policy",
    )
    _require(
        tuple(policy.get("trust_sensitive_fields", ())) == TRUST_SENSITIVE_FIELDS,
        "plan trust-sensitive field list differs from the product policy",
    )


def _validate_metric_schema(plan: Mapping[str, Any]) -> None:
    schema = plan.get("metric_schema") or {}
    _require(
        schema.get("version") == METRIC_SCHEMA_VERSION,
        f"plan does not declare {METRIC_SCHEMA_VERSION}",
    )
    validate_observed_counters(
        schema.get("runtime_observed_counters", ()),
        context="preregistered runtime observed counters",
    )
    validate_metric_names(
        schema.get("shared_evaluation_metrics", ()),
        emitted=EVALUATION_METRIC_NAMES,
        context="preregistered shared evaluation metrics",
    )
    validate_preregistered_observed_metrics(
        schema.get("preregistered_observed_metrics", ()),
        context="preregistered observed metrics",
    )
    definitions = schema.get("definitions") or {}
    _require(
        tuple(definitions) == PREREGISTERED_OBSERVED_METRIC_NAMES,
        "every preregistered observed metric must carry a frozen definition",
    )


def _validate_cases(
    plan: Mapping[str, Any], worlds: tuple[ResolveCaseWorld, ...]
) -> None:
    cases = plan.get("cases") or []
    _require(
        len(cases) == EXPECTED_CASE_COUNT,
        f"plan must preregister exactly {EXPECTED_CASE_COUNT} cases",
    )
    case_ids = [case["case_id"] for case in cases]
    _require(len(set(case_ids)) == len(case_ids), "plan case IDs are not unique")
    by_case_id = {world.case_id: world for world in worlds}
    _require(
        set(case_ids) == set(by_case_id),
        "plan cases and frozen worlds do not describe the same set",
    )
    families: dict[str, int] = {}
    for case in cases:
        case_id = case["case_id"]
        world = by_case_id[case_id]
        offending = sorted(FORBIDDEN_CASE_OVERRIDE_KEYS.intersection(case))
        _require(
            not offending,
            f"{case_id} declares a trust-sensitive override: {', '.join(offending)}",
        )
        _require(
            case.get("evidence_policy") == "product_default_evidence_policy",
            f"{case_id} does not run the product default evidence policy",
        )
        _require(
            case.get("merchant_id") == world.merchant_id
            and case.get("sku") == world.sku,
            f"{case_id} identity differs from its frozen world",
        )
        _require(
            case.get("amount_minor") == world.amount_minor
            and isinstance(world.amount_minor, int)
            and world.amount_minor > 0,
            f"{case_id} transaction amount is not frozen",
        )
        _require(
            case.get("currency") == world.currency,
            f"{case_id} currency differs from its frozen world",
        )
        _require(
            case.get("case_family") == world.case_family,
            f"{case_id} family differs from its frozen world",
        )
        _require(
            case.get("expected_initial_action") == "REVIEW",
            f"{case_id} must preregister an initial REVIEW",
        )
        allowed = tuple(case.get("allowed_final_actions") or ())
        forbidden = tuple(case.get("forbidden_final_actions") or ())
        _require(
            allowed and set(allowed) <= FINAL_ACTIONS,
            f"{case_id} has no valid allowed final action set",
        )
        _require(
            set(forbidden) <= FINAL_ACTIONS and not set(allowed) & set(forbidden),
            f"{case_id} allowed and forbidden final actions overlap",
        )
        expected = case.get("expected_final_action")
        _require(
            expected is None or expected in allowed,
            f"{case_id} expected final action is outside its allowed safe set",
        )
        posture = case.get("expected_safety_posture")
        _require(
            isinstance(posture, str) and len(posture.strip()) >= 24,
            f"{case_id} has no stated safety posture",
        )
        _require(
            case.get("max_permitted_acquisition_rounds")
            == PRODUCT_EVIDENCE_POLICY.max_acquisition_rounds
            and case.get("max_permitted_evidence_items")
            == PRODUCT_EVIDENCE_POLICY.max_new_evidence_items,
            f"{case_id} budgets differ from the product evidence policy",
        )
        attempts = case.get("expected_recovery_attempts")
        _require(
            isinstance(attempts, int) and 1 <= attempts <= 3,
            f"{case_id} preregisters no bounded recovery attempt count",
        )
        _require(
            case.get("expected_provider_execution_posture")
            == "PAYMENT_PROVIDER_ONLY_AFTER_FRESH_FINAL_ALLOW",
            f"{case_id} does not preregister the provider-execution posture",
        )
        for key in (
            "world_fixture",
            "mandate_fixture_ref",
            "catalog_fixture_ref",
            "transaction_fixture_ref",
        ):
            _require(isinstance(case.get(key), str) and case[key], f"{case_id} {key}")
        _require(
            bool(case.get("initial_evidence_fixture_refs")),
            f"{case_id} references no initial evidence fixture",
        )
        _require(
            bool(case.get("recovery_source_manifest_refs")),
            f"{case_id} references no recovery source manifest",
        )
        families[world.case_family] = families.get(world.case_family, 0) + 1
    _require(
        families == dict(plan.get("composition") or {}),
        f"plan composition does not match the frozen worlds: {families}",
    )


def _validate_invariants(plan: Mapping[str, Any]) -> None:
    invariants = plan.get("safety_invariants") or []
    identifiers = tuple(item.get("id") for item in invariants)
    _require(
        identifiers == SAFETY_INVARIANT_IDS,
        "plan does not preregister S1 through S12 in order",
    )
    for item in invariants:
        _require(
            isinstance(item.get("statement"), str) and item["statement"].strip(),
            f"invariant {item.get('id')} has no statement",
        )


def load_frozen_preregistration(repository_root: Path) -> FrozenPreregistration:
    """Load and structurally validate the frozen plan and freeze record."""

    plan_path = repository_root / PLAN_PATH
    freeze_path = repository_root / FREEZE_PATH
    for path in (plan_path, freeze_path):
        _require(path.is_file(), f"preregistration artifact is missing: {path}")

    plan = read_strict_json(plan_path)
    freeze = read_strict_json(freeze_path)
    _require(plan.get("schema") == PLAN_SCHEMA, "plan schema is not " + PLAN_SCHEMA)
    _require(
        freeze.get("schema") == FREEZE_SCHEMA, "freeze schema is not " + FREEZE_SCHEMA
    )
    _require(plan.get("status") == FROZEN_STATUS, "evaluation plan is not FROZEN")
    _require(freeze.get("status") == FROZEN_STATUS, "freeze record is not FROZEN")
    _require(
        plan.get("external_call_policy")
        == {"openai_calls": 0, "razorpay_http_calls": 0, "network_calls": 0},
        "the evaluation must remain offline",
    )

    _validate_evidence_policy(plan)
    _validate_metric_schema(plan)
    _validate_invariants(plan)

    try:
        worlds = load_worlds(repository_root)
    except WorldFixtureError as error:
        raise PreregistrationError(str(error)) from error
    _validate_cases(plan, worlds)

    registry = build_registry(worlds)
    _require(
        freeze.get("registry_sha256") == registry.registry_sha256,
        "trusted source registry differs from the frozen manifest set",
    )

    canonical = canonical_plan_sha256(plan)
    raw = _raw_sha256(plan_path)
    _require(
        freeze.get("plan_canonical_sha256") == canonical,
        "frozen plan canonical SHA-256 does not match the plan",
    )
    _require(
        freeze.get("plan_raw_file_sha256") == raw,
        "frozen plan raw file SHA-256 does not match the plan",
    )

    fixture_hashes = freeze.get("fixture_sha256") or {}
    _require(bool(fixture_hashes), "freeze record commits no fixture hashes")
    for relative, digest in fixture_hashes.items():
        path = repository_root / relative
        _require(path.is_file(), f"frozen fixture is missing: {relative}")
        _require(
            _raw_sha256(path) == digest,
            f"frozen fixture changed after the freeze: {relative}",
        )

    return FrozenPreregistration(
        plan=plan,
        plan_canonical_sha256=canonical,
        plan_raw_file_sha256=raw,
        freeze=freeze,
        freeze_raw_file_sha256=_raw_sha256(freeze_path),
        worlds=worlds,
        registry_sha256=registry.registry_sha256,
    )


def _git(repository_root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise PreregistrationError(
            "git " + " ".join(arguments) + " failed: " + result.stderr.strip()
        )
    return result.stdout.strip()


def _validate_commit_binding(
    repository_root: Path, frozen: FrozenPreregistration
) -> str:
    """Require the second freeze step to name the commit that froze the plan.

    A plan cannot contain the SHA of the commit that introduces it, so the
    freeze is two-step: Commit A introduces the plan, the freeze record, and the
    fixtures; Commit B records Commit A's SHA in an immutable binding record and
    creates no outcome data. Execution then requires that Commit A is an
    ancestor of HEAD and that every bound path is byte-identical to its content
    at Commit A, which is the operational form of "the current commit matches
    preregistration_commit_sha" once the binding commit itself exists.
    """

    commit_path = repository_root / COMMIT_PATH
    _require(
        commit_path.is_file(),
        "preregistration commit binding is missing; the freeze is incomplete",
    )
    binding = read_strict_json(commit_path)
    _require(
        binding.get("schema") == COMMIT_SCHEMA, "commit binding schema is not " + COMMIT_SCHEMA
    )
    commit_sha = binding.get("preregistration_commit_sha")
    _require(
        isinstance(commit_sha, str)
        and len(commit_sha) == 40
        and all(character in "0123456789abcdef" for character in commit_sha),
        "commit binding does not name a full commit SHA",
    )
    _require(
        binding.get("plan_canonical_sha256") == frozen.plan_canonical_sha256
        and binding.get("plan_raw_file_sha256") == frozen.plan_raw_file_sha256
        and binding.get("freeze_raw_file_sha256") == frozen.freeze_raw_file_sha256,
        "commit binding does not commit the frozen plan and freeze record",
    )
    head = _git(repository_root, "rev-parse", "HEAD")
    if head != commit_sha:
        merge_base = _git(repository_root, "merge-base", commit_sha, "HEAD")
        _require(
            merge_base == commit_sha,
            "the preregistration commit is not an ancestor of HEAD",
        )
    bound_paths = tuple(binding.get("bound_paths") or ())
    _require(bool(bound_paths), "commit binding names no bound paths")
    changed = _git(
        repository_root, "diff", "--name-only", commit_sha, "HEAD", "--", *bound_paths
    )
    _require(
        not changed,
        "preregistered artifacts changed after the preregistration commit: " + changed,
    )
    return commit_sha


def require_execution_preconditions(
    repository_root: Path, *, now: datetime, allow_resume: bool = False
) -> tuple[FrozenPreregistration, str]:
    """Refuse the evaluation unless every preregistered condition holds."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise PreregistrationError("now must be timezone-aware")
    frozen = load_frozen_preregistration(repository_root)

    window = frozen.plan.get("validity_window") or {}
    valid_from = datetime.fromisoformat(window["valid_from"])
    valid_until = datetime.fromisoformat(window["valid_until"])
    _require(
        valid_from <= now < valid_until,
        "the preregistration validity window has not opened or has closed",
    )
    for world in frozen.worlds:
        _require(
            now < world.mandate.payload.expires_at,
            f"{world.case_id} mandate has expired; the preregistration is stale",
        )

    status = _git(repository_root, "status", "--porcelain", "--untracked-files=all")
    _require(not status, "the working tree must be clean before outcomes are produced")
    commit_sha = _validate_commit_binding(repository_root, frozen)

    output_root = repository_root / OUTPUT_ROOT
    _require(
        allow_resume or not output_root.exists(),
        "an outcome artifact already exists for this evaluation",
    )
    return frozen, commit_sha


def structural_report(repository_root: Path) -> dict[str, Any]:
    """Summarize the deterministic freeze state without producing outcomes."""

    frozen = load_frozen_preregistration(repository_root)
    commit_path = repository_root / COMMIT_PATH
    binding = read_strict_json(commit_path) if commit_path.is_file() else None
    outcomes_exist = (repository_root / OUTPUT_ROOT).exists()
    return {
        "evaluation_id": frozen.plan["evaluation_id"],
        "status": frozen.plan["status"],
        "case_count": len(frozen.case_ids),
        "case_ids": list(frozen.case_ids),
        "composition": dict(frozen.plan["composition"]),
        "metric_schema_version": METRIC_SCHEMA_VERSION,
        "evidence_policy_id": PRODUCT_EVIDENCE_POLICY.policy_id,
        "registry_sha256": frozen.registry_sha256,
        "plan_canonical_sha256": frozen.plan_canonical_sha256,
        "plan_raw_file_sha256": frozen.plan_raw_file_sha256,
        "freeze_raw_file_sha256": frozen.freeze_raw_file_sha256,
        "frozen_fixture_count": len(frozen.freeze["fixture_sha256"]),
        "preregistration_commit_sha": (
            None if binding is None else binding.get("preregistration_commit_sha")
        ),
        "observed_metric_names": list(PREREGISTERED_OBSERVED_METRIC_NAMES),
        "runtime_observed_counters": list(OBSERVED_COUNTER_NAMES),
        "outcome_lifecycle_state": (
            OUTCOMES_EXIST if outcomes_exist else PREREGISTERED_NO_OUTCOMES
        ),
        "outcomes_executed": outcomes_exist,
    }


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "COMMIT_PATH",
    "COMMIT_SCHEMA",
    "FORBIDDEN_CASE_OVERRIDE_KEYS",
    "FREEZE_PATH",
    "FREEZE_SCHEMA",
    "FROZEN_STATUS",
    "OUTCOMES_EXIST",
    "OUTPUT_ROOT",
    "PLAN_PATH",
    "PLAN_SCHEMA",
    "PREREGISTERED_NO_OUTCOMES",
    "SAFETY_INVARIANT_IDS",
    "FrozenPreregistration",
    "PreregistrationError",
    "canonical_plan_sha256",
    "load_frozen_preregistration",
    "require_execution_preconditions",
    "structural_report",
    "utc_now",
]
