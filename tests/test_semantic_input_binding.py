from __future__ import annotations

from dataclasses import replace

import pytest

from mandateguard.core.hashing import (
    catalog_snapshot_sha256,
    mandate_payload_sha256,
    transaction_body_sha256,
)
from mandateguard.models.catalog import CatalogItem
from mandateguard.models.mandate import SemanticConstraint
from mandateguard.semantic.models import SemanticRequest, semantic_input_sha256
from mandateguard.semantic.verifier import build_semantic_request
from tests.factories import make_catalog, make_line, make_payload, make_transaction
from tests.semantic_factories import make_semantic_evidence, make_semantic_mandate


def _request() -> SemanticRequest:
    return build_semantic_request(
        mandate=make_semantic_mandate(),
        transaction=make_transaction(),
        catalog_snapshot=make_catalog(),
        semantic_evidence=make_semantic_evidence(),
        model_id="semantic-test-model-v1",
    )


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(
            lambda request: replace(
                request,
                constraints=(
                    replace(request.constraints[0], text="A materially different purpose."),
                    request.constraints[1],
                ),
            ),
            id="constraint-text",
        ),
        pytest.param(
            lambda request: replace(
                request,
                constraints=(
                    replace(request.constraints[0], constraint_id="purpose-2"),
                    request.constraints[1],
                ),
            ),
            id="constraint-id",
        ),
        pytest.param(
            lambda request: replace(request, transaction_body_sha256="1" * 64),
            id="transaction-body",
        ),
        pytest.param(
            lambda request: replace(request, mandate_payload_sha256="2" * 64),
            id="mandate",
        ),
        pytest.param(
            lambda request: replace(request, catalog_snapshot_sha256="3" * 64),
            id="catalog",
        ),
        pytest.param(
            lambda request: replace(request, semantic_evidence_sha256="4" * 64),
            id="semantic-evidence",
        ),
        pytest.param(
            lambda request: replace(request, model_id="semantic-test-model-v2"),
            id="model-id",
        ),
        pytest.param(
            lambda request: replace(request, prompt_version="1.1"),
            id="prompt-version",
        ),
    ],
)
def test_material_semantic_inputs_change_the_input_hash(mutate) -> None:
    request = _request()
    assert semantic_input_sha256(mutate(request)) != semantic_input_sha256(request)


def test_equivalent_constraint_and_evidence_ordering_is_canonical() -> None:
    request = _request()
    reordered = replace(
        request,
        constraints=tuple(reversed(request.constraints)),
        selected_evidence=tuple(reversed(request.selected_evidence)),
    )

    assert reordered == request
    assert semantic_input_sha256(reordered) == semantic_input_sha256(request)


def test_independently_reconstructed_requests_have_identical_hashes() -> None:
    assert _request() is not _request()
    assert semantic_input_sha256(_request()) == semantic_input_sha256(_request())


def test_request_hashes_authoritative_payloads_not_mandate_metadata() -> None:
    mandate = make_semantic_mandate()
    changed_metadata = replace(mandate, metadata={"note": "non-authoritative"})
    transaction = make_transaction()
    catalog = make_catalog()
    evidence = make_semantic_evidence()
    first = build_semantic_request(
        mandate=mandate,
        transaction=transaction,
        catalog_snapshot=catalog,
        semantic_evidence=evidence,
        model_id="semantic-test-model-v1",
    )
    second = build_semantic_request(
        mandate=changed_metadata,
        transaction=transaction,
        catalog_snapshot=catalog,
        semantic_evidence=evidence,
        model_id="semantic-test-model-v1",
    )

    assert first == second
    assert mandate_payload_sha256(mandate) == mandate_payload_sha256(changed_metadata)


def test_real_transaction_mandate_and_catalog_changes_rebind_the_request() -> None:
    mandate = make_semantic_mandate()
    changed_mandate = replace(
        mandate,
        payload=replace(mandate.payload, subject_ref="subject-2"),
    )
    transaction = make_transaction()
    changed_transaction = make_transaction(
        payload=replace(transaction.payload, transaction_id="transaction-2")
    )
    catalog = make_catalog()
    changed_catalog = replace(catalog, snapshot_id="catalog-snapshot-2")
    evidence = make_semantic_evidence()

    base = build_semantic_request(
        mandate=mandate,
        transaction=transaction,
        catalog_snapshot=catalog,
        semantic_evidence=evidence,
        model_id="semantic-test-model-v1",
    )
    variants = (
        build_semantic_request(
            mandate=changed_mandate,
            transaction=transaction,
            catalog_snapshot=catalog,
            semantic_evidence=evidence,
            model_id="semantic-test-model-v1",
        ),
        build_semantic_request(
            mandate=mandate,
            transaction=changed_transaction,
            catalog_snapshot=catalog,
            semantic_evidence=evidence,
            model_id="semantic-test-model-v1",
        ),
        build_semantic_request(
            mandate=mandate,
            transaction=transaction,
            catalog_snapshot=changed_catalog,
            semantic_evidence=evidence,
            model_id="semantic-test-model-v1",
        ),
    )

    assert len({semantic_input_sha256(base), *(semantic_input_sha256(item) for item in variants)}) == 4
    assert base.transaction_body_sha256 == transaction_body_sha256(transaction)
    assert base.catalog_snapshot_sha256 == catalog_snapshot_sha256(catalog)
