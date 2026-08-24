"""Synthetic, non-benchmark fixtures for Tier C infrastructure tests.

Nothing here is Tier C benchmark content and nothing here may ever become
Tier C benchmark content. The constraint and evidence strings are deliberately
meaningless placeholders carrying an index, not semantic scenarios: they exist
only to exercise typing, hashing, quota, and selection machinery. Real Tier C
cases are authored by humans in D8-B and adjudicated without detector output.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from mandateguard.benchmark.tier_c.models import (
    AdjudicationRecord,
    DeveloperAuthoredOrigin,
    ExternalCorpusOrigin,
    FAMILY_CASE_ID_PREFIX,
    FAMILY_SEMANTIC_KINDS,
    FAMILY_SPLIT,
    GroundTruth,
    Provenance,
    SemanticEvidenceBundleRecord,
    SemanticEvidenceEntryRecord,
    SeparateModelOrigin,
    TierCAdjudication,
    TierCCase,
    TierCEvaluationInputs,
)
from mandateguard.core.nonce_ledger import NonceLedgerState
from mandateguard.models.mandate import Mandate, SemanticConstraint
from tests.factories import (
    SERVER_TIME,
    make_catalog,
    make_commitments,
    make_mandate,
    make_transaction,
)


AUTHORED_AT = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)
ADJUDICATED_AT = datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc)
FREEZE_AT = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)

#: Placeholder prompt digest. Not a real prompt and not derived from one.
SYNTHETIC_PROMPT_SHA256 = "0" * 64


def _kind_for(family_id: str) -> str:
    return sorted(FAMILY_SEMANTIC_KINDS[family_id])[0]


def _slug(marker: str) -> str:
    """Marker reduced to the frozen ``constraint_id`` character set."""

    cleaned = "".join(
        character if character.isalnum() or character in "._-" else "-"
        for character in marker
    )
    return cleaned[:48]


def make_semantic_mandate(family_id: str, marker: str) -> Mandate:
    """A mandate with one placeholder semantic constraint for ``family_id``."""

    mandate = make_mandate()
    constraint = SemanticConstraint(
        constraint_id=f"synthetic-{_slug(marker)}",
        kind=_kind_for(family_id),
        text=f"synthetic placeholder constraint text {marker}",
    )
    constraints = replace(mandate.payload.constraints, semantic=(constraint,))
    return replace(mandate, payload=replace(mandate.payload, constraints=constraints))


def make_semantic_evidence(marker: str) -> SemanticEvidenceBundleRecord:
    return SemanticEvidenceBundleRecord(
        merchant_id="merchant-1",
        entries=(
            SemanticEvidenceEntryRecord(
                evidence_id=f"synthetic-evidence-{_slug(marker)}",
                merchant_id="merchant-1",
                sku="sku-1",
                source_kind="product_description",
                text=f"synthetic placeholder evidence text {marker}",
            ),
        ),
    )


def make_evaluation_inputs(
    family_id: str, marker: str, *, mandate: Mandate | None = None
) -> TierCEvaluationInputs:
    transaction = make_transaction()
    catalog = make_catalog()
    return TierCEvaluationInputs(
        mandate=mandate if mandate is not None else make_semantic_mandate(family_id, marker),
        transaction=transaction,
        catalog_snapshot=catalog,
        server_time=SERVER_TIME,
        nonce_state=NonceLedgerState(frozenset()),
        psp_committed_hashes=make_commitments(transaction, catalog),
        replay_seed=1,
        evaluated_at=SERVER_TIME,
        semantic_evidence=make_semantic_evidence(marker),
    )


def make_origin(
    provenance: Provenance, *, authored_at: datetime = AUTHORED_AT
) -> DeveloperAuthoredOrigin | ExternalCorpusOrigin | SeparateModelOrigin:
    if provenance is Provenance.DEVELOPER_AUTHORED:
        return DeveloperAuthoredOrigin(authored_at=authored_at)
    if provenance is Provenance.EXTERNAL_DEFENSIVE_CORPUS_ADAPTED:
        return ExternalCorpusOrigin(
            authored_at=authored_at,
            source_selected_at=authored_at - timedelta(hours=1),
            source_name="synthetic-source",
            source_reference="https://example.invalid/synthetic",
            source_version="synthetic-v0",
            adaptation_description="synthetic adaptation description",
        )
    return SeparateModelOrigin(
        authored_at=authored_at,
        authoring_model_id="synthetic-authoring-model",
        authoring_prompt_sha256=SYNTHETIC_PROMPT_SHA256,
    )


def make_adjudication(
    ground_truth: GroundTruth = GroundTruth.VIOLATION,
    *,
    ambiguous: bool = False,
    second_label: GroundTruth | None = None,
    resolution_label: GroundTruth | None = None,
) -> TierCAdjudication:
    from mandateguard.benchmark.tier_c.models import ResolutionRecord

    primary = AdjudicationRecord(
        adjudicator_id="synthetic-adjudicator-a",
        label=ground_truth,
        ambiguous=ambiguous,
        adjudicated_at=ADJUDICATED_AT,
    )
    second = (
        None
        if second_label is None
        else AdjudicationRecord(
            adjudicator_id="synthetic-adjudicator-b",
            label=second_label,
            ambiguous=False,
            adjudicated_at=ADJUDICATED_AT + timedelta(hours=1),
        )
    )
    resolution = (
        None
        if resolution_label is None
        else ResolutionRecord(
            label=resolution_label,
            resolved_at=ADJUDICATED_AT + timedelta(hours=2),
            rationale="synthetic resolution rationale",
            adjudicator_ids=("synthetic-adjudicator-a", "synthetic-adjudicator-b"),
        )
    )
    return TierCAdjudication(primary=primary, second=second, resolution=resolution)


def make_case(
    *,
    family_id: str = "C-DEV-EXCLUSION",
    index: int = 1,
    ground_truth: GroundTruth = GroundTruth.VIOLATION,
    provenance: Provenance = Provenance.DEVELOPER_AUTHORED,
    marker: str | None = None,
    adjudication: TierCAdjudication | None = None,
    authored_at: datetime = AUTHORED_AT,
    split=None,
    exclusion=None,
    first_run_at: datetime | None = None,
    evaluation_inputs: TierCEvaluationInputs | None = None,
) -> TierCCase:
    """One synthetic Tier C case. Not benchmark content."""

    actual_marker = marker if marker is not None else f"{family_id}-{index}"
    return TierCCase(
        case_id=f"{FAMILY_CASE_ID_PREFIX[family_id]}-{index:03d}",
        case_schema_version="1.2",
        evidence_tier="C",
        family_id=family_id,
        provenance=provenance,
        provenance_origin=make_origin(provenance, authored_at=authored_at),
        split=FAMILY_SPLIT[family_id] if split is None else split,
        label_source="human_adjudication",
        evaluation_inputs=(
            evaluation_inputs
            if evaluation_inputs is not None
            else make_evaluation_inputs(family_id, actual_marker)
        ),
        adjudication=(
            adjudication
            if adjudication is not None
            else make_adjudication(ground_truth)
        ),
        exclusion=exclusion,
        first_run_at=first_run_at,
    )


def make_allocated_corpus(
    split_families: tuple[str, ...],
    *,
    authored_at: datetime = AUTHORED_AT,
    second_review_everything: bool = True,
) -> list[TierCCase]:
    """A synthetic corpus filling the registered allocation for some families.

    Every case is second-reviewed by default so that quota tests are not also
    testing second-review coverage.
    """

    from mandateguard.benchmark.tier_c.models import TIER_C_ALLOCATION

    cases: list[TierCCase] = []
    for family_id in split_families:
        index = 0
        for ground_truth in (GroundTruth.VIOLATION, GroundTruth.BENIGN):
            for provenance in (
                Provenance.DEVELOPER_AUTHORED,
                Provenance.EXTERNAL_DEFENSIVE_CORPUS_ADAPTED,
                Provenance.SEPARATE_MODEL_ADVERSARIAL,
            ):
                count = TIER_C_ALLOCATION[(family_id, ground_truth, provenance)]
                for _ in range(count):
                    index += 1
                    cases.append(
                        make_case(
                            family_id=family_id,
                            index=index,
                            ground_truth=ground_truth,
                            provenance=provenance,
                            authored_at=authored_at,
                            adjudication=make_adjudication(
                                ground_truth,
                                second_label=(
                                    ground_truth if second_review_everything else None
                                ),
                            ),
                        )
                    )
    return cases
