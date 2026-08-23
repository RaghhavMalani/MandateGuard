from __future__ import annotations

from mandateguard.core.canonical import canonical_json_bytes
from mandateguard.core.hashing import (
    CommittedHashes,
    catalog_snapshot_sha256,
    transaction_body_sha256,
)
from mandateguard.core.nonce_ledger import NonceLedgerState
from mandateguard.models.decision import DecisionAction, decide_deterministically
from mandateguard.models.finding import TaxonomyFamily, TierACheckStatus
from mandateguard.policy.tier_a import evaluate_tier_a
from mandateguard.policy.tier_b import evaluate_tier_b
from tests.factories import (
    SERVER_TIME,
    make_catalog,
    make_commitments,
    make_line,
    make_mandate,
    make_payload,
    make_transaction,
)


def _families(findings: tuple) -> set[TaxonomyFamily]:
    return {finding.family for finding in findings}


def _tier_a_status(results: tuple, family: TaxonomyFamily) -> TierACheckStatus:
    return next(result.status for result in results if result.family is family)


def test_tier_b_happy_path_passes_all_checks() -> None:
    assert evaluate_tier_b(mandate=make_mandate(), transaction=make_transaction()) == ()


def test_b1_checks_declared_order_line_sum() -> None:
    line = make_line(effective_unit_price_minor=10_000, quantity=2)
    transaction = make_transaction(
        payload=make_payload(lines=(line,), declared_order_total_minor=10_000)
    )

    assert TaxonomyFamily.B1 in _families(
        evaluate_tier_b(mandate=make_mandate(), transaction=transaction)
    )


def test_b2_checks_aggregate_quantity() -> None:
    transaction = make_transaction(
        payload=make_payload(declared_aggregate_quantity=2)
    )

    assert TaxonomyFamily.B2 in _families(
        evaluate_tier_b(mandate=make_mandate(), transaction=transaction)
    )


def test_b3_checks_mandate_cart_and_order_currency() -> None:
    transaction = make_transaction(payload=make_payload(cart_currency="USD"))

    assert TaxonomyFamily.B3 in _families(
        evaluate_tier_b(mandate=make_mandate(), transaction=transaction)
    )


def test_b4_checks_self_reported_recurrence_fields() -> None:
    transaction = make_transaction(payload=make_payload(cart_recurring=True))

    assert TaxonomyFamily.B4 in _families(
        evaluate_tier_b(mandate=make_mandate(), transaction=transaction)
    )


def test_b5_recomputes_canonical_transaction_hash() -> None:
    transaction = make_transaction(declared_transaction_hash="0" * 64)

    assert TaxonomyFamily.B5 in _families(
        evaluate_tier_b(mandate=make_mandate(), transaction=transaction)
    )


def test_b6_checks_declared_order_total_against_mandate() -> None:
    transaction = make_transaction(
        payload=make_payload(lines=(make_line(effective_unit_price_minor=500_001),))
    )

    assert TaxonomyFamily.B6 in _families(
        evaluate_tier_b(mandate=make_mandate(max_total_minor=500_000), transaction=transaction)
    )


def test_b7_checks_declared_quantity_against_mandate() -> None:
    transaction = make_transaction(payload=make_payload(lines=(make_line(quantity=2),)))

    assert TaxonomyFamily.B7 in _families(
        evaluate_tier_b(mandate=make_mandate(max_quantity=1), transaction=transaction)
    )


def test_b8_checks_declared_recurrence_against_mandate() -> None:
    transaction = make_transaction(payload=make_payload(lines=(make_line(recurring=True),)))

    assert TaxonomyFamily.B8 in _families(
        evaluate_tier_b(
            mandate=make_mandate(recurring_allowed=False), transaction=transaction
        )
    )


def test_b9_checks_declared_merchant_allowlist() -> None:
    transaction = make_transaction(payload=make_payload(merchant_id="merchant-2"))

    assert TaxonomyFamily.B9 in _families(
        evaluate_tier_b(mandate=make_mandate(), transaction=transaction)
    )


def test_b10_checks_declared_sku_allowlist() -> None:
    transaction = make_transaction(payload=make_payload(lines=(make_line(sku="sku-2"),)))

    assert TaxonomyFamily.B10 in _families(
        evaluate_tier_b(mandate=make_mandate(), transaction=transaction)
    )


def test_absent_allowlists_do_not_create_constraints() -> None:
    mandate = make_mandate(merchant_allowlist=None, sku_allowlist=None)
    transaction = make_transaction(
        payload=make_payload(merchant_id="any-merchant", lines=(make_line(sku="any-sku"),))
    )

    families = _families(evaluate_tier_b(mandate=mandate, transaction=transaction))

    assert TaxonomyFamily.B9 not in families
    assert TaxonomyFamily.B10 not in families


def test_catalog_price_attack_passes_b6_but_a1_a7_force_block() -> None:
    mandate = make_mandate(max_total_minor=500_000)
    transaction = make_transaction(
        payload=make_payload(
            lines=(
                make_line(
                    effective_unit_price_minor=449_900,
                    quantity=1,
                    line_total_minor=449_900,
                ),
            ),
            declared_order_total_minor=449_900,
        )
    )
    catalog = make_catalog(price_minor=549_900)
    commitments = make_commitments(transaction, catalog)

    tier_b = evaluate_tier_b(mandate=mandate, transaction=transaction)
    tier_a = evaluate_tier_a(
        mandate=mandate,
        transaction=transaction,
        catalog_snapshot=catalog,
        server_time=SERVER_TIME,
        nonce_state=NonceLedgerState(),
        committed_hashes=commitments,
    )
    decision = decide_deterministically(
        replay_seed=7,
        evaluated_at=SERVER_TIME,
        transaction_sha256=transaction_body_sha256(transaction),
        catalog_snapshot_sha256=catalog_snapshot_sha256(catalog),
        tier_a_results=tier_a,
        tier_b_findings=tier_b,
    )

    assert TaxonomyFamily.B6 not in _families(tier_b)
    assert _tier_a_status(tier_a, TaxonomyFamily.A1) is TierACheckStatus.FAIL
    assert _tier_a_status(tier_a, TaxonomyFamily.A7) is TierACheckStatus.FAIL
    assert decision.action is DecisionAction.BLOCK
    assert canonical_json_bytes(decision) == canonical_json_bytes(
        decide_deterministically(
            replay_seed=7,
            evaluated_at=SERVER_TIME,
            transaction_sha256=transaction_body_sha256(transaction),
            catalog_snapshot_sha256=catalog_snapshot_sha256(catalog),
            tier_a_results=tier_a,
            tier_b_findings=tier_b,
        )
    )


def test_unavailable_tier_a_evidence_routes_to_review() -> None:
    mandate = make_mandate()
    transaction = make_transaction()
    tier_a = evaluate_tier_a(
        mandate=mandate,
        transaction=transaction,
        catalog_snapshot=None,
        server_time=SERVER_TIME,
        nonce_state=NonceLedgerState(),
        committed_hashes=CommittedHashes(
            transaction_sha256=transaction_body_sha256(transaction),
            catalog_snapshot_sha256=None,
        ),
    )

    decision = decide_deterministically(
        replay_seed=11,
        evaluated_at=SERVER_TIME,
        transaction_sha256=transaction_body_sha256(transaction),
        catalog_snapshot_sha256=None,
        tier_a_results=tier_a,
        tier_b_findings=evaluate_tier_b(mandate=mandate, transaction=transaction),
    )

    assert decision.action is DecisionAction.REVIEW
    assert decision.findings == ()


def test_known_tier_b_failure_blocks_even_when_tier_a_is_not_evaluable() -> None:
    mandate = make_mandate(max_total_minor=5_000)
    transaction = make_transaction()
    tier_a = evaluate_tier_a(
        mandate=mandate,
        transaction=transaction,
        catalog_snapshot=None,
        server_time=SERVER_TIME,
        nonce_state=NonceLedgerState(),
        committed_hashes=None,
    )

    decision = decide_deterministically(
        replay_seed=12,
        evaluated_at=SERVER_TIME,
        transaction_sha256=transaction_body_sha256(transaction),
        catalog_snapshot_sha256=None,
        tier_a_results=tier_a,
        tier_b_findings=evaluate_tier_b(mandate=mandate, transaction=transaction),
    )

    assert TaxonomyFamily.B6 in _families(decision.findings)
    assert decision.action is DecisionAction.BLOCK


def test_known_tier_a_failure_blocks_with_other_not_evaluable_results() -> None:
    mandate = make_mandate()
    transaction = make_transaction()
    tier_a = evaluate_tier_a(
        mandate=mandate,
        transaction=transaction,
        catalog_snapshot=None,
        server_time=mandate.payload.expires_at,
        nonce_state=None,
        committed_hashes=None,
    )

    decision = decide_deterministically(
        replay_seed=13,
        evaluated_at=mandate.payload.expires_at,
        transaction_sha256=transaction_body_sha256(transaction),
        catalog_snapshot_sha256=None,
        tier_a_results=tier_a,
        tier_b_findings=(),
    )

    assert _tier_a_status(tier_a, TaxonomyFamily.A5) is TierACheckStatus.FAIL
    assert any(result.status is TierACheckStatus.NOT_EVALUABLE for result in tier_a)
    assert decision.action is DecisionAction.BLOCK
