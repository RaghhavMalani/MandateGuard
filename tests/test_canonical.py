from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256

import pytest

from mandateguard.core.canonical import (
    CanonicalizationError,
    FloatNotAllowedError,
    canonical_json_bytes,
)
from mandateguard.core.hashing import sha256_canonical, transaction_payload_sha256
from mandateguard.models.transaction import TransactionLine
from tests.factories import make_payload


def test_canonical_json_is_sorted_compact_and_utf8() -> None:
    value = {"z": "₹", "a": [1, True, None], "middle": {"b": 2, "a": 1}}

    encoded = canonical_json_bytes(value)

    assert encoded == '{"a":[1,true,null],"middle":{"a":1,"b":2},"z":"₹"}'.encode()
    assert b" " not in encoded
    assert b"\n" not in encoded


@pytest.mark.parametrize(
    "value",
    [
        1.0,
        {"nested": 1.0},
        {"nested": [0, {"deeper": 2.5}]},
        (1, [2, 3.0]),
    ],
)
def test_canonical_json_rejects_floats_recursively(value: object) -> None:
    with pytest.raises(FloatNotAllowedError):
        canonical_json_bytes(value)


def test_canonical_json_rejects_non_string_mapping_keys() -> None:
    with pytest.raises(CanonicalizationError):
        canonical_json_bytes({1: "not allowed"})


def test_equivalent_aware_datetimes_have_identical_canonical_form() -> None:
    utc_value = datetime(2026, 8, 23, 8, 30, 1, 123, tzinfo=timezone.utc)
    offset_value = utc_value.astimezone(timezone(timedelta(hours=5, minutes=30)))

    assert canonical_json_bytes(utc_value) == canonical_json_bytes(offset_value)
    assert canonical_json_bytes(utc_value) == b'"2026-08-23T08:30:01.000123Z"'


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(CanonicalizationError):
        canonical_json_bytes(datetime(2026, 8, 23, 8, 30))


def test_sha256_is_over_exact_canonical_bytes() -> None:
    value = {"b": 2, "a": 1}

    assert sha256_canonical(value) == sha256(b'{"a":1,"b":2}').hexdigest()


def test_transaction_hash_is_stable_and_contains_no_declared_hash_cycle() -> None:
    payload = make_payload()

    assert transaction_payload_sha256(payload) == transaction_payload_sha256(payload)
    assert len(transaction_payload_sha256(payload)) == 64


def test_money_model_rejects_float_minor_units() -> None:
    with pytest.raises(ValueError):
        TransactionLine(
            sku="sku-1",
            unit_price_minor=10.5,  # type: ignore[arg-type]
            quantity=1,
            line_total_minor=10,
            recurring=False,
        )


@dataclass(frozen=True)
class NestedDataclass:
    payload: object


def test_float_rejection_walks_dataclasses() -> None:
    with pytest.raises(FloatNotAllowedError):
        canonical_json_bytes(NestedDataclass(payload={"values": [1, 1.25]}))
