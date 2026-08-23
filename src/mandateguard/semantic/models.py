"""Hash-bound request, output, and authorization result models for Tier C."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any

from mandateguard.core.hashing import sha256_canonical
from mandateguard.models.decision import DecisionAction, DeterministicDecision
from mandateguard.models.mandate import SemanticConstraint
from mandateguard.semantic.evidence import SemanticEvidenceEntry


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_OUTPUT_FIELDS = frozenset({"constraint_results"})
_RESULT_FIELDS = frozenset({"constraint_id", "status", "reason"})


def _require_digest(value: object, name: str) -> None:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")


def _require_version(value: object, name: str) -> None:
    if not isinstance(value, str) or not _VERSION_RE.fullmatch(value):
        raise ValueError(f"{name} must be a bounded version identifier")


def _require_model_id(value: object) -> None:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError("model_id must be a non-empty string of at most 256 characters")


@dataclass(frozen=True, slots=True)
class SemanticRequest:
    """All authoritative inputs that determine one model judgment."""

    detector_version: str
    prompt_version: str
    model_id: str
    mandate_payload_sha256: str
    transaction_body_sha256: str
    catalog_snapshot_sha256: str
    semantic_evidence_sha256: str
    constraints: tuple[SemanticConstraint, ...]
    selected_evidence: tuple[SemanticEvidenceEntry, ...]

    def __post_init__(self) -> None:
        _require_version(self.detector_version, "detector_version")
        _require_version(self.prompt_version, "prompt_version")
        _require_model_id(self.model_id)
        for value, name in (
            (self.mandate_payload_sha256, "mandate_payload_sha256"),
            (self.transaction_body_sha256, "transaction_body_sha256"),
            (self.catalog_snapshot_sha256, "catalog_snapshot_sha256"),
            (self.semantic_evidence_sha256, "semantic_evidence_sha256"),
        ):
            _require_digest(value, name)
        if not isinstance(self.constraints, tuple) or not self.constraints:
            raise ValueError("constraints must be a non-empty tuple")
        if not all(isinstance(item, SemanticConstraint) for item in self.constraints):
            raise ValueError("constraints contains an invalid SemanticConstraint")
        constraint_ids = [item.constraint_id for item in self.constraints]
        if len(constraint_ids) != len(set(constraint_ids)):
            raise ValueError("semantic constraint IDs must be unique")
        if not isinstance(self.selected_evidence, tuple) or not all(
            isinstance(entry, SemanticEvidenceEntry) for entry in self.selected_evidence
        ):
            raise ValueError("selected_evidence must be a tuple of evidence entries")
        object.__setattr__(
            self,
            "constraints",
            tuple(sorted(self.constraints, key=lambda item: item.constraint_id)),
        )
        object.__setattr__(
            self,
            "selected_evidence",
            tuple(
                sorted(
                    self.selected_evidence,
                    key=lambda entry: (
                        entry.sku is not None,
                        entry.sku or "",
                        entry.source_kind,
                        entry.evidence_id,
                    ),
                )
            ),
        )


def semantic_input_sha256(request: SemanticRequest) -> str:
    if not isinstance(request, SemanticRequest):
        raise TypeError("request must be SemanticRequest")
    return sha256_canonical(request)


class ConstraintStatus(str, Enum):
    PASS = "PASS"
    VIOLATION = "VIOLATION"
    ABSTAIN = "ABSTAIN"


@dataclass(frozen=True, slots=True)
class ConstraintResult:
    constraint_id: str
    status: ConstraintStatus
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.constraint_id, str) or not self.constraint_id:
            raise ValueError("constraint_id must be a non-empty string")
        if not isinstance(self.status, ConstraintStatus):
            raise ValueError("status must be PASS, VIOLATION, or ABSTAIN")
        if not isinstance(self.reason, str) or not self.reason or len(self.reason) > 256:
            raise ValueError("reason must be a non-empty string of at most 256 characters")


@dataclass(frozen=True, slots=True)
class NormalizedSemanticOutput:
    """The exact normalized structured response used by the reducer."""

    constraint_results: tuple[ConstraintResult, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.constraint_results, tuple) or not self.constraint_results:
            raise ValueError("constraint_results must be a non-empty tuple")
        if not all(
            isinstance(result, ConstraintResult) for result in self.constraint_results
        ):
            raise ValueError("constraint_results contains an invalid result")
        ids = [result.constraint_id for result in self.constraint_results]
        if len(ids) != len(set(ids)):
            raise ValueError("constraint results must have unique IDs")


def semantic_output_sha256(output: NormalizedSemanticOutput) -> str:
    if not isinstance(output, NormalizedSemanticOutput):
        raise TypeError("output must be NormalizedSemanticOutput")
    return sha256_canonical(output)


def normalize_model_output(
    value: object, constraints: tuple[SemanticConstraint, ...]
) -> NormalizedSemanticOutput:
    """Strictly parse a model response and enforce exact constraint coverage."""

    if not isinstance(value, dict) or frozenset(value) != _OUTPUT_FIELDS:
        raise ValueError("model output must contain only constraint_results")
    raw_results = value["constraint_results"]
    if not isinstance(raw_results, list) or not raw_results:
        raise ValueError("constraint_results must be a non-empty array")
    parsed: dict[str, ConstraintResult] = {}
    for raw_result in raw_results:
        if not isinstance(raw_result, dict) or frozenset(raw_result) != _RESULT_FIELDS:
            raise ValueError("each constraint result must contain exactly the schema fields")
        constraint_id = raw_result["constraint_id"]
        if not isinstance(constraint_id, str) or constraint_id in parsed:
            raise ValueError("constraint result IDs must be strings and unique")
        try:
            status = ConstraintStatus(raw_result["status"])
        except (TypeError, ValueError) as exc:
            raise ValueError("constraint result has an invalid status") from exc
        parsed[constraint_id] = ConstraintResult(
            constraint_id=constraint_id,
            status=status,
            reason=raw_result["reason"],
        )

    expected_ids = tuple(constraint.constraint_id for constraint in constraints)
    if set(parsed) != set(expected_ids) or len(parsed) != len(expected_ids):
        raise ValueError("constraint results must cover every input constraint exactly once")
    return NormalizedSemanticOutput(
        constraint_results=tuple(parsed[constraint_id] for constraint_id in expected_ids)
    )


def normalized_output_to_mapping(output: NormalizedSemanticOutput) -> dict[str, Any]:
    return {
        "constraint_results": [
            {
                "constraint_id": result.constraint_id,
                "status": result.status.value,
                "reason": result.reason,
            }
            for result in output.constraint_results
        ]
    }


class SemanticVerdict(str, Enum):
    PASS = "PASS"
    VIOLATION = "VIOLATION"
    ABSTAIN = "ABSTAIN"


def reduce_semantic_verdict(
    constraint_results: tuple[ConstraintResult, ...],
) -> SemanticVerdict:
    if any(result.status is ConstraintStatus.VIOLATION for result in constraint_results):
        return SemanticVerdict.VIOLATION
    if any(result.status is ConstraintStatus.ABSTAIN for result in constraint_results):
        return SemanticVerdict.ABSTAIN
    return SemanticVerdict.PASS


@dataclass(frozen=True, slots=True)
class SemanticDecision:
    semantic_input_sha256: str
    semantic_output_sha256: str
    prompt_version: str
    model_id: str
    constraint_results: tuple[ConstraintResult, ...]
    verdict: SemanticVerdict

    def __post_init__(self) -> None:
        _require_digest(self.semantic_input_sha256, "semantic_input_sha256")
        _require_digest(self.semantic_output_sha256, "semantic_output_sha256")
        _require_version(self.prompt_version, "prompt_version")
        _require_model_id(self.model_id)
        output = NormalizedSemanticOutput(self.constraint_results)
        if semantic_output_sha256(output) != self.semantic_output_sha256:
            raise ValueError("semantic_output_sha256 does not commit constraint_results")
        if not isinstance(self.verdict, SemanticVerdict):
            raise ValueError("verdict must be a SemanticVerdict")
        if reduce_semantic_verdict(self.constraint_results) is not self.verdict:
            raise ValueError("verdict does not match deterministic semantic reduction")


def action_for_semantic_verdict(verdict: SemanticVerdict) -> DecisionAction:
    if verdict is SemanticVerdict.PASS:
        return DecisionAction.ALLOW
    if verdict is SemanticVerdict.VIOLATION:
        return DecisionAction.BLOCK
    if verdict is SemanticVerdict.ABSTAIN:
        return DecisionAction.REVIEW
    raise TypeError("verdict must be a SemanticVerdict")


@dataclass(frozen=True, slots=True)
class AuthorizationResult:
    deterministic_decision: DeterministicDecision
    semantic_decision: SemanticDecision | None
    final_action: DecisionAction
    semantic_constraints_present: bool

    def __post_init__(self) -> None:
        if not isinstance(self.deterministic_decision, DeterministicDecision):
            raise TypeError("deterministic_decision must be DeterministicDecision")
        if self.semantic_decision is not None and not isinstance(
            self.semantic_decision, SemanticDecision
        ):
            raise TypeError("semantic_decision must be SemanticDecision or None")
        if not isinstance(self.final_action, DecisionAction):
            raise TypeError("final_action must be DecisionAction")
        if not isinstance(self.semantic_constraints_present, bool):
            raise TypeError("semantic_constraints_present must be boolean")

        deterministic_action = self.deterministic_decision.action
        if deterministic_action is DecisionAction.BLOCK:
            if self.semantic_decision is not None or self.final_action is not DecisionAction.BLOCK:
                raise ValueError("deterministic BLOCK cannot have a semantic override")
            return
        if deterministic_action is DecisionAction.REVIEW:
            if self.semantic_decision is not None or self.final_action is not DecisionAction.REVIEW:
                raise ValueError("deterministic REVIEW cannot have a semantic override")
            return
        if not self.semantic_constraints_present:
            if self.semantic_decision is not None or self.final_action is not DecisionAction.ALLOW:
                raise ValueError("deterministic ALLOW without semantics must remain ALLOW")
            return
        if self.semantic_decision is None:
            raise ValueError("deterministic ALLOW with semantics requires a semantic decision")
        expected = action_for_semantic_verdict(self.semantic_decision.verdict)
        if self.final_action is not expected:
            raise ValueError("final_action does not match the deterministic semantic reducer")
