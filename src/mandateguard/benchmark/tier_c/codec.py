"""Canonical JSON codec and content-hash projection for Tier C cases.

Case-content hash projection
----------------------------

``case_content_sha256`` is the SHA-256 of the MandateGuard canonical JSON of
exactly this mapping, and of nothing else::

    {
      "case_schema_version": str,
      "evidence_tier": str,
      "family_id": str,
      "provenance": str,
      "provenance_origin": {...},
      "split": str,
      "ground_truth": str,
      "label_source": str,
      "evaluation_inputs": {..., "semantic_evidence": {...}}
    }

This satisfies the controlling policy in ``benchmark/MANIFEST.yaml``, whose
``field_rules.case_content_sha256`` requires the digest to cover *evaluation
inputs, family_id, evidence_tier, provenance, split, ground_truth, and
label_source*, and to exclude the audit-only ``first_run_at``. Protocol section
6 states the same set.

Two components go beyond that enumeration, and both are deliberate:

* ``case_schema_version`` binds the schema under which the content is to be
  read. The frozen, already-executed Tier A/B projection in
  ``mandateguard.benchmark.codec`` includes it, so this follows the established
  repository convention rather than inventing one.
* ``provenance_origin`` binds the origin fields that *materially define* the
  case - the external source identity and adaptation, or the authoring model
  and prompt digest. Protocol 3.1.1 makes provenance part of hashed content and
  requires that changing it produce a new digest; binding only the one-word
  provenance label while leaving the source identity unhashed would let the
  claimed source be swapped silently. Timestamps inside the origin are audit
  metadata and are excluded.

The manifest enumeration is therefore treated as a required floor with exactly
one explicit exclusion (``first_run_at``), never as a licence to drop a field.
Nothing the manifest requires hashed is excluded here.

``semantic_evidence`` is carried inside ``evaluation_inputs`` because it is
literally an authorization input to the frozen D5 semantic verifier, which is
where the manifest already places it.

Deliberately excluded as audit-only: ``case_id``, ``case_content_sha256``
itself, ``label_recorded_at``, ``first_run_at``, every adjudicator identity and
review timestamp, the authoring timestamps, and the exclusion record. Excluding
``case_id`` means identical benchmark content keeps one digest even if it is
re-identified, which is what makes replacement auditable.

Reuse note: the deterministic encoders are imported from the frozen D7 codec
rather than reimplemented, so Tier A/B and Tier C produce byte-identical
canonical JSON for the same mandate, transaction, or catalog. D7 is not
modified. Only ``decode_mandate`` needs a Tier C variant, because the D7
decoder rejects the semantic constraints that every Tier C case must carry.
"""

from __future__ import annotations

from typing import Any, Mapping

from mandateguard.benchmark.codec import (
    decode_catalog,
    decode_committed_hashes,
    decode_nonce_state,
    decode_timestamp,
    decode_transaction,
    encode_catalog,
    encode_committed_hashes,
    encode_mandate,
    encode_nonce_state,
    encode_timestamp,
    encode_transaction,
)
from mandateguard.benchmark.tier_c.models import (
    AdjudicationRecord,
    DeveloperAuthoredOrigin,
    ExclusionRecord,
    ExternalCorpusOrigin,
    GroundTruth,
    Provenance,
    ProvenanceOrigin,
    ResolutionRecord,
    SemanticEvidenceBundleRecord,
    SemanticEvidenceEntryRecord,
    SeparateModelOrigin,
    Split,
    TierCAdjudication,
    TierCCase,
    TierCCaseError,
    TierCEvaluationInputs,
)
from mandateguard.core.canonical import canonical_json_text
from mandateguard.core.hashing import sha256_canonical
from mandateguard.models.mandate import (
    HardConstraints,
    IssuerAttestation,
    Mandate,
    MandateConstraints,
    MandatePayload,
    SemanticConstraint,
)


CONTENT_HASH_FIELDS = (
    "case_schema_version",
    "evidence_tier",
    "family_id",
    "provenance",
    "provenance_origin",
    "split",
    "ground_truth",
    "label_source",
    "evaluation_inputs",
)

AUDIT_ONLY_FIELDS = (
    "case_id",
    "case_content_sha256",
    "label_recorded_at",
    "first_run_at",
    "adjudication",
    "exclusion",
)


def _decode_optional_tuple(values: object, name: str) -> tuple[str, ...] | None:
    if values is None:
        return None
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        raise TierCCaseError(f"{name} must be null or an array of strings")
    return tuple(values)


def decode_mandate(value: Mapping[str, Any]) -> Mandate:
    """Tier C mandate decoder: semantic constraints are required, not rejected."""

    payload = value["payload"]
    hard = payload["constraints"]["hard"]
    semantic = payload["constraints"]["semantic"]
    if not isinstance(semantic, list) or not semantic:
        raise TierCCaseError("a Tier C mandate must carry semantic constraints")
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
                semantic=tuple(
                    SemanticConstraint(
                        constraint_id=item["constraint_id"],
                        kind=item["kind"],
                        text=item["text"],
                    )
                    for item in semantic
                ),
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


def encode_semantic_evidence(bundle: SemanticEvidenceBundleRecord) -> dict[str, Any]:
    """Emit the exact wire shape of the frozen D5 ``SemanticEvidenceBundle``."""

    return {
        "merchant_id": bundle.merchant_id,
        "entries": [
            {
                "evidence_id": entry.evidence_id,
                "merchant_id": entry.merchant_id,
                "sku": entry.sku,
                "source_kind": entry.source_kind,
                "text": entry.text,
            }
            for entry in bundle.entries
        ],
    }


def decode_semantic_evidence(value: Mapping[str, Any]) -> SemanticEvidenceBundleRecord:
    entries = value["entries"]
    if not isinstance(entries, list):
        raise TierCCaseError("semantic_evidence.entries must be a JSON array")
    return SemanticEvidenceBundleRecord(
        merchant_id=value["merchant_id"],
        entries=tuple(
            SemanticEvidenceEntryRecord(
                evidence_id=entry["evidence_id"],
                merchant_id=entry["merchant_id"],
                sku=entry["sku"],
                source_kind=entry["source_kind"],
                text=entry["text"],
            )
            for entry in entries
        ),
    )


def encode_evaluation_inputs(inputs: TierCEvaluationInputs) -> dict[str, Any]:
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
        "semantic_evidence": encode_semantic_evidence(inputs.semantic_evidence),
    }


def decode_evaluation_inputs(value: Mapping[str, Any]) -> TierCEvaluationInputs:
    server_time = value["server_time"]
    return TierCEvaluationInputs(
        mandate=decode_mandate(value["mandate"]),
        transaction=decode_transaction(value["transaction"]),
        catalog_snapshot=decode_catalog(value["catalog_snapshot"]),
        server_time=None if server_time is None else decode_timestamp(server_time),
        nonce_state=decode_nonce_state(value["nonce_state"]),
        psp_committed_hashes=decode_committed_hashes(value["psp_committed_hashes"]),
        replay_seed=value["replay_seed"],
        evaluated_at=decode_timestamp(value["evaluated_at"]),
        semantic_evidence=decode_semantic_evidence(value["semantic_evidence"]),
    )


def encode_provenance_origin_content(origin: ProvenanceOrigin) -> dict[str, Any]:
    """The origin fields bound by the content digest.

    Authoring and source-selection timestamps are excluded: they are audit
    metadata, and the held-out isolation audit reads them from the record
    rather than from the digest.
    """

    if isinstance(origin, DeveloperAuthoredOrigin):
        return {}
    if isinstance(origin, ExternalCorpusOrigin):
        return {
            "source_name": origin.source_name,
            "source_reference": origin.source_reference,
            "source_version": origin.source_version,
            "adaptation_description": origin.adaptation_description,
        }
    if isinstance(origin, SeparateModelOrigin):
        return {
            "authoring_model_id": origin.authoring_model_id,
            "authoring_prompt_sha256": origin.authoring_prompt_sha256,
        }
    raise TierCCaseError("unknown provenance origin type")


def encode_provenance_origin(origin: ProvenanceOrigin) -> dict[str, Any]:
    """The full origin record, hashed fields plus audit timestamps."""

    record = dict(encode_provenance_origin_content(origin))
    record["authored_at"] = encode_timestamp(origin.authored_at)
    if isinstance(origin, ExternalCorpusOrigin):
        record["source_selected_at"] = encode_timestamp(origin.source_selected_at)
    return record


def decode_provenance_origin(
    provenance: Provenance, value: Mapping[str, Any]
) -> ProvenanceOrigin:
    authored_at = decode_timestamp(value["authored_at"])
    if provenance is Provenance.DEVELOPER_AUTHORED:
        return DeveloperAuthoredOrigin(authored_at=authored_at)
    if provenance is Provenance.EXTERNAL_DEFENSIVE_CORPUS_ADAPTED:
        return ExternalCorpusOrigin(
            authored_at=authored_at,
            source_selected_at=decode_timestamp(value["source_selected_at"]),
            source_name=value["source_name"],
            source_reference=value["source_reference"],
            source_version=value["source_version"],
            adaptation_description=value["adaptation_description"],
        )
    if provenance is Provenance.SEPARATE_MODEL_ADVERSARIAL:
        return SeparateModelOrigin(
            authored_at=authored_at,
            authoring_model_id=value["authoring_model_id"],
            authoring_prompt_sha256=value["authoring_prompt_sha256"],
        )
    raise TierCCaseError("unknown provenance value")


def case_content_projection(case: TierCCase) -> dict[str, Any]:
    """Exactly the material benchmark definition covered by the content digest.

    Raises if the case has no adjudicated label: protocol section 5 fixes the
    ordering as finalize, adjudicate, record the label, *then* hash.
    """

    ground_truth = case.ground_truth
    if ground_truth is None:
        raise TierCCaseError(
            f"case {case.case_id} has no adjudicated ground truth; a Tier C "
            "content digest may only be computed after the human label exists "
            "(protocol 5)"
        )
    projection = {
        "case_schema_version": case.case_schema_version,
        "evidence_tier": case.evidence_tier,
        "family_id": case.family_id,
        "provenance": case.provenance.value,
        "provenance_origin": encode_provenance_origin_content(case.provenance_origin),
        "split": case.split.value,
        "ground_truth": ground_truth.value,
        "label_source": case.label_source,
        "evaluation_inputs": encode_evaluation_inputs(case.evaluation_inputs),
    }
    if tuple(sorted(projection)) != tuple(sorted(CONTENT_HASH_FIELDS)):
        raise TierCCaseError("case content projection does not match registered fields")
    return projection


def case_content_sha256(case: TierCCase) -> str:
    return sha256_canonical(case_content_projection(case))


def encode_adjudication_record(record: AdjudicationRecord) -> dict[str, Any]:
    return {
        "adjudicator_id": record.adjudicator_id,
        "label": record.label.value,
        "ambiguous": record.ambiguous,
        "adjudicated_at": encode_timestamp(record.adjudicated_at),
    }


def decode_adjudication_record(value: Mapping[str, Any] | None) -> AdjudicationRecord | None:
    if value is None:
        return None
    return AdjudicationRecord(
        adjudicator_id=value["adjudicator_id"],
        label=GroundTruth(value["label"]),
        ambiguous=value["ambiguous"],
        adjudicated_at=decode_timestamp(value["adjudicated_at"]),
    )


def encode_adjudication(adjudication: TierCAdjudication) -> dict[str, Any]:
    resolution = adjudication.resolution
    return {
        "primary": (
            None
            if adjudication.primary is None
            else encode_adjudication_record(adjudication.primary)
        ),
        "second": (
            None
            if adjudication.second is None
            else encode_adjudication_record(adjudication.second)
        ),
        "resolution": (
            None
            if resolution is None
            else {
                "label": resolution.label.value,
                "resolved_at": encode_timestamp(resolution.resolved_at),
                "rationale": resolution.rationale,
                "adjudicator_ids": list(resolution.adjudicator_ids),
            }
        ),
        "status": adjudication.status.value,
    }


def decode_adjudication(value: Mapping[str, Any]) -> TierCAdjudication:
    raw_resolution = value["resolution"]
    return TierCAdjudication(
        primary=decode_adjudication_record(value["primary"]),
        second=decode_adjudication_record(value["second"]),
        resolution=(
            None
            if raw_resolution is None
            else ResolutionRecord(
                label=GroundTruth(raw_resolution["label"]),
                resolved_at=decode_timestamp(raw_resolution["resolved_at"]),
                rationale=raw_resolution["rationale"],
                adjudicator_ids=tuple(raw_resolution["adjudicator_ids"]),
            )
        ),
    )


def encode_case(case: TierCCase) -> dict[str, Any]:
    """Full committed corpus record: hashed projection plus audit metadata."""

    record = dict(case_content_projection(case))
    record["case_id"] = case.case_id
    record["case_content_sha256"] = case_content_sha256(case)
    label_recorded_at = case.label_recorded_at
    record["label_recorded_at"] = (
        None if label_recorded_at is None else encode_timestamp(label_recorded_at)
    )
    record["first_run_at"] = (
        None if case.first_run_at is None else encode_timestamp(case.first_run_at)
    )
    record["provenance_origin"] = encode_provenance_origin(case.provenance_origin)
    record["adjudication"] = encode_adjudication(case.adjudication)
    record["exclusion"] = (
        None
        if case.exclusion is None
        else {
            "reason": case.exclusion.reason,
            "excluded_at": encode_timestamp(case.exclusion.excluded_at),
        }
    )
    return record


def decode_case(value: Mapping[str, Any]) -> TierCCase:
    provenance = Provenance(value["provenance"])
    raw_exclusion = value["exclusion"]
    first_run_at = value["first_run_at"]
    case = TierCCase(
        case_id=value["case_id"],
        case_schema_version=value["case_schema_version"],
        evidence_tier=value["evidence_tier"],
        family_id=value["family_id"],
        provenance=provenance,
        provenance_origin=decode_provenance_origin(
            provenance, value["provenance_origin"]
        ),
        split=Split(value["split"]),
        label_source=value["label_source"],
        evaluation_inputs=decode_evaluation_inputs(value["evaluation_inputs"]),
        adjudication=decode_adjudication(value["adjudication"]),
        exclusion=(
            None
            if raw_exclusion is None
            else ExclusionRecord(
                reason=raw_exclusion["reason"],
                excluded_at=decode_timestamp(raw_exclusion["excluded_at"]),
            )
        ),
        first_run_at=None if first_run_at is None else decode_timestamp(first_run_at),
    )
    stored_ground_truth = value["ground_truth"]
    if case.ground_truth is None or case.ground_truth.value != stored_ground_truth:
        raise TierCCaseError(
            f"case {case.case_id} ground_truth does not match its adjudication record"
        )
    stored = value["case_content_sha256"]
    recomputed = case_content_sha256(case)
    if stored != recomputed:
        raise TierCCaseError(
            f"case {case.case_id} content digest mismatch: "
            f"stored {stored}, recomputed {recomputed}"
        )
    return case


def case_record_line(case: TierCCase) -> str:
    """One canonical JSON line: UTF-8, sorted keys, no insignificant whitespace."""

    return canonical_json_text(encode_case(case))
