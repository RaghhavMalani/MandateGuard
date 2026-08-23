"""Canonical JSON codec for the deterministic Tier A/B benchmark corpus.

Every encoder below writes plain JSON-compatible values only: no Python repr,
no pickle, no floats. Decoding rebuilds the frozen typed objects, so a JSONL
line round-trips to the same canonical evaluation-input bytes it came from.

Case-content hash projection
----------------------------

``case_content_sha256`` is the SHA-256 of the MandateGuard canonical JSON of
exactly this mapping, and of nothing else::

    {
      "case_schema_version": str,
      "evidence_tier": str,
      "family_id": str,
      "provenance": str,
      "split": str,
      "ground_truth": str,
      "label_source": str,
      "expected_action": str,
      "target_expectation": {"family_id": str, "status": str},
      "evaluation_inputs": <encode_evaluation_inputs(...)>
    }

Deliberately excluded, because they are audit metadata rather than benchmark
content: ``case_id``, ``case_content_sha256`` itself, ``label_recorded_at``,
``first_run_at``, and the whole ``generator`` audit block. Excluding
``case_id`` means identical benchmark content keeps one digest even if someone
re-labels it with a different identifier.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from mandateguard.benchmark.models import (
    BenchmarkCase,
    EvaluationInputs,
    GeneratorAudit,
    TargetExpectation,
)
from mandateguard.core.canonical import canonical_json_text
from mandateguard.core.hashing import CommittedHashes, sha256_canonical
from mandateguard.core.nonce_ledger import NonceLedgerState
from mandateguard.models.catalog import CatalogItem, CatalogSnapshot
from mandateguard.models.mandate import (
    HardConstraints,
    IssuerAttestation,
    Mandate,
    MandateConstraints,
    MandatePayload,
)
from mandateguard.models.transaction import Transaction, TransactionLine, TransactionPayload


CONTENT_HASH_FIELDS = (
    "case_schema_version",
    "evidence_tier",
    "family_id",
    "provenance",
    "split",
    "ground_truth",
    "label_source",
    "expected_action",
    "target_expectation",
    "evaluation_inputs",
)

AUDIT_ONLY_FIELDS = (
    "case_id",
    "case_content_sha256",
    "label_recorded_at",
    "first_run_at",
    "generator",
)


def encode_timestamp(value: datetime) -> str:
    """Canonical UTC timestamp, matching MandateGuard canonical JSON exactly."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def decode_timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("canonical timestamps must be UTC strings ending in Z")
    return datetime.fromisoformat(value[:-1]).replace(tzinfo=timezone.utc)


def _encode_optional_tuple(values: tuple[str, ...] | None) -> list[str] | None:
    return None if values is None else list(values)


def _decode_optional_tuple(values: object, name: str) -> tuple[str, ...] | None:
    if values is None:
        return None
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        raise ValueError(f"{name} must be null or an array of strings")
    return tuple(values)


def encode_mandate(mandate: Mandate) -> dict[str, Any]:
    payload = mandate.payload
    hard = payload.constraints.hard
    attestation = mandate.issuer_attestation
    return {
        "payload": {
            "schema_version": payload.schema_version,
            "mandate_id": payload.mandate_id,
            "nonce": payload.nonce,
            "issued_at": encode_timestamp(payload.issued_at),
            "expires_at": encode_timestamp(payload.expires_at),
            "subject_ref": payload.subject_ref,
            "currency": payload.currency,
            "constraints": {
                "hard": {
                    "max_total_minor": hard.max_total_minor,
                    "max_quantity": hard.max_quantity,
                    "recurring_allowed": hard.recurring_allowed,
                    "merchant_allowlist": _encode_optional_tuple(hard.merchant_allowlist),
                    "sku_allowlist": _encode_optional_tuple(hard.sku_allowlist),
                },
                "semantic": [
                    {
                        "constraint_id": item.constraint_id,
                        "kind": item.kind,
                        "text": item.text,
                    }
                    for item in payload.constraints.semantic
                ],
            },
        },
        "issuer_attestation": {
            "assurance": attestation.assurance,
            "issuer_id": attestation.issuer_id,
            "alg": attestation.alg,
            "key_id": attestation.key_id,
            "signature_b64url": attestation.signature_b64url,
            "attestation_ref": attestation.attestation_ref,
        },
        "metadata": dict(mandate.metadata),
    }


def decode_mandate(value: Mapping[str, Any]) -> Mandate:
    payload = value["payload"]
    hard = payload["constraints"]["hard"]
    semantic = payload["constraints"]["semantic"]
    if semantic:
        raise ValueError("deterministic benchmark mandates carry no semantic constraints")
    attestation = value["issuer_attestation"]
    return Mandate(
        payload=MandatePayload(
            mandate_id=payload["mandate_id"],
            nonce=payload["nonce"],
            issued_at=decode_timestamp(payload["issued_at"]),
            expires_at=decode_timestamp(payload["expires_at"]),
            subject_ref=payload["subject_ref"],
            currency=payload["currency"],
            constraints=MandateConstraints(
                hard=HardConstraints(
                    max_total_minor=hard["max_total_minor"],
                    max_quantity=hard["max_quantity"],
                    recurring_allowed=hard["recurring_allowed"],
                    merchant_allowlist=_decode_optional_tuple(
                        hard["merchant_allowlist"], "merchant_allowlist"
                    ),
                    sku_allowlist=_decode_optional_tuple(
                        hard["sku_allowlist"], "sku_allowlist"
                    ),
                ),
                semantic=(),
            ),
            schema_version=payload["schema_version"],
        ),
        issuer_attestation=IssuerAttestation(
            assurance=attestation["assurance"],
            issuer_id=attestation["issuer_id"],
            alg=attestation["alg"],
            key_id=attestation["key_id"],
            signature_b64url=attestation["signature_b64url"],
            attestation_ref=attestation["attestation_ref"],
        ),
        metadata=dict(value["metadata"]),
    )


def encode_transaction(transaction: Transaction) -> dict[str, Any]:
    payload = transaction.payload
    return {
        "payload": {
            "transaction_id": payload.transaction_id,
            "merchant_id": payload.merchant_id,
            "cart_currency": payload.cart_currency,
            "order_currency": payload.order_currency,
            "declared_order_total_minor": payload.declared_order_total_minor,
            "declared_aggregate_quantity": payload.declared_aggregate_quantity,
            "cart_recurring": payload.cart_recurring,
            "order_recurring": payload.order_recurring,
            "lines": [
                {
                    "sku": line.sku,
                    "effective_unit_price_minor": line.effective_unit_price_minor,
                    "quantity": line.quantity,
                    "line_total_minor": line.line_total_minor,
                    "recurring": line.recurring,
                }
                for line in payload.lines
            ],
        },
        "declared_transaction_hash": transaction.declared_transaction_hash,
    }


def decode_transaction(value: Mapping[str, Any]) -> Transaction:
    payload = value["payload"]
    return Transaction(
        payload=TransactionPayload(
            transaction_id=payload["transaction_id"],
            merchant_id=payload["merchant_id"],
            cart_currency=payload["cart_currency"],
            order_currency=payload["order_currency"],
            declared_order_total_minor=payload["declared_order_total_minor"],
            declared_aggregate_quantity=payload["declared_aggregate_quantity"],
            cart_recurring=payload["cart_recurring"],
            order_recurring=payload["order_recurring"],
            lines=tuple(
                TransactionLine(
                    sku=line["sku"],
                    effective_unit_price_minor=line["effective_unit_price_minor"],
                    quantity=line["quantity"],
                    line_total_minor=line["line_total_minor"],
                    recurring=line["recurring"],
                )
                for line in payload["lines"]
            ),
        ),
        declared_transaction_hash=value["declared_transaction_hash"],
    )


def encode_catalog(catalog: CatalogSnapshot | None) -> dict[str, Any] | None:
    if catalog is None:
        return None
    return {
        "snapshot_id": catalog.snapshot_id,
        "merchant_id": catalog.merchant_id,
        "currency": catalog.currency,
        "items": [
            {
                "sku": item.sku,
                "merchant_id": item.merchant_id,
                "effective_unit_price_minor": item.effective_unit_price_minor,
                "recurring": item.recurring,
            }
            for item in catalog.items
        ],
    }


def decode_catalog(value: Mapping[str, Any] | None) -> CatalogSnapshot | None:
    if value is None:
        return None
    return CatalogSnapshot(
        snapshot_id=value["snapshot_id"],
        merchant_id=value["merchant_id"],
        currency=value["currency"],
        items=tuple(
            CatalogItem(
                sku=item["sku"],
                merchant_id=item["merchant_id"],
                effective_unit_price_minor=item["effective_unit_price_minor"],
                recurring=item["recurring"],
            )
            for item in value["items"]
        ),
    )


def encode_nonce_state(state: NonceLedgerState | None) -> dict[str, Any] | None:
    if state is None:
        return None
    return {"consumed_nonces": sorted(state.consumed_nonces)}


def decode_nonce_state(value: Mapping[str, Any] | None) -> NonceLedgerState | None:
    if value is None:
        return None
    return NonceLedgerState(frozenset(value["consumed_nonces"]))


def encode_committed_hashes(hashes: CommittedHashes | None) -> dict[str, Any] | None:
    if hashes is None:
        return None
    return {
        "transaction_sha256": hashes.transaction_sha256,
        "catalog_snapshot_sha256": hashes.catalog_snapshot_sha256,
    }


def decode_committed_hashes(
    value: Mapping[str, Any] | None,
) -> CommittedHashes | None:
    if value is None:
        return None
    return CommittedHashes(
        transaction_sha256=value["transaction_sha256"],
        catalog_snapshot_sha256=value["catalog_snapshot_sha256"],
    )


def encode_evaluation_inputs(inputs: EvaluationInputs) -> dict[str, Any]:
    return {
        "mandate": encode_mandate(inputs.mandate),
        "transaction": encode_transaction(inputs.transaction),
        "catalog_snapshot": encode_catalog(inputs.catalog_snapshot),
        "server_time": (
            None if inputs.server_time is None else encode_timestamp(inputs.server_time)
        ),
        "nonce_state": encode_nonce_state(inputs.nonce_state),
        "psp_committed_hashes": encode_committed_hashes(inputs.psp_committed_hashes),
        "replay_seed": inputs.replay_seed,
        "evaluated_at": encode_timestamp(inputs.evaluated_at),
    }


def decode_evaluation_inputs(value: Mapping[str, Any]) -> EvaluationInputs:
    server_time = value["server_time"]
    return EvaluationInputs(
        mandate=decode_mandate(value["mandate"]),
        transaction=decode_transaction(value["transaction"]),
        catalog_snapshot=decode_catalog(value["catalog_snapshot"]),
        server_time=None if server_time is None else decode_timestamp(server_time),
        nonce_state=decode_nonce_state(value["nonce_state"]),
        psp_committed_hashes=decode_committed_hashes(value["psp_committed_hashes"]),
        replay_seed=value["replay_seed"],
        evaluated_at=decode_timestamp(value["evaluated_at"]),
    )


def encode_target_expectation(target: TargetExpectation) -> dict[str, Any]:
    return {"family_id": target.family_id, "status": target.status}


def case_content_projection(case: BenchmarkCase) -> dict[str, Any]:
    """Exactly the material benchmark definition covered by the content digest."""

    projection = {
        "case_schema_version": case.case_schema_version,
        "evidence_tier": case.evidence_tier,
        "family_id": case.family_id,
        "provenance": case.provenance,
        "split": case.split,
        "ground_truth": case.ground_truth,
        "label_source": case.label_source,
        "expected_action": case.expected_action,
        "target_expectation": encode_target_expectation(case.target_expectation),
        "evaluation_inputs": encode_evaluation_inputs(case.evaluation_inputs),
    }
    if tuple(sorted(projection)) != tuple(sorted(CONTENT_HASH_FIELDS)):
        raise ValueError("case content projection does not match the registered fields")
    return projection


def case_content_sha256(case: BenchmarkCase) -> str:
    return sha256_canonical(case_content_projection(case))


def encode_generator_audit(audit: GeneratorAudit) -> dict[str, Any]:
    return {
        "generator_version": audit.generator_version,
        "generator_seed": audit.generator_seed,
        "recipe_id": audit.recipe_id,
        "recipe_parameters": dict(audit.recipe_parameters),
    }


def encode_case(case: BenchmarkCase) -> dict[str, Any]:
    """Full committed corpus record: hashed projection plus audit metadata."""

    record = dict(case_content_projection(case))
    record["case_id"] = case.case_id
    record["case_content_sha256"] = case_content_sha256(case)
    record["label_recorded_at"] = encode_timestamp(case.label_recorded_at)
    record["first_run_at"] = None
    record["generator"] = encode_generator_audit(case.generator)
    return record


def decode_case(value: Mapping[str, Any]) -> BenchmarkCase:
    generator = value["generator"]
    if value["first_run_at"] is not None:
        raise ValueError("the registered Tier A/B corpus must keep first_run_at null")
    case = BenchmarkCase(
        case_id=value["case_id"],
        case_schema_version=value["case_schema_version"],
        evidence_tier=value["evidence_tier"],
        family_id=value["family_id"],
        provenance=value["provenance"],
        split=value["split"],
        ground_truth=value["ground_truth"],
        label_source=value["label_source"],
        expected_action=value["expected_action"],
        target_expectation=TargetExpectation(
            family_id=value["target_expectation"]["family_id"],
            status=value["target_expectation"]["status"],
        ),
        evaluation_inputs=decode_evaluation_inputs(value["evaluation_inputs"]),
        label_recorded_at=decode_timestamp(value["label_recorded_at"]),
        generator=GeneratorAudit(
            generator_version=generator["generator_version"],
            generator_seed=generator["generator_seed"],
            recipe_id=generator["recipe_id"],
            recipe_parameters=dict(generator["recipe_parameters"]),
        ),
        first_run_at=None,
    )
    stored = value["case_content_sha256"]
    recomputed = case_content_sha256(case)
    if stored != recomputed:
        raise ValueError(
            f"case {case.case_id} content digest mismatch: "
            f"stored {stored}, recomputed {recomputed}"
        )
    return case


def case_record_line(case: BenchmarkCase) -> str:
    """One canonical JSON line: UTF-8, sorted keys, no insignificant whitespace."""

    return canonical_json_text(encode_case(case))
