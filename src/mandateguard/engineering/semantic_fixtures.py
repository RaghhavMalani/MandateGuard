"""Typed non-benchmark semantic MVP fixtures and deterministic scenarios.

These fixtures are mutable engineering data.  They are intentionally isolated
from every registered benchmark model, manifest, corpus, result, and lifecycle.
Loading and validating this module does not import semantic execution code.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping
from uuid import UUID, uuid5

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


FIXTURE_SCHEMA_VERSION = "1.0"
FIXTURE_COUNT = 72
FAMILY_COUNT = 24
EXPECTATION_PER_FAMILY = 8
DEMO_COUNT = 9
DEMO_PER_FAMILY = 3

FIXTURE_FIELDS = frozenset(
    {
        "fixture_id",
        "fixture_schema_version",
        "family",
        "difficulty",
        "engineering_expectation",
        "semantic_constraint_text",
        "semantic_evidence",
        "developer_rationale",
        "demo_priority",
    }
)
EVIDENCE_FIELDS = frozenset({"merchant_id", "entries"})
EVIDENCE_ENTRY_FIELDS = frozenset(
    {"evidence_id", "merchant_id", "sku", "source_kind", "text"}
)
BENCHMARK_ONLY_FIELDS = frozenset(
    {
        "ground_truth",
        "benchmark_label",
        "registered_label",
        "first_run_at",
        "case_content_sha256",
        "case_id",
        "case_schema_version",
        "evidence_tier",
        "family_id",
        "provenance",
        "provenance_origin",
        "split",
        "label_source",
        "expected_action",
        "adjudication",
        "exclusion",
    }
)

_FIXTURE_ID_RE = re.compile(
    r"^SMVP-(REC|EXC|PUR)-(PASS|VIOLATION|ABSTAIN)-00[1-8]$"
)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_SOURCE_KIND_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_FAMILY_CODE = {
    "RECURRENCE": "REC",
    "EXCLUSION": "EXC",
    "PURPOSE": "PUR",
}
_ENGINEERING_MERCHANT = "engineering-merchant"
_ENGINEERING_SKU = "engineering-sku"
_ENVELOPE_NAMESPACE = UUID("3ec57b0c-ed8c-4b97-a968-833837491b47")
_ISSUED_AT = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
_EVALUATED_AT = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
_EXPIRES_AT = datetime(2030, 1, 1, 0, 0, tzinfo=timezone.utc)
_UNIT_PRICE_MINOR = 2_500
_QUANTITY = 1


class SemanticMvpFixtureError(ValueError):
    """A non-benchmark engineering fixture is malformed or incomplete."""


class SemanticFamily(str, Enum):
    RECURRENCE = "RECURRENCE"
    EXCLUSION = "EXCLUSION"
    PURPOSE = "PURPOSE"


class FixtureDifficulty(str, Enum):
    CLEAR = "clear"
    HARD = "hard"
    AMBIGUOUS = "ambiguous"


class EngineeringExpectation(str, Enum):
    PASS = "PASS"
    VIOLATION = "VIOLATION"
    ABSTAIN = "ABSTAIN"


def _require_text(value: object, name: str, maximum: int) -> None:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise SemanticMvpFixtureError(
            f"{name} must be a non-empty string of at most {maximum} characters"
        )


@dataclass(frozen=True, slots=True)
class EngineeringEvidenceEntry:
    """The frozen D5 evidence-entry wire shape, held as inert fixture data."""

    evidence_id: str
    merchant_id: str
    sku: str | None
    source_kind: str
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_id, str) or not _IDENTIFIER_RE.fullmatch(
            self.evidence_id
        ):
            raise SemanticMvpFixtureError("evidence_id must be a bounded identifier")
        if self.merchant_id != _ENGINEERING_MERCHANT:
            raise SemanticMvpFixtureError(
                f"evidence merchant must be {_ENGINEERING_MERCHANT}"
            )
        if self.sku not in {None, _ENGINEERING_SKU}:
            raise SemanticMvpFixtureError(
                f"evidence sku must be null or {_ENGINEERING_SKU}"
            )
        if not isinstance(self.source_kind, str) or not _SOURCE_KIND_RE.fullmatch(
            self.source_kind
        ):
            raise SemanticMvpFixtureError(
                "source_kind must be a generic lowercase identifier"
            )
        _require_text(self.text, "semantic evidence text", 20_000)


@dataclass(frozen=True, slots=True)
class EngineeringEvidenceBundle:
    """One complete trusted evidence bundle in the frozen D5 wire shape."""

    merchant_id: str
    entries: tuple[EngineeringEvidenceEntry, ...]

    def __post_init__(self) -> None:
        if self.merchant_id != _ENGINEERING_MERCHANT:
            raise SemanticMvpFixtureError(
                f"bundle merchant must be {_ENGINEERING_MERCHANT}"
            )
        if (
            not isinstance(self.entries, tuple)
            or not 1 <= len(self.entries) <= 3
            or not all(
                isinstance(entry, EngineeringEvidenceEntry)
                for entry in self.entries
            )
        ):
            raise SemanticMvpFixtureError(
                "semantic evidence must contain one to three valid entries"
            )
        if any(entry.merchant_id != self.merchant_id for entry in self.entries):
            raise SemanticMvpFixtureError(
                "every evidence entry must belong to the bundle merchant"
            )
        evidence_ids = [entry.evidence_id for entry in self.entries]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise SemanticMvpFixtureError("evidence IDs must be unique")
        content_keys = [
            (entry.sku, entry.source_kind, entry.text) for entry in self.entries
        ]
        if len(content_keys) != len(set(content_keys)):
            raise SemanticMvpFixtureError("semantic evidence entries must be distinct")


@dataclass(frozen=True, slots=True)
class SemanticMvpFixture:
    """One AI-assisted, non-benchmark engineering fixture."""

    fixture_id: str
    fixture_schema_version: str
    family: SemanticFamily
    difficulty: FixtureDifficulty
    engineering_expectation: EngineeringExpectation
    semantic_constraint_text: str
    semantic_evidence: EngineeringEvidenceBundle
    developer_rationale: str
    demo_priority: bool

    def __post_init__(self) -> None:
        if not isinstance(self.fixture_id, str) or not _FIXTURE_ID_RE.fullmatch(
            self.fixture_id
        ):
            raise SemanticMvpFixtureError(
                "fixture_id must use the neutral SMVP engineering format"
            )
        if self.fixture_schema_version != FIXTURE_SCHEMA_VERSION:
            raise SemanticMvpFixtureError(
                f"fixture_schema_version must be {FIXTURE_SCHEMA_VERSION}"
            )
        if not isinstance(self.family, SemanticFamily):
            raise SemanticMvpFixtureError("family is not registered")
        if not isinstance(self.difficulty, FixtureDifficulty):
            raise SemanticMvpFixtureError("difficulty is not registered")
        if not isinstance(self.engineering_expectation, EngineeringExpectation):
            raise SemanticMvpFixtureError(
                "engineering_expectation is not registered"
            )
        expected_prefix = (
            f"SMVP-{_FAMILY_CODE[self.family.value]}-"
            f"{self.engineering_expectation.value}-"
        )
        if not self.fixture_id.startswith(expected_prefix):
            raise SemanticMvpFixtureError(
                "fixture_id family/expectation segments do not match the record"
            )
        _require_text(
            self.semantic_constraint_text, "semantic_constraint_text", 1_000
        )
        if not isinstance(self.semantic_evidence, EngineeringEvidenceBundle):
            raise SemanticMvpFixtureError(
                "semantic_evidence must be an EngineeringEvidenceBundle"
            )
        _require_text(self.developer_rationale, "developer_rationale", 2_000)
        if not isinstance(self.demo_priority, bool):
            raise SemanticMvpFixtureError("demo_priority must be a boolean")


@dataclass(frozen=True, slots=True)
class EngineeringSemanticScenario:
    """A coherent frozen authorization scenario for one engineering fixture."""

    fixture: SemanticMvpFixture
    mandate: Mandate
    transaction: Transaction
    catalog_snapshot: CatalogSnapshot
    server_time: datetime
    nonce_state: NonceLedgerState
    committed_hashes: CommittedHashes
    replay_seed: int
    evaluated_at: datetime
    semantic_evidence: object

    def authorization_inputs(self) -> dict[str, object]:
        return {
            "mandate": self.mandate,
            "transaction": self.transaction,
            "catalog_snapshot": self.catalog_snapshot,
            "server_time": self.server_time,
            "nonce_state": self.nonce_state,
            "committed_hashes": self.committed_hashes,
            "replay_seed": self.replay_seed,
            "evaluated_at": self.evaluated_at,
            "semantic_evidence": self.semantic_evidence,
        }


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


def _require_exact_fields(
    value: object, expected: frozenset[str], location: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SemanticMvpFixtureError(f"{location} must be a JSON object")
    actual = frozenset(value)
    if actual != expected:
        missing = ",".join(sorted(expected - actual)) or "none"
        unknown = ",".join(sorted(actual - expected)) or "none"
        raise SemanticMvpFixtureError(
            f"{location} has invalid fields "
            f"(missing={missing}; unknown={unknown})"
        )
    return value


def _find_benchmark_fields(value: object) -> frozenset[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in BENCHMARK_ONLY_FIELDS:
                found.add(key)
            found.update(_find_benchmark_fields(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_find_benchmark_fields(item))
    return frozenset(found)


def parse_fixture_line(raw: str, location: str = "fixture") -> SemanticMvpFixture:
    """Parse one strict JSON fixture without accepting benchmark fields."""

    try:
        decoded = json.loads(
            raw,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_float=_reject_float,
            parse_constant=_reject_non_json_number,
        )
    except (TypeError, ValueError) as error:
        raise SemanticMvpFixtureError(f"{location} is not strict JSON") from error
    benchmark_fields = _find_benchmark_fields(decoded)
    if benchmark_fields:
        raise SemanticMvpFixtureError(
            f"{location} contains forbidden benchmark fields: "
            + ",".join(sorted(benchmark_fields))
        )
    record = _require_exact_fields(decoded, FIXTURE_FIELDS, location)
    evidence = _require_exact_fields(
        record["semantic_evidence"], EVIDENCE_FIELDS, f"{location}.semantic_evidence"
    )
    raw_entries = evidence["entries"]
    if not isinstance(raw_entries, list) or not raw_entries:
        raise SemanticMvpFixtureError(
            f"{location}.semantic_evidence.entries must be a non-empty array"
        )
    entries: list[EngineeringEvidenceEntry] = []
    for index, raw_entry in enumerate(raw_entries):
        entry = _require_exact_fields(
            raw_entry,
            EVIDENCE_ENTRY_FIELDS,
            f"{location}.semantic_evidence.entries[{index}]",
        )
        entries.append(
            EngineeringEvidenceEntry(
                evidence_id=entry["evidence_id"],
                merchant_id=entry["merchant_id"],
                sku=entry["sku"],
                source_kind=entry["source_kind"],
                text=entry["text"],
            )
        )
    try:
        return SemanticMvpFixture(
            fixture_id=record["fixture_id"],
            fixture_schema_version=record["fixture_schema_version"],
            family=SemanticFamily(record["family"]),
            difficulty=FixtureDifficulty(record["difficulty"]),
            engineering_expectation=EngineeringExpectation(
                record["engineering_expectation"]
            ),
            semantic_constraint_text=record["semantic_constraint_text"],
            semantic_evidence=EngineeringEvidenceBundle(
                merchant_id=evidence["merchant_id"], entries=tuple(entries)
            ),
            developer_rationale=record["developer_rationale"],
            demo_priority=record["demo_priority"],
        )
    except (TypeError, ValueError) as error:
        if isinstance(error, SemanticMvpFixtureError):
            raise
        raise SemanticMvpFixtureError(
            f"{location} contains an invalid typed value"
        ) from error


def load_fixture_corpus(path: Path) -> tuple[SemanticMvpFixture, ...]:
    """Load and validate the complete UTF-8 engineering JSONL corpus."""

    if not isinstance(path, Path):
        raise TypeError("path must be pathlib.Path")
    try:
        with path.open("r", encoding="utf-8", newline="") as stream:
            lines = stream.read().splitlines()
    except (OSError, UnicodeError) as error:
        raise SemanticMvpFixtureError(f"cannot read fixture corpus {path}") from error
    if not lines or any(not line.strip() for line in lines):
        raise SemanticMvpFixtureError(
            "fixture corpus must contain non-blank JSONL records only"
        )
    fixtures = tuple(
        parse_fixture_line(line, f"{path}:{number}")
        for number, line in enumerate(lines, start=1)
    )
    return validate_fixture_corpus(fixtures)


def fixture_record(fixture: SemanticMvpFixture) -> dict[str, object]:
    """Return the exact engineering fixture wire record."""

    return {
        "fixture_id": fixture.fixture_id,
        "fixture_schema_version": fixture.fixture_schema_version,
        "family": fixture.family.value,
        "difficulty": fixture.difficulty.value,
        "engineering_expectation": fixture.engineering_expectation.value,
        "semantic_constraint_text": fixture.semantic_constraint_text,
        "semantic_evidence": {
            "merchant_id": fixture.semantic_evidence.merchant_id,
            "entries": [
                {
                    "evidence_id": entry.evidence_id,
                    "merchant_id": entry.merchant_id,
                    "sku": entry.sku,
                    "source_kind": entry.source_kind,
                    "text": entry.text,
                }
                for entry in fixture.semantic_evidence.entries
            ],
        },
        "developer_rationale": fixture.developer_rationale,
        "demo_priority": fixture.demo_priority,
    }


def fixture_record_line(fixture: SemanticMvpFixture) -> str:
    return canonical_json_text(fixture_record(fixture))


def validate_fixture_corpus(
    fixtures: Iterable[SemanticMvpFixture],
) -> tuple[SemanticMvpFixture, ...]:
    """Enforce the complete 72-case engineering allocation."""

    values = tuple(fixtures)
    if not all(isinstance(item, SemanticMvpFixture) for item in values):
        raise SemanticMvpFixtureError(
            "fixture corpus must contain SemanticMvpFixture values"
        )
    if len(values) != FIXTURE_COUNT:
        raise SemanticMvpFixtureError(
            f"fixture corpus must contain exactly {FIXTURE_COUNT} records"
        )
    fixture_ids = [item.fixture_id for item in values]
    if len(fixture_ids) != len(set(fixture_ids)):
        raise SemanticMvpFixtureError("fixture IDs must be unique")

    family_counts = Counter(item.family for item in values)
    for family in SemanticFamily:
        if family_counts[family] != FAMILY_COUNT:
            raise SemanticMvpFixtureError(
                f"{family.value} must contain exactly {FAMILY_COUNT} fixtures"
            )
    strata = Counter(
        (item.family, item.engineering_expectation) for item in values
    )
    for family in SemanticFamily:
        for expectation in EngineeringExpectation:
            if strata[(family, expectation)] != EXPECTATION_PER_FAMILY:
                raise SemanticMvpFixtureError(
                    f"{family.value}/{expectation.value} must contain exactly "
                    f"{EXPECTATION_PER_FAMILY} fixtures"
                )

    demos = tuple(item for item in values if item.demo_priority)
    if len(demos) != DEMO_COUNT:
        raise SemanticMvpFixtureError(
            f"exactly {DEMO_COUNT} fixtures must have demo_priority=true"
        )
    demo_families = Counter(item.family for item in demos)
    demo_strata = Counter(
        (item.family, item.engineering_expectation) for item in demos
    )
    for family in SemanticFamily:
        if demo_families[family] != DEMO_PER_FAMILY:
            raise SemanticMvpFixtureError(
                f"{family.value} must contain exactly {DEMO_PER_FAMILY} demos"
            )
        for expectation in EngineeringExpectation:
            if demo_strata[(family, expectation)] != 1:
                raise SemanticMvpFixtureError(
                    f"{family.value} demos must contain one "
                    f"{expectation.value} fixture"
                )
    return values


def select_fixtures(
    fixtures: Iterable[SemanticMvpFixture],
    *,
    case_id: str | None = None,
    demo_only: bool = False,
    limit: int | None = None,
) -> tuple[SemanticMvpFixture, ...]:
    """Apply deterministic CLI selection after full-corpus validation."""

    values = validate_fixture_corpus(fixtures)
    if case_id is not None:
        selected = tuple(item for item in values if item.fixture_id == case_id)
        if not selected:
            raise SemanticMvpFixtureError(f"unknown fixture_id {case_id!r}")
    else:
        selected = values
    if demo_only:
        selected = tuple(item for item in selected if item.demo_priority)
    if limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise SemanticMvpFixtureError("limit must be a positive integer")
        selected = selected[:limit]
    return selected


def build_semantic_scenario(
    fixture: SemanticMvpFixture,
) -> EngineeringSemanticScenario:
    """Wrap one fixture in a clean deterministic frozen authorization envelope."""

    if not isinstance(fixture, SemanticMvpFixture):
        raise TypeError("fixture must be SemanticMvpFixture")

    # This local import keeps plain corpus loading and validation free of the
    # semantic execution package.  The types themselves are the frozen D5
    # evidence models; no parallel evidence representation reaches the verifier.
    from mandateguard.semantic.evidence import (
        SemanticEvidence,
        SemanticEvidenceBundle,
        SemanticEvidenceEntry,
        semantic_evidence_sha256,
    )

    family_kind = {
        SemanticFamily.RECURRENCE: "obligation",
        SemanticFamily.EXCLUSION: "exclusion",
        SemanticFamily.PURPOSE: "purpose",
    }
    line = TransactionLine(
        sku=_ENGINEERING_SKU,
        effective_unit_price_minor=_UNIT_PRICE_MINOR,
        quantity=_QUANTITY,
        line_total_minor=_UNIT_PRICE_MINOR * _QUANTITY,
        recurring=False,
    )
    transaction_payload = TransactionPayload(
        transaction_id=f"engineering-{fixture.fixture_id.lower()}",
        merchant_id=_ENGINEERING_MERCHANT,
        cart_currency="USD",
        order_currency="USD",
        declared_order_total_minor=line.line_total_minor,
        declared_aggregate_quantity=line.quantity,
        cart_recurring=False,
        order_recurring=False,
        lines=(line,),
    )
    transaction = Transaction(
        payload=transaction_payload,
        declared_transaction_hash=transaction_body_sha256(transaction_payload),
    )
    catalog = CatalogSnapshot(
        snapshot_id="semantic-mvp-clean-catalog-v1",
        merchant_id=_ENGINEERING_MERCHANT,
        currency="USD",
        items=(
            CatalogItem(
                sku=_ENGINEERING_SKU,
                merchant_id=_ENGINEERING_MERCHANT,
                effective_unit_price_minor=_UNIT_PRICE_MINOR,
                recurring=False,
            ),
        ),
    )
    mandate = Mandate(
        payload=MandatePayload(
            mandate_id=str(uuid5(_ENVELOPE_NAMESPACE, fixture.fixture_id)),
            nonce=f"semantic-mvp-{fixture.fixture_id.lower()}",
            issued_at=_ISSUED_AT,
            expires_at=_EXPIRES_AT,
            subject_ref="semantic-mvp-engineering-subject",
            currency="USD",
            constraints=MandateConstraints(
                hard=HardConstraints(
                    max_total_minor=100_000,
                    max_quantity=10,
                    recurring_allowed=False,
                    merchant_allowlist=(_ENGINEERING_MERCHANT,),
                    sku_allowlist=(_ENGINEERING_SKU,),
                ),
                semantic=(
                    SemanticConstraint(
                        constraint_id=f"engineering-{fixture.fixture_id.lower()}",
                        kind=family_kind[fixture.family],
                        text=fixture.semantic_constraint_text,
                    ),
                ),
            ),
        ),
        issuer_attestation=IssuerAttestation(
            assurance="DECLARED_ONLY",
            issuer_id="semantic-mvp-engineering-issuer",
        ),
        metadata={"engineering_fixture_id": fixture.fixture_id},
    )
    bundle = SemanticEvidenceBundle(
        merchant_id=fixture.semantic_evidence.merchant_id,
        entries=tuple(
            SemanticEvidenceEntry(
                evidence_id=entry.evidence_id,
                merchant_id=entry.merchant_id,
                sku=entry.sku,
                source_kind=entry.source_kind,
                text=entry.text,
            )
            for entry in fixture.semantic_evidence.entries
        ),
    )
    semantic_evidence = SemanticEvidence(
        bundle=bundle,
        semantic_evidence_sha256=semantic_evidence_sha256(bundle),
    )
    return EngineeringSemanticScenario(
        fixture=fixture,
        mandate=mandate,
        transaction=transaction,
        catalog_snapshot=catalog,
        server_time=_EVALUATED_AT,
        nonce_state=NonceLedgerState(),
        committed_hashes=CommittedHashes(
            transaction_sha256=transaction_body_sha256(transaction),
            catalog_snapshot_sha256=catalog_snapshot_sha256(catalog),
        ),
        replay_seed=7_200,
        evaluated_at=_EVALUATED_AT,
        semantic_evidence=semantic_evidence,
    )


def validate_clean_deterministic_envelope(
    scenario: EngineeringSemanticScenario,
) -> None:
    """Prove frozen Tier A/B produce ALLOW before semantic execution."""

    if not isinstance(scenario, EngineeringSemanticScenario):
        raise TypeError("scenario must be EngineeringSemanticScenario")

    from mandateguard.models.finding import TierACheckStatus
    from mandateguard.policy.tier_a import evaluate_tier_a
    from mandateguard.policy.tier_b import evaluate_tier_b

    tier_a = evaluate_tier_a(
        mandate=scenario.mandate,
        transaction=scenario.transaction,
        catalog_snapshot=scenario.catalog_snapshot,
        server_time=scenario.server_time,
        nonce_state=scenario.nonce_state,
        committed_hashes=scenario.committed_hashes,
    )
    if any(result.status is not TierACheckStatus.PASS for result in tier_a):
        raise SemanticMvpFixtureError(
            f"{scenario.fixture.fixture_id} has a non-clean Tier A envelope"
        )
    tier_b = evaluate_tier_b(
        mandate=scenario.mandate,
        transaction=scenario.transaction,
    )
    if tier_b:
        raise SemanticMvpFixtureError(
            f"{scenario.fixture.fixture_id} has a non-clean Tier B envelope"
        )
