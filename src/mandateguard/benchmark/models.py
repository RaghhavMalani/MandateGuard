"""Typed D7 benchmark case records for the deterministic Tier A/B corpus.

This module deliberately imports no policy, semantic, execution, or replay
module. D7 generates and labels the registered corpus; it never executes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Mapping, TypeAlias

from mandateguard.core.hashing import CommittedHashes
from mandateguard.core.nonce_ledger import NonceLedgerState
from mandateguard.models.catalog import CatalogSnapshot
from mandateguard.models.mandate import Mandate
from mandateguard.models.transaction import Transaction


GENERATOR_VERSION = "d7-tier-ab/1.0.0"
CASE_SCHEMA_VERSION = "1.2"

EVIDENCE_TIERS = frozenset({"A", "B"})
TIER_A_FAMILIES = ("A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8")
TIER_B_FAMILIES = ("B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B9", "B10")
BENCHMARK_FAMILIES = TIER_A_FAMILIES + TIER_B_FAMILIES

PROVENANCE_VALUES = frozenset({"developer_authored"})
SPLIT_VALUES = frozenset({"dev"})
GROUND_TRUTH_VALUES = frozenset({"violation", "benign"})
LABEL_SOURCE_VALUES = frozenset({"deterministic_invariant"})
EXPECTED_ACTIONS = frozenset({"ALLOW", "REVIEW", "BLOCK"})
TIER_A_STATUSES = frozenset({"PASS", "FAIL", "NOT_EVALUABLE"})
TIER_B_STATUSES = frozenset({"PASS", "FAIL"})

CASE_CLASSES = ("V", "P", "NE")

RecipeParameterValue: TypeAlias = str | int | bool | None


def _require_aware_utc(value: object, name: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{name} must be a timezone-aware datetime")


@dataclass(frozen=True, slots=True)
class EvaluationInputs:
    """Complete explicit deterministic authorization inputs for one case.

    The field set mirrors ``mandateguard.replay.scenario.ReplayScenario`` so a
    generated case can later be executed without translation, but this module
    does not import the replay package: importing it would load the Tier A/B
    policy modules that D7 must not touch.
    """

    mandate: Mandate
    transaction: Transaction
    catalog_snapshot: CatalogSnapshot | None
    server_time: datetime | None
    nonce_state: NonceLedgerState | None
    psp_committed_hashes: CommittedHashes | None
    replay_seed: int
    evaluated_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.mandate, Mandate):
            raise TypeError("mandate must be Mandate")
        if not isinstance(self.transaction, Transaction):
            raise TypeError("transaction must be Transaction")
        if self.catalog_snapshot is not None and not isinstance(
            self.catalog_snapshot, CatalogSnapshot
        ):
            raise TypeError("catalog_snapshot must be CatalogSnapshot or None")
        if self.server_time is not None:
            _require_aware_utc(self.server_time, "server_time")
        _require_aware_utc(self.evaluated_at, "evaluated_at")
        if self.server_time is not None and self.server_time != self.evaluated_at:
            raise ValueError(
                "server_time must equal evaluated_at when server_time is present"
            )
        if self.nonce_state is not None and not isinstance(
            self.nonce_state, NonceLedgerState
        ):
            raise TypeError("nonce_state must be NonceLedgerState or None")
        if self.psp_committed_hashes is not None and not isinstance(
            self.psp_committed_hashes, CommittedHashes
        ):
            raise TypeError("psp_committed_hashes must be CommittedHashes or None")
        if isinstance(self.replay_seed, bool) or not isinstance(self.replay_seed, int):
            raise ValueError("replay_seed must be an integer")
        if self.mandate.payload.constraints.semantic != ():
            raise ValueError(
                "deterministic benchmark mandates must carry no semantic constraints"
            )


@dataclass(frozen=True, slots=True)
class TargetExpectation:
    """Registered expectation for the target family only."""

    family_id: str
    status: str

    def __post_init__(self) -> None:
        if self.family_id not in BENCHMARK_FAMILIES:
            raise ValueError("target_expectation.family_id must be a Tier A/B family")
        allowed = (
            TIER_A_STATUSES if self.family_id in TIER_A_FAMILIES else TIER_B_STATUSES
        )
        if self.status not in allowed:
            raise ValueError(
                f"target_expectation.status {self.status!r} is not registered for "
                f"{self.family_id}"
            )


@dataclass(frozen=True, slots=True)
class GeneratorAudit:
    """Non-hashed reproduction metadata for one generated case."""

    generator_version: str
    generator_seed: int
    recipe_id: str
    recipe_parameters: Mapping[str, RecipeParameterValue]

    def __post_init__(self) -> None:
        if not isinstance(self.generator_version, str) or not self.generator_version:
            raise ValueError("generator_version must be a non-empty string")
        if isinstance(self.generator_seed, bool) or not isinstance(
            self.generator_seed, int
        ):
            raise ValueError("generator_seed must be an integer")
        if not isinstance(self.recipe_id, str) or not self.recipe_id:
            raise ValueError("recipe_id must be a non-empty string")
        parameters = dict(self.recipe_parameters)
        for key, value in parameters.items():
            if not isinstance(key, str) or not key:
                raise ValueError("recipe_parameters keys must be non-empty strings")
            if isinstance(value, float) or not isinstance(
                value, (str, int, bool, type(None))
            ):
                raise ValueError("recipe_parameters values must be JSON scalars")
        object.__setattr__(self, "recipe_parameters", MappingProxyType(parameters))


def _require_registered_label(
    *, ground_truth: str, expected_action: str, status: str
) -> None:
    """Enforce the registered label triples; nothing here consults a detector."""

    registered = {
        ("violation", "BLOCK", "FAIL"),
        ("benign", "ALLOW", "PASS"),
        ("benign", "REVIEW", "NOT_EVALUABLE"),
    }
    if (ground_truth, expected_action, status) not in registered:
        raise ValueError(
            f"unregistered label triple ({ground_truth}, {expected_action}, {status})"
        )


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    """One registered, labelled deterministic benchmark case.

    ``first_run_at`` is audit-only lifecycle metadata excluded from
    ``case_content_sha256``. Generation always leaves it null - the generator
    enforces that independently in ``deterministic_generator._validate`` - and
    the registered manifest schema records it exactly once, at the first
    detector execution, after which it is immutable.
    """

    case_id: str
    case_schema_version: str
    evidence_tier: str
    family_id: str
    provenance: str
    split: str
    ground_truth: str
    label_source: str
    expected_action: str
    target_expectation: TargetExpectation
    evaluation_inputs: EvaluationInputs
    label_recorded_at: datetime
    generator: GeneratorAudit
    first_run_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id:
            raise ValueError("case_id must be a non-empty string")
        if self.case_schema_version != CASE_SCHEMA_VERSION:
            raise ValueError(f"case_schema_version must be {CASE_SCHEMA_VERSION}")
        if self.evidence_tier not in EVIDENCE_TIERS:
            raise ValueError("evidence_tier must be A or B")
        if self.family_id not in BENCHMARK_FAMILIES:
            raise ValueError("family_id must be a registered Tier A/B family")
        if self.family_id[0] != self.evidence_tier:
            raise ValueError("family_id does not belong to evidence_tier")
        if self.provenance not in PROVENANCE_VALUES:
            raise ValueError("Tier A/B provenance must be developer_authored")
        if self.split not in SPLIT_VALUES:
            raise ValueError("Tier A/B split must be dev")
        if self.ground_truth not in GROUND_TRUTH_VALUES:
            raise ValueError("ground_truth must be violation or benign")
        if self.label_source not in LABEL_SOURCE_VALUES:
            raise ValueError("Tier A/B label_source must be deterministic_invariant")
        if self.expected_action not in EXPECTED_ACTIONS:
            raise ValueError("expected_action must be ALLOW, REVIEW, or BLOCK")
        if not isinstance(self.target_expectation, TargetExpectation):
            raise TypeError("target_expectation must be TargetExpectation")
        if self.target_expectation.family_id != self.family_id:
            raise ValueError("target_expectation must name the case family")
        if not isinstance(self.evaluation_inputs, EvaluationInputs):
            raise TypeError("evaluation_inputs must be EvaluationInputs")
        _require_aware_utc(self.label_recorded_at, "label_recorded_at")
        if not isinstance(self.generator, GeneratorAudit):
            raise TypeError("generator must be GeneratorAudit")
        if self.first_run_at is not None:
            _require_aware_utc(self.first_run_at, "first_run_at")
        _require_registered_label(
            ground_truth=self.ground_truth,
            expected_action=self.expected_action,
            status=self.target_expectation.status,
        )
