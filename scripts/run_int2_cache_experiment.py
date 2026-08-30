"""Run one explicit offline-fake INT-2 semantic cache experiment."""

from __future__ import annotations

import argparse
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare an offline fake semantic MISS with an exact HIT."
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=REPOSITORY_ROOT
        / "fixtures"
        / "semantic_mvp"
        / "semantic_cases.jsonl",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT / "artifacts" / "engineering" / "int2",
    )
    return parser


def main(arguments: list[str] | None = None) -> int:
    args = _parser().parse_args(arguments)
    from mandateguard.engineering.int2.artifacts import write_cache_experiment
    from mandateguard.engineering.int2.cache import CacheExperimentHarness
    from mandateguard.engineering.int2.downstream import (
        DownstreamAuthorizationCase,
    )
    from mandateguard.engineering.semantic_fixtures import (
        build_semantic_scenario,
        load_fixture_corpus,
    )
    from mandateguard.intelligence.offline import DeterministicSemanticModel
    from mandateguard.replay.scenario import ReplayScenario

    fixture = load_fixture_corpus(args.fixtures)[0]
    semantic_scenario = build_semantic_scenario(fixture)
    scenario = ReplayScenario(
        mandate=semantic_scenario.mandate,
        transaction=semantic_scenario.transaction,
        catalog_snapshot=semantic_scenario.catalog_snapshot,
        server_time=semantic_scenario.server_time,
        nonce_state=semantic_scenario.nonce_state,
        psp_committed_hashes=semantic_scenario.committed_hashes,
        replay_seed=semantic_scenario.replay_seed,
        evaluated_at=semantic_scenario.evaluated_at,
    )
    entries = semantic_scenario.semantic_evidence.bundle.entries
    case = DownstreamAuthorizationCase(
        query_id="INT2-CACHE-SMVP-001",
        engineering_expectation=fixture.engineering_expectation,
        scenario=scenario,
        eligible_evidence=entries,
    )
    result = CacheExperimentHarness(DeterministicSemanticModel()).run(
        case,
        evidence_ids=tuple(item.evidence_id for item in entries),
    )
    paths = write_cache_experiment(
        result,
        args.output,
        repository_root=REPOSITORY_ROOT,
    )
    print("INT-2 OFFLINE-FAKE CACHE ENGINEERING EXPERIMENT")
    print(
        "semantic_provider_calls="
        f"{result.total_semantic_provider_calls} live_calls=0 razorpay_calls=0"
    )
    print("artifacts=" + ",".join(str(path) for path in paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
