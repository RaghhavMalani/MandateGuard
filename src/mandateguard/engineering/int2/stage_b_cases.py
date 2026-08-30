"""Strict frozen case data for the non-benchmark INT-2 Stage-B experiment."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from mandateguard.core.hashing import (
    CommittedHashes,
    catalog_snapshot_sha256,
    sha256_canonical,
    transaction_body_sha256,
)
from mandateguard.core.nonce_ledger import NonceLedgerState
from mandateguard.engineering.int2.downstream import DownstreamAuthorizationCase
from mandateguard.engineering.int2.fixtures import (
    RetrievalQueryCorpus,
    build_experiment_queries,
    load_query_corpus,
)
from mandateguard.engineering.int2.models import Int2ExperimentError
from mandateguard.engineering.semantic_fixtures import EngineeringExpectation
from mandateguard.intelligence.store import TrustedCommerceStore
from mandateguard.models.catalog import CatalogItem, CatalogSnapshot
from mandateguard.models.decision import DecisionAction, decide_deterministically
from mandateguard.models.mandate import (
    HardConstraints,
    IssuerAttestation,
    Mandate,
    MandateConstraints,
    MandatePayload,
    SemanticConstraint,
)
from mandateguard.models.transaction import (
    Transaction,
    TransactionLine,
    TransactionPayload,
)
from mandateguard.policy.tier_a import evaluate_tier_a
from mandateguard.policy.tier_b import evaluate_tier_b
from mandateguard.replay.scenario import ReplayScenario


EXPECTED_STAGE_B_QUERY_IDS = (
    "INT2-Q-STUDYGLOW",
    "INT2-Q-NOTEBOOK",
    "INT2-Q-STUDY-CLUB",
    "INT2-Q-MARKET-EDGE",
    "INT2-Q-TAX-GUIDE",
    "INT2-Q-FLEXI",
)
EXPECTED_PROVENANCE_SOURCE_PATHS = (
    "fixtures/agentic_commerce/merchant_catalog.json",
    "fixtures/agentic_commerce/merchant_terms.json",
)

_ROOT_FIELDS = frozenset(
    {"schema_version", "created_at", "case_count", "manifest_sha256", "cases"}
)
_CASE_FIELDS = frozenset(
    {
        "query_id",
        "engineering_purchase_intent",
        "engineering_expectation",
        "expected_final_action",
        "expectation_reason",
        "eligible_evidence_ids",
        "provenance",
        "replay_scenario",
    }
)
_PROVENANCE_FIELDS = frozenset(
    {
        "catalog_product_id",
        "catalog_snapshot_id",
        "merchant_evidence_ids",
        "policy_constraint_ids",
        "source_paths",
    }
)
_SCENARIO_FIELDS = frozenset(
    {
        "mandate",
        "transaction",
        "catalog_snapshot",
        "server_time",
        "nonce_state",
        "psp_committed_hashes",
        "replay_seed",
        "evaluated_at",
    }
)
_MANDATE_FIELDS = frozenset({"payload", "issuer_attestation", "metadata"})
_MANDATE_METADATA_FIELDS = frozenset({"engineering_query_id"})
_MANDATE_PAYLOAD_FIELDS = frozenset(
    {
        "schema_version",
        "mandate_id",
        "nonce",
        "issued_at",
        "expires_at",
        "subject_ref",
        "currency",
        "constraints",
    }
)
_CONSTRAINTS_FIELDS = frozenset({"hard", "semantic"})
_HARD_FIELDS = frozenset(
    {
        "max_total_minor",
        "max_quantity",
        "recurring_allowed",
        "merchant_allowlist",
        "sku_allowlist",
    }
)
_SEMANTIC_FIELDS = frozenset({"constraint_id", "kind", "text"})
_ATTESTATION_FIELDS = frozenset(
    {"assurance", "issuer_id", "alg", "key_id", "signature_b64url", "attestation_ref"}
)
_TRANSACTION_FIELDS = frozenset({"payload", "declared_transaction_hash"})
_TRANSACTION_PAYLOAD_FIELDS = frozenset(
    {
        "transaction_id",
        "merchant_id",
        "cart_currency",
        "order_currency",
        "declared_order_total_minor",
        "declared_aggregate_quantity",
        "cart_recurring",
        "order_recurring",
        "lines",
    }
)
_TRANSACTION_LINE_FIELDS = frozenset(
    {"sku", "effective_unit_price_minor", "quantity", "line_total_minor", "recurring"}
)
_CATALOG_FIELDS = frozenset({"snapshot_id", "merchant_id", "currency", "items"})
_CATALOG_ITEM_FIELDS = frozenset(
    {"sku", "merchant_id", "effective_unit_price_minor", "recurring"}
)
_NONCE_FIELDS = frozenset({"consumed_nonces"})
_COMMITMENT_FIELDS = frozenset(
    {"transaction_sha256", "catalog_snapshot_sha256"}
)
_FORBIDDEN_STAGE_A_FIELDS = frozenset(
    {
        "recall_at_k",
        "precision_at_k",
        "mean_precision_at_k",
        "reciprocal_rank",
        "mean_reciprocal_rank",
        "mean_recall_at_k",
        "mrr",
        "stage_a_metrics",
        "ranked_documents",
        "configuration_id",
        "all_required_retrieval_count",
        "required_evidence_miss_count",
        "retrieved_evidence_ids",
        "retrieval_strategy",
        "alpha",
        "top_k",
    }
)
_ACTION_BY_EXPECTATION = {
    EngineeringExpectation.PASS: "ALLOW",
    EngineeringExpectation.VIOLATION: "BLOCK",
    EngineeringExpectation.ABSTAIN: "REVIEW",
}


class StageBCaseManifestError(ValueError):
    """The frozen Stage-B case manifest is absent, mutated, or invalid."""


class _DuplicateJsonKeyError(ValueError):
    pass


def _object_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKeyError(f"duplicate JSON field: {key}")
        value[key] = item
    return value


def _reject_float(value: str) -> None:
    raise ValueError(f"floating-point values are not allowed: {value}")


def _reject_non_json_number(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant is not allowed: {value}")


def _exact(value: object, fields: frozenset[str], location: str) -> dict[str, Any]:
    if not isinstance(value, dict) or frozenset(value) != fields:
        raise StageBCaseManifestError(
            f"{location} has unexpected or missing fields"
        )
    return value


def _strings(value: object, location: str, *, nonempty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list) or (nonempty and not value):
        raise StageBCaseManifestError(f"{location} must be a JSON string array")
    if not all(isinstance(item, str) and item for item in value):
        raise StageBCaseManifestError(f"{location} must contain non-empty strings")
    if len(value) != len(set(value)):
        raise StageBCaseManifestError(f"{location} must contain unique values")
    return tuple(value)


def _optional_strings(value: object, location: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    return _strings(value, location, nonempty=False)


def _datetime(value: object, location: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise StageBCaseManifestError(f"{location} must be an RFC3339 UTC timestamp")
    try:
        result = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise StageBCaseManifestError(
            f"{location} must be an RFC3339 UTC timestamp"
        ) from error
    return result


def _find_forbidden_fields(value: object) -> frozenset[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in _FORBIDDEN_STAGE_A_FIELDS:
                found.add(key)
            found.update(_find_forbidden_fields(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_find_forbidden_fields(item))
    return frozenset(found)


@dataclass(frozen=True, slots=True)
class StageBCaseProvenance:
    catalog_product_id: str
    catalog_snapshot_id: str
    merchant_evidence_ids: tuple[str, ...]
    policy_constraint_ids: tuple[str, ...]
    source_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        for value, name in (
            (self.catalog_product_id, "catalog_product_id"),
            (self.catalog_snapshot_id, "catalog_snapshot_id"),
        ):
            if not isinstance(value, str) or not value:
                raise StageBCaseManifestError(f"{name} must be non-empty")
        for values, name in (
            (self.merchant_evidence_ids, "merchant_evidence_ids"),
            (self.policy_constraint_ids, "policy_constraint_ids"),
            (self.source_paths, "source_paths"),
        ):
            if not isinstance(values, tuple) or not values:
                raise StageBCaseManifestError(f"{name} must be non-empty")


@dataclass(frozen=True, slots=True)
class FrozenStageBCase:
    downstream_case: DownstreamAuthorizationCase
    engineering_purchase_intent: str
    expected_final_action: str
    expectation_reason: str
    provenance: StageBCaseProvenance

    def __post_init__(self) -> None:
        if not isinstance(self.downstream_case, DownstreamAuthorizationCase):
            raise StageBCaseManifestError("downstream_case is invalid")
        for value, name, maximum in (
            (self.engineering_purchase_intent, "engineering_purchase_intent", 2000),
            (self.expectation_reason, "expectation_reason", 1000),
        ):
            if not isinstance(value, str) or not value or len(value) > maximum:
                raise StageBCaseManifestError(f"{name} must be bounded and non-empty")
        expected = _ACTION_BY_EXPECTATION[
            self.downstream_case.engineering_expectation
        ]
        if self.expected_final_action != expected:
            raise StageBCaseManifestError(
                "expected_final_action does not match engineering_expectation"
            )
        if not isinstance(self.provenance, StageBCaseProvenance):
            raise StageBCaseManifestError("provenance is invalid")

    @property
    def query_id(self) -> str:
        return self.downstream_case.query_id

    @property
    def engineering_expectation(self) -> EngineeringExpectation:
        return self.downstream_case.engineering_expectation

    @property
    def scenario(self) -> ReplayScenario:
        return self.downstream_case.scenario

    @property
    def eligible_evidence_ids(self) -> tuple[str, ...]:
        return tuple(
            item.evidence_id for item in self.downstream_case.eligible_evidence
        )


@dataclass(frozen=True, slots=True)
class StageBCaseManifest:
    schema_version: str
    created_at: datetime
    manifest_sha256: str
    cases: tuple[FrozenStageBCase, ...]

    def __post_init__(self) -> None:
        if self.schema_version != "1.0":
            raise StageBCaseManifestError("schema_version must be 1.0")
        if not isinstance(self.created_at, datetime) or self.created_at.tzinfo is None:
            raise StageBCaseManifestError("created_at must be timezone-aware")
        if not isinstance(self.manifest_sha256, str) or len(self.manifest_sha256) != 64:
            raise StageBCaseManifestError("manifest_sha256 must be a SHA-256 digest")
        if not isinstance(self.cases, tuple) or len(self.cases) != 6:
            raise StageBCaseManifestError("the manifest must contain exactly six cases")
        query_ids = tuple(item.query_id for item in self.cases)
        if query_ids != EXPECTED_STAGE_B_QUERY_IDS:
            raise StageBCaseManifestError(
                "cases must exactly cover the six frozen query IDs in order"
            )

    def for_query(self, query_id: str) -> FrozenStageBCase:
        try:
            return next(item for item in self.cases if item.query_id == query_id)
        except StopIteration as error:
            raise StageBCaseManifestError(f"unknown Stage-B query ID {query_id!r}") from error


def deterministic_action(case: DownstreamAuthorizationCase) -> DecisionAction:
    """Evaluate only the existing frozen Tier A/B policy for one case."""

    scenario = case.scenario
    tier_a = evaluate_tier_a(
        mandate=scenario.mandate,
        transaction=scenario.transaction,
        catalog_snapshot=scenario.catalog_snapshot,
        server_time=scenario.server_time,
        nonce_state=scenario.nonce_state,
        committed_hashes=scenario.psp_committed_hashes,
    )
    tier_b = evaluate_tier_b(
        mandate=scenario.mandate,
        transaction=scenario.transaction,
    )
    decision = decide_deterministically(
        replay_seed=scenario.replay_seed,
        evaluated_at=scenario.evaluated_at,
        transaction_sha256=transaction_body_sha256(scenario.transaction),
        catalog_snapshot_sha256=(
            catalog_snapshot_sha256(scenario.catalog_snapshot)
            if scenario.catalog_snapshot is not None
            else None
        ),
        tier_a_results=tier_a,
        tier_b_findings=tier_b,
    )
    return decision.action


def canonical_stage_b_manifest_sha256(decoded: Mapping[str, object]) -> str:
    """Hash every manifest field except the field that stores this digest."""

    if not isinstance(decoded, Mapping):
        raise TypeError("decoded must be a mapping")
    payload = {key: value for key, value in decoded.items() if key != "manifest_sha256"}
    return sha256_canonical(payload)


def _parse_mandate(value: object, location: str) -> Mandate:
    record = _exact(value, _MANDATE_FIELDS, location)
    payload = _exact(record["payload"], _MANDATE_PAYLOAD_FIELDS, f"{location}.payload")
    constraints = _exact(
        payload["constraints"], _CONSTRAINTS_FIELDS, f"{location}.payload.constraints"
    )
    hard = _exact(
        constraints["hard"], _HARD_FIELDS, f"{location}.payload.constraints.hard"
    )
    raw_semantic = constraints["semantic"]
    if not isinstance(raw_semantic, list) or not raw_semantic:
        raise StageBCaseManifestError(
            f"{location}.payload.constraints.semantic must be non-empty"
        )
    semantic = tuple(
        SemanticConstraint(**_exact(item, _SEMANTIC_FIELDS, f"{location}.semantic[{index}]"))
        for index, item in enumerate(raw_semantic)
    )
    attestation = _exact(
        record["issuer_attestation"], _ATTESTATION_FIELDS, f"{location}.issuer_attestation"
    )
    metadata = _exact(
        record["metadata"], _MANDATE_METADATA_FIELDS, f"{location}.metadata"
    )
    return Mandate(
        payload=MandatePayload(
            schema_version=payload["schema_version"],
            mandate_id=payload["mandate_id"],
            nonce=payload["nonce"],
            issued_at=_datetime(payload["issued_at"], f"{location}.payload.issued_at"),
            expires_at=_datetime(payload["expires_at"], f"{location}.payload.expires_at"),
            subject_ref=payload["subject_ref"],
            currency=payload["currency"],
            constraints=MandateConstraints(
                hard=HardConstraints(
                    max_total_minor=hard["max_total_minor"],
                    max_quantity=hard["max_quantity"],
                    recurring_allowed=hard["recurring_allowed"],
                    merchant_allowlist=_optional_strings(
                        hard["merchant_allowlist"],
                        f"{location}.payload.constraints.hard.merchant_allowlist",
                    ),
                    sku_allowlist=_optional_strings(
                        hard["sku_allowlist"],
                        f"{location}.payload.constraints.hard.sku_allowlist",
                    ),
                ),
                semantic=semantic,
            ),
        ),
        issuer_attestation=IssuerAttestation(**attestation),
        metadata=metadata,
    )


def _parse_transaction(value: object, location: str) -> Transaction:
    record = _exact(value, _TRANSACTION_FIELDS, location)
    payload = _exact(
        record["payload"], _TRANSACTION_PAYLOAD_FIELDS, f"{location}.payload"
    )
    raw_lines = payload["lines"]
    if not isinstance(raw_lines, list) or not raw_lines:
        raise StageBCaseManifestError(f"{location}.payload.lines must be non-empty")
    lines = tuple(
        TransactionLine(
            **_exact(item, _TRANSACTION_LINE_FIELDS, f"{location}.payload.lines[{index}]")
        )
        for index, item in enumerate(raw_lines)
    )
    transaction_payload = TransactionPayload(
        transaction_id=payload["transaction_id"],
        merchant_id=payload["merchant_id"],
        cart_currency=payload["cart_currency"],
        order_currency=payload["order_currency"],
        declared_order_total_minor=payload["declared_order_total_minor"],
        declared_aggregate_quantity=payload["declared_aggregate_quantity"],
        cart_recurring=payload["cart_recurring"],
        order_recurring=payload["order_recurring"],
        lines=lines,
    )
    return Transaction(
        payload=transaction_payload,
        declared_transaction_hash=record["declared_transaction_hash"],
    )


def _parse_catalog(value: object, location: str) -> CatalogSnapshot:
    record = _exact(value, _CATALOG_FIELDS, location)
    raw_items = record["items"]
    if not isinstance(raw_items, list) or not raw_items:
        raise StageBCaseManifestError(f"{location}.items must be non-empty")
    return CatalogSnapshot(
        snapshot_id=record["snapshot_id"],
        merchant_id=record["merchant_id"],
        currency=record["currency"],
        items=tuple(
            CatalogItem(
                **_exact(item, _CATALOG_ITEM_FIELDS, f"{location}.items[{index}]")
            )
            for index, item in enumerate(raw_items)
        ),
    )


def _parse_scenario(value: object, location: str) -> ReplayScenario:
    record = _exact(value, _SCENARIO_FIELDS, location)
    transaction = _parse_transaction(record["transaction"], f"{location}.transaction")
    catalog = _parse_catalog(record["catalog_snapshot"], f"{location}.catalog_snapshot")
    nonce = _exact(record["nonce_state"], _NONCE_FIELDS, f"{location}.nonce_state")
    commitments = _exact(
        record["psp_committed_hashes"],
        _COMMITMENT_FIELDS,
        f"{location}.psp_committed_hashes",
    )
    if transaction.declared_transaction_hash != transaction_body_sha256(transaction):
        raise StageBCaseManifestError(
            f"{location}.transaction declared hash does not match its payload"
        )
    if commitments["transaction_sha256"] != transaction_body_sha256(transaction):
        raise StageBCaseManifestError(
            f"{location} transaction commitment does not match"
        )
    if commitments["catalog_snapshot_sha256"] != catalog_snapshot_sha256(catalog):
        raise StageBCaseManifestError(f"{location} catalog commitment does not match")
    return ReplayScenario(
        mandate=_parse_mandate(record["mandate"], f"{location}.mandate"),
        transaction=transaction,
        catalog_snapshot=catalog,
        server_time=_datetime(record["server_time"], f"{location}.server_time"),
        nonce_state=NonceLedgerState(
            frozenset(
                _strings(
                    nonce["consumed_nonces"],
                    f"{location}.nonce_state.consumed_nonces",
                    nonempty=False,
                )
            )
        ),
        psp_committed_hashes=CommittedHashes(**commitments),
        replay_seed=record["replay_seed"],
        evaluated_at=_datetime(record["evaluated_at"], f"{location}.evaluated_at"),
    )


def _validate_case_against_sources(
    case: FrozenStageBCase,
    *,
    query_spec: object,
    experiment_query: object,
    store: TrustedCommerceStore,
) -> None:
    scenario = case.scenario
    transaction = scenario.transaction.payload
    query_merchant = getattr(query_spec, "merchant_id")
    query_sku = getattr(query_spec, "sku")
    if transaction.merchant_id != query_merchant:
        raise StageBCaseManifestError("scenario merchant does not match query corpus")
    if len(transaction.lines) != 1 or transaction.lines[0].sku != query_sku:
        raise StageBCaseManifestError("scenario SKU does not match query corpus")
    product = store.get_product(merchant_id=query_merchant, sku=query_sku)
    line = transaction.lines[0]
    if (
        line.effective_unit_price_minor != product.effective_unit_price_minor
        or line.recurring != product.recurring
        or transaction.cart_currency != product.currency
        or transaction.order_currency != product.currency
    ):
        raise StageBCaseManifestError("scenario transaction does not match catalog product")
    expected_catalog = store.catalog_snapshot(merchant_id=query_merchant)
    if scenario.catalog_snapshot != expected_catalog:
        raise StageBCaseManifestError("scenario catalog snapshot is not authoritative")
    expected_eligible = tuple(
        document.evidence_id
        for document in getattr(experiment_query, "documents")
        if document.evidence_id is not None
    )
    if case.eligible_evidence_ids != expected_eligible:
        raise StageBCaseManifestError(
            "eligible evidence must exactly match the frozen merchant retrieval corpus"
        )
    by_evidence_id = {item.evidence_id: item for item in store.evidence_entries}
    for evidence_id in case.eligible_evidence_ids:
        entry = by_evidence_id[evidence_id]
        if entry.merchant_id != query_merchant:
            raise StageBCaseManifestError("eligible evidence is outside merchant scope")
        if entry.sku is not None:
            store.get_product(merchant_id=query_merchant, sku=entry.sku)
    scoped_sources = store.evidence_for_product(
        merchant_id=query_merchant, sku=query_sku
    )
    scoped_source_ids = {item.evidence_id for item in scoped_sources}
    if set(case.provenance.merchant_evidence_ids) != scoped_source_ids:
        raise StageBCaseManifestError(
            "expectation provenance must exactly cover product-scoped evidence"
        )
    if case.provenance.catalog_product_id != f"{query_merchant}/{query_sku}":
        raise StageBCaseManifestError("catalog_product_id does not match the case")
    if case.provenance.catalog_snapshot_id != expected_catalog.snapshot_id:
        raise StageBCaseManifestError("catalog_snapshot_id does not match the case")
    if case.provenance.source_paths != EXPECTED_PROVENANCE_SOURCE_PATHS:
        raise StageBCaseManifestError(
            "source_paths must identify the frozen catalog and merchant evidence"
        )
    if scenario.mandate.metadata.get("engineering_query_id") != case.query_id:
        raise StageBCaseManifestError(
            "mandate engineering_query_id does not match the case"
        )
    semantic_ids = {
        item.constraint_id
        for item in scenario.mandate.payload.constraints.semantic
    }
    if set(case.provenance.policy_constraint_ids) != semantic_ids:
        raise StageBCaseManifestError(
            "policy_constraint_ids must exactly identify semantic constraints"
        )
    if deterministic_action(case.downstream_case) is not DecisionAction.ALLOW:
        raise StageBCaseManifestError(
            "every Stage-B case must be deterministic Tier A/B ALLOW"
        )


def load_stage_b_case_manifest(
    path: Path,
    *,
    query_corpus_path: Path,
    store: TrustedCommerceStore,
) -> StageBCaseManifest:
    """Load, hash-check, type, and source-validate all six frozen cases."""

    if not isinstance(path, Path) or not isinstance(query_corpus_path, Path):
        raise TypeError("paths must be pathlib.Path")
    if not isinstance(store, TrustedCommerceStore):
        raise TypeError("store must be TrustedCommerceStore")
    try:
        decoded = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_float=_reject_float,
            parse_constant=_reject_non_json_number,
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise StageBCaseManifestError(f"cannot parse Stage-B manifest {path}") from error
    root = _exact(decoded, _ROOT_FIELDS, "stage_b_cases")
    forbidden = _find_forbidden_fields(root)
    if forbidden:
        raise StageBCaseManifestError(
            "Stage-B expectations contain forbidden Stage-A result fields: "
            + ",".join(sorted(forbidden))
        )
    if root["schema_version"] != "1.0" or root["case_count"] != 6:
        raise StageBCaseManifestError("Stage-B root metadata is invalid")
    expected_hash = canonical_stage_b_manifest_sha256(root)
    if root["manifest_sha256"] != expected_hash:
        raise StageBCaseManifestError("manifest_sha256 does not commit the manifest")
    raw_cases = root["cases"]
    if not isinstance(raw_cases, list) or len(raw_cases) != 6:
        raise StageBCaseManifestError("stage_b_cases.cases must contain six records")

    corpus: RetrievalQueryCorpus = load_query_corpus(query_corpus_path)
    query_ids = tuple(item.query_id for item in corpus.queries)
    if query_ids != EXPECTED_STAGE_B_QUERY_IDS:
        raise StageBCaseManifestError(
            "retrieval query corpus does not match the frozen Stage-B query IDs"
        )
    experiment_queries = build_experiment_queries(corpus, store)
    parsed: list[FrozenStageBCase] = []
    for index, raw_case in enumerate(raw_cases):
        location = f"stage_b_cases.cases[{index}]"
        record = _exact(raw_case, _CASE_FIELDS, location)
        try:
            expectation = EngineeringExpectation(record["engineering_expectation"])
        except (TypeError, ValueError) as error:
            raise StageBCaseManifestError(
                f"{location}.engineering_expectation is unsupported"
            ) from error
        provenance_record = _exact(
            record["provenance"], _PROVENANCE_FIELDS, f"{location}.provenance"
        )
        scenario = _parse_scenario(record["replay_scenario"], f"{location}.replay_scenario")
        evidence_ids = _strings(
            record["eligible_evidence_ids"], f"{location}.eligible_evidence_ids"
        )
        by_id = {item.evidence_id: item for item in store.evidence_entries}
        try:
            eligible_evidence = tuple(by_id[evidence_id] for evidence_id in evidence_ids)
        except KeyError as error:
            raise StageBCaseManifestError(
                f"{location} contains an unknown eligible evidence ID"
            ) from error
        try:
            frozen = FrozenStageBCase(
                downstream_case=DownstreamAuthorizationCase(
                    query_id=record["query_id"],
                    engineering_expectation=expectation,
                    scenario=scenario,
                    eligible_evidence=eligible_evidence,
                ),
                engineering_purchase_intent=record["engineering_purchase_intent"],
                expected_final_action=record["expected_final_action"],
                expectation_reason=record["expectation_reason"],
                provenance=StageBCaseProvenance(
                    catalog_product_id=provenance_record["catalog_product_id"],
                    catalog_snapshot_id=provenance_record["catalog_snapshot_id"],
                    merchant_evidence_ids=_strings(
                        provenance_record["merchant_evidence_ids"],
                        f"{location}.provenance.merchant_evidence_ids",
                    ),
                    policy_constraint_ids=_strings(
                        provenance_record["policy_constraint_ids"],
                        f"{location}.provenance.policy_constraint_ids",
                    ),
                    source_paths=_strings(
                        provenance_record["source_paths"],
                        f"{location}.provenance.source_paths",
                    ),
                ),
            )
        except Int2ExperimentError as error:
            raise StageBCaseManifestError(f"{location} is invalid: {error}") from error
        _validate_case_against_sources(
            frozen,
            query_spec=corpus.queries[index],
            experiment_query=experiment_queries[index],
            store=store,
        )
        parsed.append(frozen)
    return StageBCaseManifest(
        schema_version=root["schema_version"],
        created_at=_datetime(root["created_at"], "stage_b_cases.created_at"),
        manifest_sha256=root["manifest_sha256"],
        cases=tuple(parsed),
    )


def manifest_preview_record(manifest: StageBCaseManifest) -> tuple[Mapping[str, object], ...]:
    """Return the deterministic, network-free case preview."""

    return tuple(
        MappingProxyType(
            {
                "query_id": case.query_id,
                "engineering_expectation": case.engineering_expectation.value,
                "deterministic_action": deterministic_action(
                    case.downstream_case
                ).value,
                "eligible_evidence_count": len(case.eligible_evidence_ids),
            }
        )
        for case in manifest.cases
    )
