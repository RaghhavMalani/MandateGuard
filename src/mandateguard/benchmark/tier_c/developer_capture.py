"""Deterministic capture tooling for human-authored Tier C development drafts.

This module supplies only non-semantic scaffolding.  The benchmark author must
write both semantic fields in the worksheet.  A blank worksheet row is not a
candidate, and a partially populated semantic pair is rejected rather than
completed.  No authoring intent is converted into adjudication metadata.

The resulting records are deliberately *candidate drafts*, not committed Tier
C corpus records: they have no human ground truth and therefore no final
``case_content_sha256``.  They are serialized with a distinct candidate schema
outside ``benchmark/cases/tier_c``.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import io
from pathlib import Path
from typing import Iterable
from uuid import UUID, uuid5

from mandateguard.benchmark.tier_c.codec import (
    encode_adjudication,
    encode_evaluation_inputs,
    encode_provenance_origin,
)
from mandateguard.benchmark.tier_c.models import (
    CASE_SCHEMA_VERSION,
    EVIDENCE_TIER,
    FAMILY_CASE_ID_PREFIX,
    LABEL_SOURCE,
    DeveloperAuthoredOrigin,
    Provenance,
    SemanticEvidenceBundleRecord,
    SemanticEvidenceEntryRecord,
    Split,
    TierCAdjudication,
    TierCCase,
    TierCCaseError,
    TierCEvaluationInputs,
)
from mandateguard.core.canonical import canonical_json_text
from mandateguard.core.hashing import (
    CommittedHashes,
    catalog_snapshot_sha256,
    transaction_body_sha256,
)
from mandateguard.core.nonce_ledger import NonceLedgerState
from mandateguard.models.catalog import CatalogItem, CatalogSnapshot
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


CANDIDATE_SCHEMA_VERSION = "1.0"

WORKSHEET_FIELDS = (
    "case_id",
    "family_id",
    "authoring_intent",
    "semantic_constraint_text",
    "semantic_evidence_text",
    "optional_author_note",
)

DEVELOPER_FAMILY_COUNTS = {
    "C-DEV-RECURRENCE": 30,
    "C-DEV-EXCLUSION": 29,
    "C-DEV-PURPOSE": 29,
}

DEVELOPER_INTENT_QUOTAS = {
    ("C-DEV-RECURRENCE", "violation_intended"): 16,
    ("C-DEV-RECURRENCE", "benign_intended"): 14,
    ("C-DEV-EXCLUSION", "violation_intended"): 16,
    ("C-DEV-EXCLUSION", "benign_intended"): 13,
    ("C-DEV-PURPOSE", "violation_intended"): 16,
    ("C-DEV-PURPOSE", "benign_intended"): 13,
}

# The kind tag is schema scaffolding selected mechanically from the family.  It
# does not supply or alter the semantic statement written by the human author.
FAMILY_CONSTRAINT_KIND = {
    "C-DEV-RECURRENCE": "obligation",
    "C-DEV-EXCLUSION": "exclusion",
    "C-DEV-PURPOSE": "purpose",
}

_FAMILY_ORDER = tuple(DEVELOPER_FAMILY_COUNTS)
_FAMILY_PREFIX = {
    family_id: FAMILY_CASE_ID_PREFIX[family_id] for family_id in _FAMILY_ORDER
}
_EXPECTED_CASE_FAMILY = {
    f"{_FAMILY_PREFIX[family_id]}-{index:03d}": family_id
    for family_id, count in DEVELOPER_FAMILY_COUNTS.items()
    for index in range(1, count + 1)
}

_ENVELOPE_NAMESPACE = UUID("4aa991d9-3258-45af-9c8b-2740ec4135c8")
_ISSUED_AT = datetime(2030, 1, 1, 0, 0, tzinfo=timezone.utc)
_EVALUATED_AT = datetime(2030, 6, 1, 0, 0, tzinfo=timezone.utc)
_EXPIRES_AT = datetime(2031, 1, 1, 0, 0, tzinfo=timezone.utc)
_MERCHANT_ID = "tier-c-clean-merchant"
_SKU = "tier-c-clean-sku"
_UNIT_PRICE_MINOR = 100
_QUANTITY = 1


class DeveloperCaptureError(TierCCaseError):
    """Raised when a developer-authoring worksheet or candidate is invalid."""


class AuthoringIntent(str, Enum):
    """Requested class for authoring allocation; never a ground-truth label."""

    VIOLATION_INTENDED = "violation_intended"
    BENIGN_INTENDED = "benign_intended"


class AuthoringMode(str, Enum):
    """Worksheet completeness level."""

    PARTIAL = "partial"
    FINAL_CANDIDATES = "final_candidates"


@dataclass(frozen=True, slots=True)
class DeveloperWorksheetRow:
    """One human-editable worksheet row, whether blank or complete."""

    case_id: str
    family_id: str
    authoring_intent: AuthoringIntent
    semantic_constraint_text: str
    semantic_evidence_text: str
    optional_author_note: str

    @property
    def is_blank(self) -> bool:
        """Whether the row contains no semantic input and is not a candidate."""

        return not self.semantic_constraint_text and not self.semantic_evidence_text

    @property
    def is_complete(self) -> bool:
        return bool(self.semantic_constraint_text) and bool(self.semantic_evidence_text)


@dataclass(frozen=True, slots=True)
class DeveloperCandidate:
    """Captured authoring metadata plus an unadjudicated frozen Tier C model."""

    authoring_intent: AuthoringIntent
    optional_author_note: str
    tier_c_case: TierCCase

    def __post_init__(self) -> None:
        case = self.tier_c_case
        if case.provenance is not Provenance.DEVELOPER_AUTHORED:
            raise DeveloperCaptureError("candidate provenance must be developer_authored")
        if not isinstance(case.provenance_origin, DeveloperAuthoredOrigin):
            raise DeveloperCaptureError(
                "developer candidate must carry DeveloperAuthoredOrigin"
            )
        if case.split is not Split.DEV:
            raise DeveloperCaptureError("developer candidate must use split=dev")
        if case.ground_truth is not None or case.adjudication != TierCAdjudication():
            raise DeveloperCaptureError(
                "authoring candidates must not carry adjudication or ground truth"
            )
        if case.first_run_at is not None or case.exclusion is not None:
            raise DeveloperCaptureError(
                "authoring candidates must be unexecuted and not excluded"
            )


def expected_case_ids() -> tuple[str, ...]:
    """The 88 neutral developer-candidate IDs in worksheet order."""

    return tuple(_EXPECTED_CASE_FAMILY)


def _require_verbatim_semantic_text(value: str, field_name: str, maximum: int) -> None:
    if not value:
        raise DeveloperCaptureError(f"{field_name} is required; it is never auto-filled")
    if value != value.strip():
        raise DeveloperCaptureError(
            f"{field_name} has surrounding whitespace; correct it explicitly so "
            "capture does not transform semantic text"
        )
    if len(value) > maximum:
        raise DeveloperCaptureError(
            f"{field_name} must be at most {maximum} characters"
        )


def _parse_row(raw: dict[str, str | None], row_number: int) -> DeveloperWorksheetRow:
    if any(value is None for value in raw.values()):
        raise DeveloperCaptureError(
            f"worksheet row {row_number} has fewer columns than the header"
        )
    values = {name: raw[name] for name in WORKSHEET_FIELDS}
    case_id = values["case_id"]
    family_id = values["family_id"]
    if not case_id or not family_id or not values["authoring_intent"]:
        raise DeveloperCaptureError(
            f"worksheet row {row_number} requires case_id, family_id, and "
            "authoring_intent"
        )
    try:
        intent = AuthoringIntent(values["authoring_intent"])
    except ValueError as error:
        raise DeveloperCaptureError(
            f"worksheet row {row_number} has invalid authoring_intent "
            f"{values['authoring_intent']!r}"
        ) from error
    return DeveloperWorksheetRow(
        case_id=case_id,
        family_id=family_id,
        authoring_intent=intent,
        semantic_constraint_text=values["semantic_constraint_text"],
        semantic_evidence_text=values["semantic_evidence_text"],
        optional_author_note=values["optional_author_note"],
    )


def parse_worksheet_text(text: str) -> tuple[DeveloperWorksheetRow, ...]:
    """Parse the exact six-column TSV format without changing field text."""

    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter="\t")
    if tuple(reader.fieldnames or ()) != WORKSHEET_FIELDS:
        raise DeveloperCaptureError(
            "worksheet header must be exactly: " + "\\t".join(WORKSHEET_FIELDS)
        )
    return tuple(_parse_row(raw, row_number) for row_number, raw in enumerate(reader, 2))


def load_worksheet(path: Path) -> tuple[DeveloperWorksheetRow, ...]:
    """Read one UTF-8 TSV worksheet."""

    try:
        with path.open("r", encoding="utf-8", newline="") as stream:
            text = stream.read()
    except OSError as error:
        raise DeveloperCaptureError(f"cannot read worksheet {path}: {error}") from error
    return parse_worksheet_text(text)


def validate_worksheet(
    rows: Iterable[DeveloperWorksheetRow], mode: AuthoringMode
) -> tuple[DeveloperWorksheetRow, ...]:
    """Validate IDs, quotas, and semantic completeness for an authoring mode.

    Partial mode permits omitted rows and entirely blank semantic pairs.  A
    blank row remains scaffolding only.  Once either semantic field is present,
    both must be present and are validated verbatim.
    """

    if not isinstance(mode, AuthoringMode):
        raise DeveloperCaptureError("mode must be an AuthoringMode")
    row_list = tuple(rows)
    if not all(isinstance(row, DeveloperWorksheetRow) for row in row_list):
        raise DeveloperCaptureError("rows must contain DeveloperWorksheetRow values")

    seen: set[str] = set()
    intent_counts: dict[tuple[str, str], int] = {}
    for row in row_list:
        expected_family = _EXPECTED_CASE_FAMILY.get(row.case_id)
        if expected_family is None:
            raise DeveloperCaptureError(
                f"case_id {row.case_id!r} is not one of the 88 neutral developer IDs"
            )
        if row.case_id in seen:
            raise DeveloperCaptureError(f"duplicate case_id {row.case_id}")
        seen.add(row.case_id)
        if row.family_id != expected_family:
            raise DeveloperCaptureError(
                f"case_id {row.case_id} requires family_id={expected_family}"
            )

        quota_key = (row.family_id, row.authoring_intent.value)
        intent_counts[quota_key] = intent_counts.get(quota_key, 0) + 1
        quota = DEVELOPER_INTENT_QUOTAS[quota_key]
        if intent_counts[quota_key] > quota:
            raise DeveloperCaptureError(
                f"authoring intent quota exceeded for {row.family_id}/"
                f"{row.authoring_intent.value}: {intent_counts[quota_key]} > {quota}"
            )

        has_constraint = bool(row.semantic_constraint_text)
        has_evidence = bool(row.semantic_evidence_text)
        if has_constraint != has_evidence:
            missing = (
                "semantic_evidence_text" if has_constraint else "semantic_constraint_text"
            )
            raise DeveloperCaptureError(
                f"case {row.case_id} is missing {missing}; semantic input is never "
                "auto-filled"
            )
        if row.is_complete:
            _require_verbatim_semantic_text(
                row.semantic_constraint_text, "semantic_constraint_text", 1000
            )
            _require_verbatim_semantic_text(
                row.semantic_evidence_text, "semantic_evidence_text", 20_000
            )
        elif mode is AuthoringMode.FINAL_CANDIDATES:
            raise DeveloperCaptureError(
                f"case {row.case_id} has blank semantic fields; a blank row is not "
                "a Tier C candidate"
            )
        if len(row.optional_author_note) > 4096:
            raise DeveloperCaptureError(
                f"case {row.case_id} optional_author_note exceeds 4096 characters"
            )

    if mode is AuthoringMode.FINAL_CANDIDATES:
        missing_ids = set(_EXPECTED_CASE_FAMILY) - seen
        if missing_ids:
            raise DeveloperCaptureError(
                f"final candidate mode requires all 88 rows; missing {len(missing_ids)}"
            )
        for quota_key, quota in DEVELOPER_INTENT_QUOTAS.items():
            actual = intent_counts.get(quota_key, 0)
            if actual != quota:
                raise DeveloperCaptureError(
                    f"authoring intent quota mismatch for {quota_key[0]}/"
                    f"{quota_key[1]}: {actual} != {quota}"
                )
    return row_list


def _clean_transaction(case_id: str) -> Transaction:
    line = TransactionLine(
        sku=_SKU,
        effective_unit_price_minor=_UNIT_PRICE_MINOR,
        quantity=_QUANTITY,
        line_total_minor=_UNIT_PRICE_MINOR * _QUANTITY,
        recurring=False,
    )
    payload = TransactionPayload(
        transaction_id=f"tier-c-candidate-{case_id.lower()}",
        merchant_id=_MERCHANT_ID,
        cart_currency="USD",
        order_currency="USD",
        declared_order_total_minor=line.line_total_minor,
        declared_aggregate_quantity=line.quantity,
        cart_recurring=False,
        order_recurring=False,
        lines=(line,),
    )
    return Transaction(
        payload=payload,
        declared_transaction_hash=transaction_body_sha256(payload),
    )


def _clean_catalog() -> CatalogSnapshot:
    return CatalogSnapshot(
        snapshot_id="tier-c-clean-envelope-v1",
        merchant_id=_MERCHANT_ID,
        currency="USD",
        items=(
            CatalogItem(
                sku=_SKU,
                merchant_id=_MERCHANT_ID,
                effective_unit_price_minor=_UNIT_PRICE_MINOR,
                recurring=False,
            ),
        ),
    )


def _clean_mandate(row: DeveloperWorksheetRow) -> Mandate:
    semantic = SemanticConstraint(
        constraint_id=f"human-{row.case_id.lower()}",
        kind=FAMILY_CONSTRAINT_KIND[row.family_id],
        text=row.semantic_constraint_text,
    )
    return Mandate(
        payload=MandatePayload(
            mandate_id=str(uuid5(_ENVELOPE_NAMESPACE, row.case_id)),
            nonce=f"tier-c-authoring-{row.case_id.lower()}",
            issued_at=_ISSUED_AT,
            expires_at=_EXPIRES_AT,
            subject_ref="tier-c-authoring-subject",
            currency="USD",
            constraints=MandateConstraints(
                hard=HardConstraints(
                    max_total_minor=10_000,
                    max_quantity=10,
                    recurring_allowed=True,
                    merchant_allowlist=(_MERCHANT_ID,),
                    sku_allowlist=(_SKU,),
                ),
                semantic=(semantic,),
            ),
        ),
        issuer_attestation=IssuerAttestation(
            assurance="DECLARED_ONLY",
            issuer_id="tier-c-authoring-issuer",
        ),
    )


def _capture_row(
    row: DeveloperWorksheetRow, authored_at: datetime
) -> DeveloperCandidate:
    if not row.is_complete:
        raise DeveloperCaptureError(
            f"case {row.case_id} has missing semantic input; it is never auto-filled"
        )
    _require_verbatim_semantic_text(
        row.semantic_constraint_text, "semantic_constraint_text", 1000
    )
    _require_verbatim_semantic_text(
        row.semantic_evidence_text, "semantic_evidence_text", 20_000
    )
    transaction = _clean_transaction(row.case_id)
    catalog = _clean_catalog()
    inputs = TierCEvaluationInputs(
        mandate=_clean_mandate(row),
        transaction=transaction,
        catalog_snapshot=catalog,
        server_time=_EVALUATED_AT,
        nonce_state=NonceLedgerState(),
        psp_committed_hashes=CommittedHashes(
            transaction_sha256=transaction_body_sha256(transaction),
            catalog_snapshot_sha256=catalog_snapshot_sha256(catalog),
        ),
        replay_seed=0,
        evaluated_at=_EVALUATED_AT,
        semantic_evidence=SemanticEvidenceBundleRecord(
            merchant_id=_MERCHANT_ID,
            entries=(
                SemanticEvidenceEntryRecord(
                    evidence_id=f"human-evidence-{row.case_id.lower()}",
                    merchant_id=_MERCHANT_ID,
                    sku=_SKU,
                    source_kind="product_description",
                    text=row.semantic_evidence_text,
                ),
            ),
        ),
    )
    case = TierCCase(
        case_id=row.case_id,
        case_schema_version=CASE_SCHEMA_VERSION,
        evidence_tier=EVIDENCE_TIER,
        family_id=row.family_id,
        provenance=Provenance.DEVELOPER_AUTHORED,
        provenance_origin=DeveloperAuthoredOrigin(authored_at=authored_at),
        split=Split.DEV,
        label_source=LABEL_SOURCE,
        evaluation_inputs=inputs,
        adjudication=TierCAdjudication(),
    )
    return DeveloperCandidate(
        authoring_intent=row.authoring_intent,
        optional_author_note=row.optional_author_note,
        tier_c_case=case,
    )


def capture_candidates(
    rows: Iterable[DeveloperWorksheetRow], mode: AuthoringMode
) -> tuple[DeveloperCandidate, ...]:
    """Capture complete rows at the current UTC instant; skip blank partial rows."""

    validated = validate_worksheet(rows, mode)
    authored_at = datetime.now(timezone.utc)
    return tuple(
        _capture_row(row, authored_at) for row in validated if row.is_complete
    )


def candidate_record(candidate: DeveloperCandidate) -> dict[str, object]:
    """Serialize a draft without inventing adjudication or final hash fields."""

    if not isinstance(candidate, DeveloperCandidate):
        raise DeveloperCaptureError("candidate must be a DeveloperCandidate")
    case = candidate.tier_c_case
    return {
        "candidate_schema_version": CANDIDATE_SCHEMA_VERSION,
        "authoring_intent": candidate.authoring_intent.value,
        "optional_author_note": candidate.optional_author_note,
        "tier_c_case": {
            "case_id": case.case_id,
            "case_schema_version": case.case_schema_version,
            "evidence_tier": case.evidence_tier,
            "family_id": case.family_id,
            "provenance": case.provenance.value,
            "provenance_origin": encode_provenance_origin(case.provenance_origin),
            "split": case.split.value,
            "label_source": case.label_source,
            "evaluation_inputs": encode_evaluation_inputs(case.evaluation_inputs),
            "adjudication": encode_adjudication(case.adjudication),
            "exclusion": None,
            "first_run_at": None,
        },
    }


def candidate_record_line(candidate: DeveloperCandidate) -> str:
    """One canonical JSONL candidate draft line."""

    return canonical_json_text(candidate_record(candidate))


def validate_clean_envelope(candidate: DeveloperCandidate) -> None:
    """Assert the fixed non-semantic envelope's deterministic clean relations.

    This is a structural check only.  It intentionally does not execute any
    authorization policy or semantic evaluation path.
    """

    inputs = candidate.tier_c_case.evaluation_inputs
    mandate = inputs.mandate.payload
    transaction = inputs.transaction
    payload = transaction.payload
    catalog = inputs.catalog_snapshot
    commitments = inputs.psp_committed_hashes
    if catalog is None or commitments is None or inputs.nonce_state is None:
        raise DeveloperCaptureError("clean envelope requires complete fixed inputs")
    if not (mandate.issued_at <= inputs.evaluated_at < mandate.expires_at):
        raise DeveloperCaptureError("clean envelope evaluation time is outside mandate")
    if inputs.server_time != inputs.evaluated_at:
        raise DeveloperCaptureError("clean envelope server time mismatch")
    if inputs.nonce_state.is_consumed(mandate.nonce):
        raise DeveloperCaptureError("clean envelope nonce is already consumed")
    if not (
        mandate.currency == payload.cart_currency == payload.order_currency == catalog.currency
    ):
        raise DeveloperCaptureError("clean envelope currency mismatch")
    if payload.merchant_id not in (mandate.constraints.hard.merchant_allowlist or ()):
        raise DeveloperCaptureError("clean envelope merchant is not allowed")
    if catalog.merchant_id != payload.merchant_id:
        raise DeveloperCaptureError("clean envelope catalog merchant mismatch")
    if payload.cart_recurring or payload.order_recurring:
        raise DeveloperCaptureError("clean envelope recurrence flags must be false")
    if not mandate.constraints.hard.recurring_allowed:
        raise DeveloperCaptureError("clean envelope must allow recurrence")
    line_total = sum(line.line_total_minor for line in payload.lines)
    quantity = sum(line.quantity for line in payload.lines)
    if line_total != payload.declared_order_total_minor:
        raise DeveloperCaptureError("clean envelope total arithmetic mismatch")
    if quantity != payload.declared_aggregate_quantity:
        raise DeveloperCaptureError("clean envelope quantity arithmetic mismatch")
    if line_total > mandate.constraints.hard.max_total_minor:
        raise DeveloperCaptureError("clean envelope exceeds mandate total")
    if quantity > mandate.constraints.hard.max_quantity:
        raise DeveloperCaptureError("clean envelope exceeds mandate quantity")
    for line in payload.lines:
        item = catalog.item_by_sku(line.sku)
        if item is None:
            raise DeveloperCaptureError("clean envelope SKU is absent from catalog")
        if line.recurring:
            raise DeveloperCaptureError("clean envelope line recurrence must be false")
        if line.sku not in (mandate.constraints.hard.sku_allowlist or ()):
            raise DeveloperCaptureError("clean envelope SKU is not allowed")
        if line.line_total_minor != line.effective_unit_price_minor * line.quantity:
            raise DeveloperCaptureError("clean envelope line arithmetic mismatch")
        if (
            item.effective_unit_price_minor != line.effective_unit_price_minor
            or item.recurring != line.recurring
            or item.merchant_id != payload.merchant_id
        ):
            raise DeveloperCaptureError("clean envelope catalog item mismatch")
    if transaction.declared_transaction_hash != transaction_body_sha256(transaction):
        raise DeveloperCaptureError("clean envelope declared transaction hash mismatch")
    if commitments.transaction_sha256 != transaction_body_sha256(transaction):
        raise DeveloperCaptureError("clean envelope transaction commitment mismatch")
    if commitments.catalog_snapshot_sha256 != catalog_snapshot_sha256(catalog):
        raise DeveloperCaptureError("clean envelope catalog commitment mismatch")
