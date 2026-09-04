"""The invariants the authorization-scale benchmark exists to hold.

A scale number is only worth reporting if the safety properties survive the
scale. These tests run the real path over a small slice covering every family and
assert the properties that must not degrade: no provider call on a refusal,
replay refused, revocation refused, and identity mutations caught after a
capability already exists.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mandateguard.engineering.authscale.benchmark import (
    CountingProviderClient,
    run_benchmark,
)
from mandateguard.engineering.authscale.universe import FAMILIES


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

#: 170 cases is ten of every family: the smallest slice that exercises all
#: seventeen construction recipes and both refusal layers.
SLICE = 170


@pytest.fixture(scope="module")
def report() -> dict:
    return run_benchmark(case_count=SLICE)


def test_every_family_is_present_in_the_slice(report: dict) -> None:
    assert set(report["by_family"]) == set(FAMILIES)
    for block in report["by_family"].values():
        assert block["cases"] == 10


def test_the_construction_labels_are_met_by_the_real_controller(report: dict) -> None:
    counters = report["counters"]
    assert counters["target_invariant_agreement"] == counters["total_cases"] == SLICE
    assert report["disagreements"] == []


def test_no_provider_call_happens_on_a_blocked_decision(report: dict) -> None:
    assert report["counters"]["provider_calls_on_block"] == 0


def test_no_provider_call_happens_on_a_review_decision(report: dict) -> None:
    assert report["counters"]["provider_calls_on_review"] == 0


def test_no_provider_call_precedes_an_allow(report: dict) -> None:
    assert report["counters"]["provider_calls_before_allow"] == 0


def test_only_benign_cases_dispatch_to_the_provider(report: dict) -> None:
    """One authorized dispatch per ALLOW, and none anywhere else.

    CAPABILITY_REPLAY has an authorized *setup* dispatch before the replay it is
    labelled on. It is counted separately precisely so it cannot be mistaken for
    a call on a refusal.
    """

    for family, block in report["by_family"].items():
        if family == "BENIGN_ALLOWED":
            assert block["provider_calls"] == block["cases"]
        else:
            assert block["provider_calls"] == 0, family
        if family != "CAPABILITY_REPLAY":
            assert block["authorized_setup_provider_calls"] == 0, family


def test_replay_is_refused_at_scale(report: dict) -> None:
    block = report["by_family"]["CAPABILITY_REPLAY"]
    assert block["pipeline_actions"] == {"BLOCK": block["cases"]}
    assert "NONCE_ALREADY_USED" in block["refusal_reasons"]
    assert report["counters"]["replay_rejections"] == block["cases"]
    # The replay submission itself dispatched nothing.
    assert block["provider_calls"] == 0


def test_revocation_is_refused_at_scale(report: dict) -> None:
    block = report["by_family"]["MANDATE_REVOKED"]
    assert block["pipeline_actions"] == {"BLOCK": block["cases"]}
    assert "MANDATE_REVOKED" in block["refusal_reasons"]
    assert report["counters"]["revocation_rejections"] == block["cases"]
    assert block["provider_calls"] == 0


def test_supersession_is_refused_at_scale(report: dict) -> None:
    block = report["by_family"]["MANDATE_SUPERSEDED"]
    assert block["pipeline_actions"] == {"BLOCK": block["cases"]}
    assert "MANDATE_SUPERSEDED" in block["refusal_reasons"]


def test_identity_and_request_mutations_are_caught_after_issuance(report: dict) -> None:
    for family in ("WRONG_MERCHANT", "WRONG_SKU", "PRICE_MUTATION", "REQUEST_MUTATION"):
        block = report["by_family"][family]
        # The controller allowed; the gate refused. That is the design.
        assert block["controller_actions"] == {"ALLOW": block["cases"]}, family
        assert block["pipeline_actions"] == {"BLOCK": block["cases"]}, family
        assert block["provider_calls"] == 0, family
    assert report["counters"]["request_mutation_rejections"] == 10
    assert report["counters"]["merchant_sku_mismatch_rejections"] == 20


def test_an_expired_capability_is_refused_at_its_boundary(report: dict) -> None:
    block = report["by_family"]["CAPABILITY_EXPIRED"]
    assert block["pipeline_actions"] == {"BLOCK": block["cases"]}
    assert "CAPABILITY_EXPIRED" in block["refusal_reasons"]


def test_a_capability_is_never_issued_for_a_block_or_review(report: dict) -> None:
    """Issuance is ALLOW-only; refusals never mint one."""

    issued = report["counters"]["capabilities_issued"]
    controller_allows = sum(
        block["controller_actions"].get("ALLOW", 0)
        for block in report["by_family"].values()
    )
    assert issued == controller_allows


def test_evidence_absent_families_reach_review_not_block(report: dict) -> None:
    for family in (
        "MISSING_EVIDENCE",
        "AUTHORITY_CONFLICT",
        "STALE_EVIDENCE",
        "SUPERSEDED_EVIDENCE",
    ):
        block = report["by_family"][family]
        assert block["pipeline_actions"] == {"REVIEW": block["cases"]}, family
        assert block["provider_calls"] == 0, family


def test_the_benchmark_records_zero_external_calls(report: dict) -> None:
    assert report["external_calls"] == {
        "openai": 0,
        "razorpay_http": 0,
        "hugging_face_api": 0,
    }


def test_the_benchmark_states_its_scope_limit(report: dict) -> None:
    scope = report["scope_limit"].lower()
    assert "one process" in scope
    assert "synthetic" in scope
    assert "not a merchant network" in scope
    assert "distributed" in scope


def test_the_provider_client_opens_no_socket() -> None:
    """The stub is a counter, not a client."""

    import ast

    tree = ast.parse(
        (
            REPOSITORY_ROOT / "src" / "mandateguard" / "engineering" / "authscale"
            / "benchmark.py"
        ).read_text(encoding="utf-8")
    )
    banned = {"socket", "urllib", "requests", "http", "httpx", "ssl", "asyncio"}
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & banned), sorted(imported & banned)

    client = CountingProviderClient()
    assert client.calls == 0


def test_the_benchmark_is_deterministic() -> None:
    """Same rung, same counts. Latency is allowed to differ; nothing else is."""

    first = run_benchmark(case_count=51)
    second = run_benchmark(case_count=51)
    assert first["actions"] == second["actions"]
    assert first["counters"]["target_invariant_agreement"] == (
        second["counters"]["target_invariant_agreement"]
    )
    assert first["case_descriptor_stream_sha256"] == (
        second["case_descriptor_stream_sha256"]
    )
    assert first["by_family"] == second["by_family"]


# --------------------------------------------------------------------------
# The committed report
# --------------------------------------------------------------------------


def _committed_report() -> dict | None:
    path = (
        REPOSITORY_ROOT / "artifacts" / "engineering" / "authorization-scale"
        / "benchmark.json"
    )
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_committed_report_holds_the_invariants_at_every_rung() -> None:
    report = _committed_report()
    if report is None:
        pytest.skip("the authorization-scale benchmark has not been run")
    assert report["ladder"]
    for rung in report["ladder"]:
        counters = rung["counters"]
        assert counters["target_invariant_agreement"] == counters["total_cases"], (
            rung["case_count"]
        )
        assert counters["provider_calls_on_block"] == 0
        assert counters["provider_calls_on_review"] == 0
        assert counters["provider_calls_before_allow"] == 0
        assert rung["disagreements"] == []
        assert rung["external_calls"]["razorpay_http"] == 0
        assert rung["external_calls"]["openai"] == 0


def test_every_frozen_rung_is_labelled_as_matching_the_freeze() -> None:
    report = _committed_report()
    if report is None:
        pytest.skip("the authorization-scale benchmark has not been run")
    freeze = json.loads(
        (
            REPOSITORY_ROOT / "data" / "eval" / "authorization-scale"
            / "WORLD_FREEZE.json"
        ).read_text(encoding="utf-8")
    )
    frozen_counts = {rung["case_count"] for rung in freeze["scale_ladder"]}
    for rung in report["ladder"]:
        expected = (
            "MATCHES_FREEZE"
            if rung["case_count"] in frozen_counts
            else "NOT_IN_FREEZE_EXPLORATORY"
        )
        assert rung["freeze_status"] == expected, rung["case_count"]
