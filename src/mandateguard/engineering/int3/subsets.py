"""Offline enumeration of every non-empty trusted-evidence subset.

This module builds the INT-3A *plan* only.  It reads the frozen INT-2 Stage-B
cases and the already-recorded Stage-B production observations, enumerates
subsets, and computes each subset's semantic input hash.  It never calls a
semantic provider, an embedding provider, or a payment API, and it never
mutates any frozen INT-1 or INT-2 input.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from itertools import combinations
import json
from pathlib import Path
from typing import Any, Mapping

from mandateguard.engineering.int2.downstream import selected_semantic_evidence
from mandateguard.engineering.int2.models import RelevanceManifest
from mandateguard.engineering.int2.stage_b_cases import (
    FrozenStageBCase,
    StageBCaseManifest,
)
from mandateguard.engineering.int3.features import (
    REFERENCE_RETRIEVAL_ALPHA,
    REFERENCE_SURFACE_CONFIGURATION_ID,
    EvidenceScoreRecord,
    RetrievalScoreSurface,
    SubsetFeatureInput,
)
from mandateguard.engineering.int3.models import (
    FullEvidenceReference,
    Int3ExperimentError,
    SubsetEquivalenceClass,
    SubsetObservation,
    SubsetPlan,
    case_family_for_constraint_kinds,
)
from mandateguard.semantic.evidence import SemanticEvidenceEntry
from mandateguard.semantic.models import semantic_input_sha256
from mandateguard.semantic.verifier import (
    SEMANTIC_DETECTOR_VERSION,
    SEMANTIC_PROMPT_VERSION,
    build_semantic_request,
)


#: Stage-B condition whose retrieval surface covers the complete eligible
#: evidence set for every frozen query.  This is the existing INT-2 production
#: / full-evidence path; INT-3 adopts its recorded result as the reference.
FULL_EVIDENCE_CONDITION_LABEL = "E"
FULL_EVIDENCE_CONDITION_ROLE = "PRODUCTION DEFAULT"


class SubsetPlanError(Int3ExperimentError):
    """The frozen inputs cannot produce a valid INT-3A subset plan."""


def _read_jsonl(path: Path) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(path, Path):
        raise TypeError("path must be pathlib.Path")
    try:
        records = tuple(
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise SubsetPlanError(f"cannot read frozen INT-2 records from {path}") from error
    if not records or not all(isinstance(item, dict) for item in records):
        raise SubsetPlanError(f"{path} must contain JSON object records")
    return records


def load_reference_score_surface(
    stage_a_observations_path: Path,
) -> RetrievalScoreSurface:
    """Read both retrieval channels from the frozen Stage-A surface record.

    The surface is read only; the Stage-A artifact is never rewritten.  Scores
    are blended with the fixed, untuned INT-2 production alpha.
    """

    records = _read_jsonl(stage_a_observations_path)
    scores: dict[str, dict[str, EvidenceScoreRecord]] = {}
    for record in records:
        if record.get("configuration_id") != REFERENCE_SURFACE_CONFIGURATION_ID:
            continue
        query_id = record.get("query_id")
        if not isinstance(query_id, str) or not query_id:
            raise SubsetPlanError("Stage-A observation query_id is invalid")
        if query_id in scores:
            raise SubsetPlanError("duplicate Stage-A reference surface record")
        ranked = record.get("ranked_documents")
        if not isinstance(ranked, list) or not ranked:
            raise SubsetPlanError("Stage-A reference surface is incomplete")
        per_query: dict[str, EvidenceScoreRecord] = {}
        for item in ranked:
            if not isinstance(item, dict):
                raise SubsetPlanError("Stage-A ranked document is invalid")
            evidence_id = item.get("evidence_id")
            if not isinstance(evidence_id, str) or not evidence_id:
                raise SubsetPlanError("Stage-A ranked document lacks an evidence ID")
            per_query[evidence_id] = EvidenceScoreRecord(
                evidence_id=evidence_id,
                lexical_score=item.get("lexical_score"),
                semantic_score=item.get("semantic_score"),
            )
        scores[query_id] = per_query
    if not scores:
        raise SubsetPlanError(
            f"no {REFERENCE_SURFACE_CONFIGURATION_ID} records in the Stage-A run"
        )
    return RetrievalScoreSurface(
        configuration_id=REFERENCE_SURFACE_CONFIGURATION_ID,
        alpha=REFERENCE_RETRIEVAL_ALPHA,
        scores=scores,
    )


def load_full_evidence_references(
    stage_b_observations_path: Path,
    *,
    cases: StageBCaseManifest,
) -> tuple[FullEvidenceReference, ...]:
    """Adopt the recorded Stage-B production observation as the reference.

    The reference is *read*, never recomputed against a model.  Its retrieval
    surface must already cover the complete eligible evidence set, otherwise it
    is not a full-evidence path and is rejected.
    """

    if not isinstance(cases, StageBCaseManifest):
        raise TypeError("cases must be StageBCaseManifest")
    records = _read_jsonl(stage_b_observations_path)
    by_query: dict[str, Mapping[str, Any]] = {}
    for record in records:
        condition = record.get("condition")
        if not isinstance(condition, dict):
            raise SubsetPlanError("Stage-B observation condition is invalid")
        if condition.get("label") != FULL_EVIDENCE_CONDITION_LABEL:
            continue
        if condition.get("role") != FULL_EVIDENCE_CONDITION_ROLE:
            raise SubsetPlanError(
                "the full-evidence condition is not the frozen production default"
            )
        query_id = record.get("query_id")
        if not isinstance(query_id, str) or query_id in by_query:
            raise SubsetPlanError("Stage-B production observations must be unique")
        by_query[query_id] = record

    references: list[FullEvidenceReference] = []
    for case in cases.cases:
        record = by_query.get(case.query_id)
        if record is None:
            raise SubsetPlanError(
                f"no full-evidence Stage-B observation for {case.query_id!r}"
            )
        if record.get("semantic_status") != "EVALUATED":
            raise SubsetPlanError(
                "the full-evidence reference must be an evaluated observation"
            )
        retrieved = record.get("retrieved_trusted_evidence_ids")
        if not isinstance(retrieved, list) or set(retrieved) != set(
            case.eligible_evidence_ids
        ):
            raise SubsetPlanError(
                f"{case.query_id} reference did not use the full eligible evidence"
            )
        selected = record.get("selected_trusted_evidence_ids")
        if not isinstance(selected, list) or not selected:
            raise SubsetPlanError("reference selection must be non-empty")
        references.append(
            FullEvidenceReference(
                query_id=case.query_id,
                source_run_id=record.get("run_id"),
                source_observation_id=record.get("observation_id"),
                model_id=record.get("model_id"),
                prompt_version=record.get("prompt_version"),
                detector_version=record.get("detector_version"),
                full_reference_semantic_behavior=record.get(
                    "observed_semantic_behavior"
                ),
                full_reference_action=record.get("final_action"),
                full_reference_semantic_input_sha256=record.get(
                    "semantic_input_sha256"
                ),
                full_evidence_ids=case.eligible_evidence_ids,
                sku_scoped_selected_evidence_ids=tuple(selected),
            )
        )
    return tuple(references)


def enumerate_nonempty_subsets(
    eligible_evidence_ids: tuple[str, ...],
) -> tuple[tuple[str, ...], ...]:
    """Enumerate every non-empty subset in frozen (size, eligible-order) order."""

    if not isinstance(eligible_evidence_ids, tuple) or not eligible_evidence_ids:
        raise Int3ExperimentError("eligible_evidence_ids must be a non-empty tuple")
    if len(eligible_evidence_ids) != len(set(eligible_evidence_ids)):
        raise Int3ExperimentError("eligible_evidence_ids must be unique")
    return tuple(
        subset
        for size in range(1, len(eligible_evidence_ids) + 1)
        for subset in combinations(eligible_evidence_ids, size)
    )


def subset_mask(
    *, eligible_evidence_ids: tuple[str, ...], subset_evidence_ids: tuple[str, ...]
) -> str:
    """Render the stable eligible-order bitmask that identifies a subset."""

    selected = frozenset(subset_evidence_ids)
    if not selected.issubset(eligible_evidence_ids):
        raise Int3ExperimentError("subset evidence must be eligible evidence")
    return "".join(
        "1" if item in selected else "0" for item in eligible_evidence_ids
    )


def subset_observation_id(*, query_id: str, mask: str) -> str:
    """Build the stable observation identifier for one query/subset pair."""

    if not isinstance(query_id, str) or not query_id:
        raise Int3ExperimentError("query_id must be non-empty")
    if not isinstance(mask, str) or not mask or set(mask) - {"0", "1"}:
        raise Int3ExperimentError("mask must be a non-empty bitmask")
    return f"INT3:{query_id}:m{mask}"


def build_subset_feature_input(
    case: FrozenStageBCase,
    subset_evidence_ids: tuple[str, ...],
    *,
    relevance: RelevanceManifest,
    score_surface: RetrievalScoreSurface | None = None,
) -> SubsetFeatureInput:
    """Assemble only the pre-inference facts the feature extractor may read."""

    if not isinstance(case, FrozenStageBCase):
        raise TypeError("case must be FrozenStageBCase")
    if not isinstance(relevance, RelevanceManifest):
        raise TypeError("relevance must be RelevanceManifest")
    eligible = case.downstream_case.eligible_evidence
    by_id = {item.evidence_id: item for item in eligible}
    try:
        subset = tuple(by_id[item] for item in subset_evidence_ids)
    except KeyError as error:
        raise Int3ExperimentError(
            "subset evidence must be drawn from the case eligible evidence"
        ) from error
    annotation = relevance.for_query(case.query_id)
    scenario = case.scenario
    return SubsetFeatureInput(
        query_id=case.query_id,
        eligible_evidence=eligible,
        subset_evidence=subset,
        transaction_skus=tuple(
            line.sku for line in scenario.transaction.payload.lines
        ),
        constraint_kinds=tuple(
            item.kind for item in scenario.mandate.payload.constraints.semantic
        ),
        required_evidence_ids=annotation.required_evidence_ids,
        relevant_evidence_ids=annotation.relevant_evidence_ids,
        score_surface=score_surface,
    )


def _subset_semantic_input(
    case: FrozenStageBCase,
    subset_evidence_ids: tuple[str, ...],
    *,
    model_id: str,
) -> tuple[str, tuple[str, ...]]:
    """Hash one subset's semantic input without evaluating any model."""

    evidence = selected_semantic_evidence(
        case.downstream_case, subset_evidence_ids
    )
    if evidence is None:
        raise SubsetPlanError("a non-empty subset must resolve trusted evidence")
    scenario = case.scenario
    request = build_semantic_request(
        mandate=scenario.mandate,
        transaction=scenario.transaction,
        catalog_snapshot=scenario.catalog_snapshot,
        semantic_evidence=evidence,
        model_id=model_id,
        prompt_version=SEMANTIC_PROMPT_VERSION,
        detector_version=SEMANTIC_DETECTOR_VERSION,
    )
    return (
        semantic_input_sha256(request),
        tuple(item.evidence_id for item in request.selected_evidence),
    )


def build_subset_plan(
    *,
    cases: StageBCaseManifest,
    references: tuple[FullEvidenceReference, ...],
    created_at: datetime,
) -> SubsetPlan:
    """Enumerate every non-empty subset of every frozen case, offline."""

    if not isinstance(cases, StageBCaseManifest):
        raise TypeError("cases must be StageBCaseManifest")
    if not isinstance(references, tuple) or not references:
        raise SubsetPlanError("references must be a non-empty tuple")
    if not all(isinstance(item, FullEvidenceReference) for item in references):
        raise SubsetPlanError("references contains an invalid record")
    by_query = {item.query_id: item for item in references}
    if set(by_query) != {case.query_id for case in cases.cases}:
        raise SubsetPlanError("references must cover exactly the frozen cases")

    model_ids = {item.model_id for item in references}
    prompt_versions = {item.prompt_version for item in references}
    detector_versions = {item.detector_version for item in references}
    if (
        len(model_ids) != 1
        or len(prompt_versions) != 1
        or len(detector_versions) != 1
    ):
        raise SubsetPlanError(
            "every full-evidence reference must share one model and version pair"
        )
    model_id = model_ids.pop()
    prompt_version = prompt_versions.pop()
    detector_version = detector_versions.pop()
    if (
        prompt_version != SEMANTIC_PROMPT_VERSION
        or detector_version != SEMANTIC_DETECTOR_VERSION
    ):
        raise SubsetPlanError(
            "the frozen reference prompt/detector versions no longer match the code"
        )

    drafts: list[dict[str, Any]] = []
    hashes: dict[str, list[str]] = defaultdict(list)
    for case in cases.cases:
        reference = by_query[case.query_id]
        eligible = case.eligible_evidence_ids
        if reference.full_evidence_ids != eligible:
            raise SubsetPlanError(
                f"{case.query_id} reference evidence differs from the frozen case"
            )
        family = case_family_for_constraint_kinds(
            tuple(
                item.kind
                for item in case.scenario.mandate.payload.constraints.semantic
            )
        )
        for subset_ids in enumerate_nonempty_subsets(eligible):
            digest, selected = _subset_semantic_input(
                case, subset_ids, model_id=model_id
            )
            mask = subset_mask(
                eligible_evidence_ids=eligible, subset_evidence_ids=subset_ids
            )
            observation_id = subset_observation_id(
                query_id=case.query_id, mask=mask
            )
            hashes[digest].append(observation_id)
            drafts.append(
                {
                    "observation_id": observation_id,
                    "query_id": case.query_id,
                    "eligible": eligible,
                    "subset": subset_ids,
                    "mask": mask,
                    "case_family": family,
                    "reference": reference,
                    "digest": digest,
                    "selected": selected,
                }
            )

    for reference in references:
        full_mask = "1" * len(reference.full_evidence_ids)
        full_id = subset_observation_id(
            query_id=reference.query_id, mask=full_mask
        )
        computed = next(
            item["digest"] for item in drafts if item["observation_id"] == full_id
        )
        if computed != reference.full_reference_semantic_input_sha256:
            raise SubsetPlanError(
                f"{reference.query_id} full-evidence subset does not reproduce the "
                "frozen Stage-B reference semantic input hash"
            )

    canonical_by_hash = {key: members[0] for key, members in hashes.items()}
    observations = tuple(
        SubsetObservation(
            observation_id=draft["observation_id"],
            query_id=draft["query_id"],
            eligible_evidence_ids=draft["eligible"],
            subset_evidence_ids=draft["subset"],
            subset_size=len(draft["subset"]),
            eligible_size=len(draft["eligible"]),
            subset_mask=draft["mask"],
            case_family=draft["case_family"],
            full_reference_semantic_behavior=(
                draft["reference"].full_reference_semantic_behavior
            ),
            full_reference_action=draft["reference"].full_reference_action,
            full_reference_semantic_input_sha256=(
                draft["reference"].full_reference_semantic_input_sha256
            ),
            subset_semantic_input_sha256=draft["digest"],
            sku_scoped_selected_evidence_ids=draft["selected"],
            matches_full_reference_semantic_input=(
                draft["digest"]
                == draft["reference"].full_reference_semantic_input_sha256
            ),
            is_full_evidence_subset=len(draft["subset"]) == len(draft["eligible"]),
            canonical_observation_id=canonical_by_hash[draft["digest"]],
            planned_semantic_call=(
                draft["observation_id"] == canonical_by_hash[draft["digest"]]
            ),
        )
        for draft in drafts
    )
    reference_hashes = {
        item.full_reference_semantic_input_sha256 for item in references
    }
    equivalence_classes = tuple(
        SubsetEquivalenceClass(
            semantic_input_sha256=digest,
            canonical_observation_id=members[0],
            member_observation_ids=tuple(members),
            matches_full_reference_semantic_input=digest in reference_hashes,
        )
        for digest, members in sorted(hashes.items())
    )
    return SubsetPlan(
        schema_version="1.0",
        created_at=created_at,
        model_id=model_id,
        prompt_version=prompt_version,
        detector_version=detector_version,
        references=references,
        observations=observations,
        equivalence_classes=equivalence_classes,
    )


def eligible_evidence_by_id(
    case: FrozenStageBCase,
) -> Mapping[str, SemanticEvidenceEntry]:
    """Index one case's frozen eligible evidence without copying its text."""

    if not isinstance(case, FrozenStageBCase):
        raise TypeError("case must be FrozenStageBCase")
    return {
        item.evidence_id: item for item in case.downstream_case.eligible_evidence
    }
