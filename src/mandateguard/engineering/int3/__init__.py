"""INT-3 evidence-sufficiency and value-of-information infrastructure."""

from mandateguard.engineering.int3.artifacts import (
    FUTURE_SUBSET_RESULTS_FILENAME,
    FUTURE_SUFFICIENCY_DATASET_FILENAME,
    SUBSET_PLAN_FILENAME,
    build_unlabeled_sufficiency_dataset,
    subset_plan_record,
    write_subset_plan_jsonl,
)
from mandateguard.engineering.int3.controller import (
    ControllerAction,
    ControllerCosts,
    ControllerDecision,
    select_controller_action,
)
from mandateguard.engineering.int3.dataset import (
    SufficiencyDataset,
    SufficiencyDatasetRow,
    build_dataset,
    dataset_csv_columns,
    decision_stable,
    write_sufficiency_dataset_csv,
)
from mandateguard.engineering.int3.demo import (
    OfflineDemoScenario,
    run_offline_demo,
)
from mandateguard.engineering.int3.features import (
    FEATURE_DEFINITIONS,
    FEATURE_NAMES,
    EvidenceScoreRecord,
    RetrievalScoreSurface,
    SubsetFeatureInput,
    assert_no_target_leakage,
    extract_subset_features,
    feature_vector,
)
from mandateguard.engineering.int3.metrics import (
    SufficiencyMetrics,
    brier_score,
    evaluate_sufficiency,
    roc_auc,
)
from mandateguard.engineering.int3.model import (
    SufficiencyModel,
    SufficiencyModelNotFittedError,
    SufficiencyModelUnavailableError,
    SufficiencyTrainingDataError,
)
from mandateguard.engineering.int3.models import (
    CaseFamily,
    FullEvidenceReference,
    Int3ExperimentError,
    SubsetEquivalenceClass,
    SubsetObservation,
    SubsetPlan,
    case_family_for_constraint_kinds,
    subset_counts_by_query,
)
from mandateguard.engineering.int3.safety import (
    SafeSufficiencyDecision,
    SufficiencyRoute,
    enforce_sufficiency_safety_boundary,
)
from mandateguard.engineering.int3.splits import (
    LeaveOneQueryOutFold,
    assert_no_query_leakage,
    leave_one_query_out_folds,
)
from mandateguard.engineering.int3.subsets import (
    FULL_EVIDENCE_CONDITION_LABEL,
    FULL_EVIDENCE_CONDITION_ROLE,
    SubsetPlanError,
    build_subset_feature_input,
    build_subset_plan,
    enumerate_nonempty_subsets,
    load_full_evidence_references,
    load_reference_score_surface,
    subset_mask,
    subset_observation_id,
)
from mandateguard.engineering.int3.voi import (
    EvidenceValueCandidate,
    rank_evidence_by_voi,
)

__all__ = [
    "CaseFamily",
    "ControllerAction",
    "ControllerCosts",
    "ControllerDecision",
    "EvidenceScoreRecord",
    "EvidenceValueCandidate",
    "FEATURE_DEFINITIONS",
    "FEATURE_NAMES",
    "FULL_EVIDENCE_CONDITION_LABEL",
    "FULL_EVIDENCE_CONDITION_ROLE",
    "FUTURE_SUBSET_RESULTS_FILENAME",
    "FUTURE_SUFFICIENCY_DATASET_FILENAME",
    "FullEvidenceReference",
    "Int3ExperimentError",
    "LeaveOneQueryOutFold",
    "OfflineDemoScenario",
    "RetrievalScoreSurface",
    "SUBSET_PLAN_FILENAME",
    "SafeSufficiencyDecision",
    "SubsetEquivalenceClass",
    "SubsetFeatureInput",
    "SubsetObservation",
    "SubsetPlan",
    "SubsetPlanError",
    "SufficiencyDataset",
    "SufficiencyDatasetRow",
    "SufficiencyMetrics",
    "SufficiencyModel",
    "SufficiencyModelNotFittedError",
    "SufficiencyModelUnavailableError",
    "SufficiencyRoute",
    "SufficiencyTrainingDataError",
    "assert_no_query_leakage",
    "assert_no_target_leakage",
    "brier_score",
    "build_dataset",
    "build_subset_feature_input",
    "build_subset_plan",
    "build_unlabeled_sufficiency_dataset",
    "case_family_for_constraint_kinds",
    "dataset_csv_columns",
    "decision_stable",
    "enforce_sufficiency_safety_boundary",
    "enumerate_nonempty_subsets",
    "evaluate_sufficiency",
    "extract_subset_features",
    "feature_vector",
    "leave_one_query_out_folds",
    "load_full_evidence_references",
    "load_reference_score_surface",
    "rank_evidence_by_voi",
    "roc_auc",
    "run_offline_demo",
    "select_controller_action",
    "subset_counts_by_query",
    "subset_mask",
    "subset_observation_id",
    "subset_plan_record",
    "write_subset_plan_jsonl",
    "write_sufficiency_dataset_csv",
]
