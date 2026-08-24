"""Typed D8-A Tier C benchmark case records.

This package builds the infrastructure required to author, adjudicate, hash,
validate, and freeze Tier C semantic benchmark cases. It contains **no Tier C
benchmark case content**, authors none, and executes none.

Frozen authority
----------------

Every constant here is transcribed from a frozen artifact and may not be
reinterpreted:

* family names and definitions - ``TAXONOMY.md`` section 6;
* family/split mapping - ``benchmark/PROTOCOL.md`` section 2.4;
* family and ground-truth allocation - ``benchmark/PROTOCOL.md`` section 2.3;
* provenance allocation - ``benchmark/PROTOCOL.md`` section 3;
* provenance metadata and origin immutability - sections 3.1 and 3.1.1;
* adjudication rules - section 5; and
* the case record schema and enums - ``benchmark/MANIFEST.yaml``.

Detector isolation
------------------

No module in this package imports ``mandateguard.policy``,
``mandateguard.semantic``, ``mandateguard.execution``, or
``mandateguard.replay``. Authoring infrastructure must never be able to consult
a detector, and ``tests/test_benchmark_tier_c_infrastructure.py`` enforces this
both statically and by subprocess import check.

That isolation is why :class:`SemanticEvidenceEntryRecord` and
:class:`SemanticEvidenceBundleRecord` mirror the frozen D5 types
``mandateguard.semantic.evidence.SemanticEvidenceEntry`` and
``SemanticEvidenceBundle`` field-for-field rather than importing them: the
frozen ``mandateguard.semantic`` package ``__init__`` eagerly re-exports
``SemanticVerifier``, ``authorize_transaction``, and the OpenAI adapter, so
importing the evidence model at all would load the whole detector. This follows
the precedent already frozen in ``mandateguard.benchmark.models``, whose
``EvaluationInputs`` deliberately mirrors ``replay.scenario.ReplayScenario``
for the identical reason. The mirror is exact, and is pinned against the frozen
D5 classes by test, so a Tier C case reconstructs into the frozen types without
translation at execution time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import re
from typing import TypeAlias

from mandateguard.core.hashing import CommittedHashes
from mandateguard.core.nonce_ledger import NonceLedgerState
from mandateguard.models.catalog import CatalogSnapshot
from mandateguard.models.mandate import Mandate
from mandateguard.models.transaction import Transaction


CASE_SCHEMA_VERSION = "1.2"
EVIDENCE_TIER = "C"
LABEL_SOURCE = "human_adjudication"

DEV_FAMILIES = ("C-DEV-RECURRENCE", "C-DEV-EXCLUSION", "C-DEV-PURPOSE")
HELD_OUT_FAMILIES = ("C-HOLD-BUNDLE", "C-HOLD-COMPATIBILITY", "C-HOLD-FULFILLMENT")
TIER_C_FAMILIES = DEV_FAMILIES + HELD_OUT_FAMILIES

TIER_C_TOTAL = 440
DEVELOPMENT_TOTAL = 220
HELD_OUT_TOTAL = 220

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CASE_ID_RE = re.compile(r"^(CDEV-(REC|EXC|PUR)|CHOLD-(BUN|CMP|FUL))-[0-9]{3}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_ADJUDICATOR_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_SOURCE_KIND_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class TierCCaseError(ValueError):
    """Raised when a proposed Tier C case violates a frozen rule."""


class Split(str, Enum):
    """The manifest ``split`` enum, reproduced without amendment.

    ``BENIGN_CONTROL`` remains a reserved manifest value. Protocol section 2.4
    forbids it for Tier C in V1, and :func:`structural_issues` rejects it.
    """

    DEV = "dev"
    HELD_OUT = "held_out"
    BENIGN_CONTROL = "benign_control"


class GroundTruth(str, Enum):
    VIOLATION = "violation"
    BENIGN = "benign"


class Provenance(str, Enum):
    DEVELOPER_AUTHORED = "developer_authored"
    EXTERNAL_DEFENSIVE_CORPUS_ADAPTED = "external_defensive_corpus_adapted"
    SEPARATE_MODEL_ADVERSARIAL = "separate_model_adversarial"


class AdjudicationStatus(str, Enum):
    """Lifecycle of one case's human label. ``REVIEW`` is never a label."""

    UNADJUDICATED = "UNADJUDICATED"
    PRIMARY_LABELLED = "PRIMARY_LABELLED"
    DOUBLE_LABELLED = "DOUBLE_LABELLED"
    DISAGREEMENT = "DISAGREEMENT"
    RESOLVED = "RESOLVED"
    EXCLUDED = "EXCLUDED"


#: Protocol section 2.4: ``split`` is a pure function of the family prefix and
#: of nothing else. Ground truth never affects it.
FAMILY_SPLIT: dict[str, Split] = {
    **{family: Split.DEV for family in DEV_FAMILIES},
    **{family: Split.HELD_OUT for family in HELD_OUT_FAMILIES},
}

#: Case-ID family segment. Ground truth is deliberately **not** encoded: it is
#: adjudicated rather than authored, and a disagreement resolution may change
#: it. See ``benchmark/tier_c/README.md``.
FAMILY_CASE_ID_PREFIX: dict[str, str] = {
    "C-DEV-RECURRENCE": "CDEV-REC",
    "C-DEV-EXCLUSION": "CDEV-EXC",
    "C-DEV-PURPOSE": "CDEV-PUR",
    "C-HOLD-BUNDLE": "CHOLD-BUN",
    "C-HOLD-COMPATIBILITY": "CHOLD-CMP",
    "C-HOLD-FULFILLMENT": "CHOLD-FUL",
}

#: Which frozen ``SemanticConstraint.kind`` values (``models.mandate``) may
#: carry a family's defining constraint. Derived from the TAXONOMY section 6
#: family definitions and the frozen kind enum; ``other`` is always allowed as
#: the schema's own escape hatch.
#:
#: This is a structural authoring aid over the constraint's *kind tag* only. It
#: never inspects constraint text and never decides whether a case is violating
#: or benign: that remains human adjudication.
FAMILY_SEMANTIC_KINDS: dict[str, frozenset[str]] = {
    "C-DEV-RECURRENCE": frozenset({"obligation", "other"}),
    "C-DEV-EXCLUSION": frozenset({"exclusion", "other"}),
    "C-DEV-PURPOSE": frozenset({"purpose", "category_intent", "other"}),
    "C-HOLD-BUNDLE": frozenset({"exclusion", "category_intent", "other"}),
    "C-HOLD-COMPATIBILITY": frozenset({"compatibility", "other"}),
    "C-HOLD-FULFILLMENT": frozenset({"fulfillment", "other"}),
}

StratumKey: TypeAlias = tuple[str, "GroundTruth", "Provenance"]

#: Protocol sections 2.3 and 3: the complete registered allocation, keyed by
#: ``(family_id, ground_truth, provenance)``. These are validation constants.
#: D8-A generates no case to fill them.
TIER_C_ALLOCATION: dict[StratumKey, int] = {}


def _register_allocation(
    family: str, ground_truth: GroundTruth, developer: int, external: int, model: int
) -> None:
    TIER_C_ALLOCATION[(family, ground_truth, Provenance.DEVELOPER_AUTHORED)] = developer
    TIER_C_ALLOCATION[
        (family, ground_truth, Provenance.EXTERNAL_DEFENSIVE_CORPUS_ADAPTED)
    ] = external
    TIER_C_ALLOCATION[
        (family, ground_truth, Provenance.SEPARATE_MODEL_ADVERSARIAL)
    ] = model


for _family in TIER_C_FAMILIES:
    _register_allocation(_family, GroundTruth.VIOLATION, 16, 12, 12)
for _family, _developer_benign in (
    ("C-DEV-RECURRENCE", 14),
    ("C-DEV-EXCLUSION", 13),
    ("C-DEV-PURPOSE", 13),
    ("C-HOLD-BUNDLE", 14),
    ("C-HOLD-COMPATIBILITY", 13),
    ("C-HOLD-FULFILLMENT", 13),
):
    _register_allocation(_family, GroundTruth.BENIGN, _developer_benign, 10, 10)
del _family, _developer_benign


def allocation_for_split(split: Split) -> dict[StratumKey, int]:
    """The registered allocation restricted to one split."""

    return {
        key: count
        for key, count in TIER_C_ALLOCATION.items()
        if FAMILY_SPLIT[key[0]] is split
    }


def _require_aware_utc(value: object, name: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise TierCCaseError(f"{name} must be a timezone-aware datetime")


def _require_text(value: object, name: str, maximum: int) -> None:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise TierCCaseError(
            f"{name} must be a non-empty string of at most {maximum} characters"
        )


def _require_digest(value: object, name: str) -> None:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise TierCCaseError(f"{name} must be a lowercase SHA-256 hex digest")


# ---------------------------------------------------------------------------
# Semantic evidence: exact mirror of the frozen D5 structure
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SemanticEvidenceEntryRecord:
    """Mirror of the frozen ``semantic.evidence.SemanticEvidenceEntry``.

    Field names, order, and validation match the frozen class exactly so this
    record reconstructs into it without translation. See the module docstring
    for why it is mirrored rather than imported.
    """

    evidence_id: str
    merchant_id: str
    sku: str | None
    source_kind: str
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_id, str) or not _IDENTIFIER_RE.fullmatch(
            self.evidence_id
        ):
            raise TierCCaseError("evidence_id must be a bounded identifier")
        _require_text(self.merchant_id, "merchant_id", 128)
        if self.sku is not None:
            _require_text(self.sku, "sku", 128)
        if not isinstance(self.source_kind, str) or not _SOURCE_KIND_RE.fullmatch(
            self.source_kind
        ):
            raise TierCCaseError("source_kind must be a generic lowercase identifier")
        _require_text(self.text, "text", 20_000)


def _entry_sort_key(entry: SemanticEvidenceEntryRecord) -> tuple[bool, str, str, str]:
    return (
        entry.sku is not None,
        entry.sku or "",
        entry.source_kind,
        entry.evidence_id,
    )


@dataclass(frozen=True, slots=True)
class SemanticEvidenceBundleRecord:
    """Mirror of the frozen ``semantic.evidence.SemanticEvidenceBundle``.

    Entries are sorted with the frozen sort key so the canonical bytes of a
    Tier C case match the bytes the frozen bundle would produce.
    """

    merchant_id: str
    entries: tuple[SemanticEvidenceEntryRecord, ...]

    def __post_init__(self) -> None:
        _require_text(self.merchant_id, "merchant_id", 128)
        if not isinstance(self.entries, tuple) or not self.entries:
            raise TierCCaseError("semantic evidence entries must be a non-empty tuple")
        if not all(
            isinstance(entry, SemanticEvidenceEntryRecord) for entry in self.entries
        ):
            raise TierCCaseError("entries contains an invalid semantic evidence entry")
        if any(entry.merchant_id != self.merchant_id for entry in self.entries):
            raise TierCCaseError("every evidence entry must belong to the bundle merchant")
        evidence_ids = [entry.evidence_id for entry in self.entries]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise TierCCaseError("evidence IDs must be unique within a bundle")
        content_keys = [
            (entry.sku, entry.source_kind, entry.text) for entry in self.entries
        ]
        if len(content_keys) != len(set(content_keys)):
            raise TierCCaseError("duplicate ambiguous semantic evidence entry")
        object.__setattr__(self, "entries", tuple(sorted(self.entries, key=_entry_sort_key)))


# ---------------------------------------------------------------------------
# Evaluation inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TierCEvaluationInputs:
    """Complete authorization inputs for one Tier C case.

    The deterministic field set mirrors ``replay.scenario.ReplayScenario``
    exactly as ``benchmark.models.EvaluationInputs`` does, plus the trusted
    semantic evidence the frozen D5 verifier requires. Unlike the Tier A/B
    variant, a Tier C mandate **must** carry at least one semantic constraint.
    """

    mandate: Mandate
    transaction: Transaction
    catalog_snapshot: CatalogSnapshot | None
    server_time: datetime | None
    nonce_state: NonceLedgerState | None
    psp_committed_hashes: CommittedHashes | None
    replay_seed: int
    evaluated_at: datetime
    semantic_evidence: SemanticEvidenceBundleRecord

    def __post_init__(self) -> None:
        if not isinstance(self.mandate, Mandate):
            raise TierCCaseError("mandate must be Mandate")
        if not isinstance(self.transaction, Transaction):
            raise TierCCaseError("transaction must be Transaction")
        if self.catalog_snapshot is not None and not isinstance(
            self.catalog_snapshot, CatalogSnapshot
        ):
            raise TierCCaseError("catalog_snapshot must be CatalogSnapshot or None")
        if self.server_time is not None:
            _require_aware_utc(self.server_time, "server_time")
        _require_aware_utc(self.evaluated_at, "evaluated_at")
        if self.server_time is not None and self.server_time != self.evaluated_at:
            raise TierCCaseError(
                "server_time must equal evaluated_at when server_time is present"
            )
        if self.nonce_state is not None and not isinstance(
            self.nonce_state, NonceLedgerState
        ):
            raise TierCCaseError("nonce_state must be NonceLedgerState or None")
        if self.psp_committed_hashes is not None and not isinstance(
            self.psp_committed_hashes, CommittedHashes
        ):
            raise TierCCaseError("psp_committed_hashes must be CommittedHashes or None")
        if isinstance(self.replay_seed, bool) or not isinstance(self.replay_seed, int):
            raise TierCCaseError("replay_seed must be an integer")
        if not isinstance(self.semantic_evidence, SemanticEvidenceBundleRecord):
            raise TierCCaseError("semantic_evidence must be SemanticEvidenceBundleRecord")
        if not self.mandate.payload.constraints.semantic:
            raise TierCCaseError(
                "a Tier C case must carry at least one semantic constraint"
            )
        if self.semantic_evidence.merchant_id != self.transaction.payload.merchant_id:
            raise TierCCaseError(
                "semantic evidence merchant must match the transaction merchant"
            )


# ---------------------------------------------------------------------------
# Provenance origin (protocol 3.1, 3.1.1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DeveloperAuthoredOrigin:
    """Origin metadata for ``developer_authored``."""

    authored_at: datetime

    def __post_init__(self) -> None:
        _require_aware_utc(self.authored_at, "authored_at")


@dataclass(frozen=True, slots=True)
class ExternalCorpusOrigin:
    """Origin metadata for ``external_defensive_corpus_adapted``.

    ``source_selected_at`` records when the specific source passage was first
    selected or inspected. Protocol section 3.1 forbids specific-item contact
    with held-out source material before detector freeze, so this timestamp is
    what the held-out isolation audit checks.
    """

    authored_at: datetime
    source_selected_at: datetime
    source_name: str
    source_reference: str
    source_version: str | None
    adaptation_description: str

    def __post_init__(self) -> None:
        _require_aware_utc(self.authored_at, "authored_at")
        _require_aware_utc(self.source_selected_at, "source_selected_at")
        _require_text(self.source_name, "source_name", 256)
        _require_text(self.source_reference, "source_reference", 2048)
        if self.source_version is not None:
            _require_text(self.source_version, "source_version", 128)
        _require_text(self.adaptation_description, "adaptation_description", 4096)
        if self.source_selected_at > self.authored_at:
            raise TierCCaseError(
                "source_selected_at must not be later than authored_at"
            )


@dataclass(frozen=True, slots=True)
class SeparateModelOrigin:
    """Origin metadata for ``separate_model_adversarial``.

    Only the authoring model identifier and the SHA-256 of the authoring prompt
    are retained, exactly as protocol section 3.1 requires. There is
    deliberately no field for the raw prompt, for provider credentials, or for
    model chain-of-thought, so none can be stored here.
    """

    authored_at: datetime
    authoring_model_id: str
    authoring_prompt_sha256: str

    def __post_init__(self) -> None:
        _require_aware_utc(self.authored_at, "authored_at")
        _require_text(self.authoring_model_id, "authoring_model_id", 256)
        _require_digest(self.authoring_prompt_sha256, "authoring_prompt_sha256")


ProvenanceOrigin: TypeAlias = (
    DeveloperAuthoredOrigin | ExternalCorpusOrigin | SeparateModelOrigin
)

#: Protocol 3.1.1: provenance records where a case came from. The origin type
#: is bound to the provenance value, so a case cannot be relabelled
#: ``developer_authored`` because a developer later edited it.
PROVENANCE_ORIGIN_TYPES: dict[Provenance, type] = {
    Provenance.DEVELOPER_AUTHORED: DeveloperAuthoredOrigin,
    Provenance.EXTERNAL_DEFENSIVE_CORPUS_ADAPTED: ExternalCorpusOrigin,
    Provenance.SEPARATE_MODEL_ADVERSARIAL: SeparateModelOrigin,
}


# ---------------------------------------------------------------------------
# Human adjudication (protocol 5, 5.2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AdjudicationRecord:
    """One independent human label, recorded without detector output.

    There is deliberately no field for a detector action, a semantic result, a
    model response, or a score: an adjudication record cannot carry one.
    """

    adjudicator_id: str
    label: GroundTruth
    ambiguous: bool
    adjudicated_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.adjudicator_id, str) or not _ADJUDICATOR_ID_RE.fullmatch(
            self.adjudicator_id
        ):
            raise TierCCaseError("adjudicator_id must be a bounded identifier")
        if not isinstance(self.label, GroundTruth):
            raise TierCCaseError("label must be violation or benign")
        if not isinstance(self.ambiguous, bool):
            raise TierCCaseError("ambiguous must be a boolean")
        _require_aware_utc(self.adjudicated_at, "adjudicated_at")


@dataclass(frozen=True, slots=True)
class ResolutionRecord:
    """A disagreement resolved from case content alone (protocol 5.2)."""

    label: GroundTruth
    resolved_at: datetime
    rationale: str
    adjudicator_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.label, GroundTruth):
            raise TierCCaseError("resolution label must be violation or benign")
        _require_aware_utc(self.resolved_at, "resolved_at")
        _require_text(self.rationale, "rationale", 4096)
        if not isinstance(self.adjudicator_ids, tuple) or not self.adjudicator_ids:
            raise TierCCaseError("adjudicator_ids must be a non-empty tuple")
        for adjudicator_id in self.adjudicator_ids:
            if not isinstance(adjudicator_id, str) or not _ADJUDICATOR_ID_RE.fullmatch(
                adjudicator_id
            ):
                raise TierCCaseError("adjudicator_ids must be bounded identifiers")


@dataclass(frozen=True, slots=True)
class ExclusionRecord:
    """A permanently retired case (protocol 5.2).

    An excluded case is never executed and never enters a benchmark metric, but
    it stays in audit history and its ``case_id`` is never reused.
    """

    reason: str
    excluded_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.reason, "reason", 2048)
        _require_aware_utc(self.excluded_at, "excluded_at")


@dataclass(frozen=True, slots=True)
class TierCAdjudication:
    """The complete human-label state of one case.

    ``ground_truth`` is **derived** from these records rather than stored
    alongside them, so a Tier C case can never carry a label that no human
    assigned. That is what makes ``label_source=human_adjudication`` structural
    rather than declarative.
    """

    primary: AdjudicationRecord | None = None
    second: AdjudicationRecord | None = None
    resolution: ResolutionRecord | None = None

    def __post_init__(self) -> None:
        for record, name in ((self.primary, "primary"), (self.second, "second")):
            if record is not None and not isinstance(record, AdjudicationRecord):
                raise TierCCaseError(f"{name} must be AdjudicationRecord or None")
        if self.resolution is not None and not isinstance(
            self.resolution, ResolutionRecord
        ):
            raise TierCCaseError("resolution must be ResolutionRecord or None")
        if self.second is not None and self.primary is None:
            raise TierCCaseError("a second review requires a primary adjudication")
        if (
            self.primary is not None
            and self.second is not None
            and self.primary.adjudicator_id == self.second.adjudicator_id
        ):
            raise TierCCaseError("the second review must be independent of the primary")
        if self.resolution is not None:
            if self.primary is None or self.second is None:
                raise TierCCaseError("resolution requires two independent labels")
            if self.primary.label is self.second.label:
                raise TierCCaseError("resolution requires an actual disagreement")

    @property
    def is_disagreement(self) -> bool:
        return (
            self.primary is not None
            and self.second is not None
            and self.primary.label is not self.second.label
        )

    @property
    def marked_ambiguous(self) -> bool:
        """Whether the primary adjudicator marked the case ambiguous."""

        return self.primary is not None and self.primary.ambiguous

    @property
    def final_label(self) -> GroundTruth | None:
        """The adjudicated ground truth, or ``None`` while it does not exist."""

        if self.resolution is not None:
            return self.resolution.label
        if self.primary is None:
            return None
        if self.is_disagreement:
            return None
        return self.primary.label

    @property
    def label_recorded_at(self) -> datetime | None:
        """When the final label was recorded (protocol 5)."""

        if self.resolution is not None:
            return self.resolution.resolved_at
        if self.primary is None or self.is_disagreement:
            return None
        return self.primary.adjudicated_at

    @property
    def status(self) -> AdjudicationStatus:
        if self.primary is None:
            return AdjudicationStatus.UNADJUDICATED
        if self.second is None:
            return AdjudicationStatus.PRIMARY_LABELLED
        if not self.is_disagreement:
            return AdjudicationStatus.DOUBLE_LABELLED
        if self.resolution is None:
            return AdjudicationStatus.DISAGREEMENT
        return AdjudicationStatus.RESOLVED


# ---------------------------------------------------------------------------
# The case record
# ---------------------------------------------------------------------------

#: Hashed content may never change once a case has been executed (protocol 6,
#: 7.1 batch closure, 17).
IMMUTABLE_AFTER_FIRST_RUN = (
    "ground_truth",
    "family_id",
    "split",
    "provenance",
    "evaluation_inputs",
    "semantic_evidence",
    "case_content_sha256",
)


@dataclass(frozen=True, slots=True)
class TierCCase:
    """One Tier C benchmark case definition.

    Deliberately absent: ``expected_action`` (the manifest fixes it as omitted
    for Tier C), and every detector output - ``actual_action``,
    ``semantic_result``, ``model_response``, ``detector_score``. Those belong to
    later evaluation-result artifacts, never to a case definition.

    ``ground_truth`` and ``label_recorded_at`` are read-only properties derived
    from :attr:`adjudication`.
    """

    case_id: str
    case_schema_version: str
    evidence_tier: str
    family_id: str
    provenance: Provenance
    provenance_origin: ProvenanceOrigin
    split: Split
    label_source: str
    evaluation_inputs: TierCEvaluationInputs
    adjudication: TierCAdjudication
    exclusion: ExclusionRecord | None = None
    first_run_at: datetime | None = None

    def __post_init__(self) -> None:
        issues = structural_issues(self)
        if issues:
            raise TierCCaseError(issues[0].message)

    @property
    def ground_truth(self) -> GroundTruth | None:
        return self.adjudication.final_label

    @property
    def label_recorded_at(self) -> datetime | None:
        return self.adjudication.label_recorded_at

    @property
    def status(self) -> AdjudicationStatus:
        if self.exclusion is not None:
            return AdjudicationStatus.EXCLUDED
        return self.adjudication.status

    @property
    def stratum(self) -> StratumKey | None:
        """``(family_id, ground_truth, provenance)``, once a label exists."""

        ground_truth = self.ground_truth
        if ground_truth is None:
            return None
        return (self.family_id, ground_truth, self.provenance)

    @property
    def authored_at(self) -> datetime:
        return self.provenance_origin.authored_at


@dataclass(frozen=True, slots=True)
class StructuralIssue:
    """One structural defect found in a proposed case."""

    code: str
    message: str


def structural_issues(case: TierCCase) -> list[StructuralIssue]:
    """Every frozen structural rule one case must satisfy.

    ``TierCCase.__post_init__`` raises on the first issue, so a constructed case
    is always structurally valid; the corpus validator calls this directly to
    report all issues at once, and to re-check cases rebuilt from JSON.
    """

    issues: list[StructuralIssue] = []

    def add(code: str, message: str) -> None:
        issues.append(StructuralIssue(code=code, message=message))

    if not isinstance(case.case_id, str) or not _CASE_ID_RE.fullmatch(case.case_id):
        add("INVALID_CASE_ID", f"case_id {case.case_id!r} is not a Tier C case ID")
    if case.case_schema_version != CASE_SCHEMA_VERSION:
        add(
            "INVALID_SCHEMA_VERSION",
            f"case_schema_version must be {CASE_SCHEMA_VERSION}",
        )
    if case.evidence_tier != EVIDENCE_TIER:
        add("INVALID_EVIDENCE_TIER", "Tier C evidence_tier must be C")
    if case.family_id not in TIER_C_FAMILIES:
        add("INVALID_FAMILY", f"family_id {case.family_id!r} is not a Tier C family")
    else:
        expected_prefix = FAMILY_CASE_ID_PREFIX[case.family_id]
        if isinstance(case.case_id, str) and not case.case_id.startswith(
            f"{expected_prefix}-"
        ):
            add(
                "CASE_ID_FAMILY_MISMATCH",
                f"case_id {case.case_id!r} does not use the {case.family_id} "
                f"prefix {expected_prefix}",
            )
        if case.split is Split.BENIGN_CONTROL:
            add(
                "INVALID_SPLIT",
                "benign_control is not used for Tier C in V1 (protocol 2.4)",
            )
        elif case.split is not FAMILY_SPLIT[case.family_id]:
            add(
                "INVALID_SPLIT",
                f"{case.family_id} requires split="
                f"{FAMILY_SPLIT[case.family_id].value}",
            )
    if not isinstance(case.provenance, Provenance):
        add("INVALID_PROVENANCE", "provenance must be a registered Tier C provenance")
    else:
        expected_origin = PROVENANCE_ORIGIN_TYPES[case.provenance]
        if not isinstance(case.provenance_origin, expected_origin):
            add(
                "INVALID_PROVENANCE_METADATA",
                f"provenance {case.provenance.value} requires "
                f"{expected_origin.__name__} metadata",
            )
    if case.label_source != LABEL_SOURCE:
        add("INVALID_LABEL_SOURCE", "Tier C label_source must be human_adjudication")
    if not isinstance(case.evaluation_inputs, TierCEvaluationInputs):
        add(
            "INCOMPLETE_EVALUATION_INPUTS",
            "evaluation_inputs must be TierCEvaluationInputs",
        )
    elif case.family_id in FAMILY_SEMANTIC_KINDS:
        allowed = FAMILY_SEMANTIC_KINDS[case.family_id]
        kinds = {
            constraint.kind
            for constraint in case.evaluation_inputs.mandate.payload.constraints.semantic
        }
        if not kinds:
            add(
                "MISSING_SEMANTIC_CONSTRAINT",
                "a Tier C case must carry at least one semantic constraint",
            )
        elif not (kinds & allowed):
            add(
                "FAMILY_CONSTRAINT_KIND_MISMATCH",
                f"{case.family_id} needs a constraint of kind "
                f"{sorted(allowed)}, found {sorted(kinds)}",
            )
    if not isinstance(case.adjudication, TierCAdjudication):
        add("INVALID_ADJUDICATION", "adjudication must be TierCAdjudication")
    if case.exclusion is not None and not isinstance(case.exclusion, ExclusionRecord):
        add("INVALID_EXCLUSION", "exclusion must be ExclusionRecord or None")
    if case.first_run_at is not None:
        if (
            not isinstance(case.first_run_at, datetime)
            or case.first_run_at.tzinfo is None
            or case.first_run_at.utcoffset() is None
        ):
            add("INVALID_FIRST_RUN", "first_run_at must be a timezone-aware datetime")
        elif case.exclusion is not None:
            add(
                "EXCLUDED_CASE_EXECUTED",
                "an excluded case may never be executed (protocol 5.2)",
            )
        elif case.ground_truth is None:
            add(
                "EXECUTED_WITHOUT_LABEL",
                "first_run_at requires a recorded human label (protocol 5)",
            )
    return issues
