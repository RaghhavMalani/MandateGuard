"""Generic development-concept fixtures for Tier C controller tests."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from mandateguard.core.nonce_ledger import NonceLedgerState
from mandateguard.models.mandate import Mandate, SemanticConstraint
from mandateguard.semantic.evidence import (
    SemanticEvidence,
    SemanticEvidenceBundle,
    SemanticEvidenceEntry,
    semantic_evidence_sha256,
)
from mandateguard.semantic.models import SemanticRequest
from tests.factories import (
    SERVER_TIME,
    make_catalog,
    make_commitments,
    make_mandate,
    make_transaction,
)


def make_constraints() -> tuple[SemanticConstraint, ...]:
    return (
        SemanticConstraint(
            constraint_id="purpose-1",
            kind="purpose",
            text="The purchase must be intended for individual study.",
        ),
        SemanticConstraint(
            constraint_id="exclusion-1",
            kind="exclusion",
            text="The purchase must not include a recurring subscription.",
        ),
    )


def make_semantic_mandate(
    constraints: tuple[SemanticConstraint, ...] | None = None,
) -> Mandate:
    mandate = make_mandate()
    updated_constraints = replace(
        mandate.payload.constraints,
        semantic=constraints if constraints is not None else make_constraints(),
    )
    return replace(
        mandate,
        payload=replace(mandate.payload, constraints=updated_constraints),
    )


def make_semantic_bundle(
    *, instruction_text: str | None = None
) -> SemanticEvidenceBundle:
    return SemanticEvidenceBundle(
        merchant_id="merchant-1",
        entries=(
            SemanticEvidenceEntry(
                evidence_id="terms-v1",
                merchant_id="merchant-1",
                sku=None,
                source_kind="merchant_terms",
                text="Orders are one-time unless product terms say otherwise.",
            ),
            SemanticEvidenceEntry(
                evidence_id="sku-1-v1",
                merchant_id="merchant-1",
                sku="sku-1",
                source_kind="product_description",
                text=instruction_text
                or "A one-time digital reference guide for individual study.",
            ),
            SemanticEvidenceEntry(
                evidence_id="unselected-v1",
                merchant_id="merchant-1",
                sku="sku-unselected",
                source_kind="product_description",
                text="A separate one-time study reference.",
            ),
        ),
    )


def make_semantic_evidence(
    *, instruction_text: str | None = None
) -> SemanticEvidence:
    bundle = make_semantic_bundle(instruction_text=instruction_text)
    return SemanticEvidence(
        bundle=bundle,
        semantic_evidence_sha256=semantic_evidence_sha256(bundle),
    )


def valid_authorization_inputs(mandate: Mandate | None = None) -> dict[str, object]:
    transaction = make_transaction()
    catalog = make_catalog()
    return {
        "mandate": mandate or make_semantic_mandate(),
        "transaction": transaction,
        "catalog_snapshot": catalog,
        "server_time": SERVER_TIME,
        "nonce_state": NonceLedgerState(),
        "committed_hashes": make_commitments(transaction, catalog),
        "replay_seed": 501,
        "evaluated_at": SERVER_TIME,
    }


@dataclass
class ScriptedSemanticModel:
    response: object
    model_id: str = "semantic-test-model-v1"
    exception: Exception | None = None
    calls: list[SemanticRequest] = field(default_factory=list)

    def evaluate(self, request: SemanticRequest) -> object:
        self.calls.append(request)
        if self.exception is not None:
            raise self.exception
        return self.response


def model_output(*statuses: str) -> dict[str, object]:
    constraints = make_constraints()
    return {
        "constraint_results": [
            {
                "constraint_id": constraint.constraint_id,
                "status": status,
                "reason": f"bounded {status.lower()} reason",
            }
            for constraint, status in zip(constraints, statuses, strict=True)
        ]
    }
