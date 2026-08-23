"""Narrow HMAC-SHA256 signing and verification for D6 capabilities."""

from __future__ import annotations

from enum import Enum
import hashlib
import hmac
from types import MappingProxyType
from typing import Mapping, Protocol

from mandateguard.core.canonical import canonical_json_bytes
from mandateguard.execution.models import (
    ExecutionAuthorizationPayload,
    SignedExecutionAuthorization,
)


class SignatureVerification(str, Enum):
    VALID = "VALID"
    INVALID = "INVALID"
    UNKNOWN_KEY = "UNKNOWN_KEY"


class ExecutionSigner(Protocol):
    def sign(
        self, payload: ExecutionAuthorizationPayload
    ) -> SignedExecutionAuthorization: ...


class ExecutionVerifier(Protocol):
    def verify(self, authorization: SignedExecutionAuthorization) -> SignatureVerification: ...


def _validate_key_material(key: object) -> bytes:
    if not isinstance(key, bytes) or len(key) < 32:
        raise ValueError("HMAC key material must contain at least 32 bytes")
    return key


class HMACSHA256Signer:
    __slots__ = ("_key", "_key_id")

    def __init__(self, *, key_id: str, key: bytes) -> None:
        if not isinstance(key_id, str) or not key_id or len(key_id) > 256:
            raise ValueError("key_id must be a non-empty string of at most 256 characters")
        self._key_id = key_id
        self._key = _validate_key_material(key)

    def sign(
        self, payload: ExecutionAuthorizationPayload
    ) -> SignedExecutionAuthorization:
        if not isinstance(payload, ExecutionAuthorizationPayload):
            raise TypeError("payload must be ExecutionAuthorizationPayload")
        signature = hmac.new(
            self._key, canonical_json_bytes(payload), hashlib.sha256
        ).digest()
        return SignedExecutionAuthorization(
            payload=payload,
            key_id=self._key_id,
            algorithm="HMAC-SHA256",
            signature=signature,
        )


class HMACSHA256Verifier:
    __slots__ = ("_keys",)

    def __init__(self, trusted_keys: Mapping[str, bytes]) -> None:
        if not isinstance(trusted_keys, Mapping) or not trusted_keys:
            raise ValueError("trusted_keys must be a non-empty key mapping")
        checked: dict[str, bytes] = {}
        for key_id, key in trusted_keys.items():
            if not isinstance(key_id, str) or not key_id or len(key_id) > 256:
                raise ValueError("trusted signing key IDs must be bounded strings")
            checked[key_id] = _validate_key_material(key)
        self._keys = MappingProxyType(checked)

    def verify(self, authorization: SignedExecutionAuthorization) -> SignatureVerification:
        if not isinstance(authorization, SignedExecutionAuthorization):
            raise TypeError("authorization must be SignedExecutionAuthorization")
        key = self._keys.get(authorization.key_id)
        if key is None:
            return SignatureVerification.UNKNOWN_KEY
        expected = hmac.new(
            key,
            canonical_json_bytes(authorization.payload),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(expected, authorization.signature):
            return SignatureVerification.INVALID
        return SignatureVerification.VALID
