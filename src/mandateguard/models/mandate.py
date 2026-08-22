"""Typed representation of the frozen V1 mandate contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re
from types import MappingProxyType
from typing import Mapping, TypeAlias
from uuid import UUID


MetadataValue: TypeAlias = str | int | bool | None

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_CONSTRAINT_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_SEMANTIC_KINDS = frozenset(
    {
        "exclusion",
        "purpose",
        "compatibility",
        "fulfillment",
        "obligation",
        "category_intent",
        "other",
    }
)


def _require_nonempty_string(value: object, name: str, maximum: int) -> None:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{name} must be a non-empty string of at most {maximum} characters")


def _require_nonnegative_int(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _require_positive_int(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _require_aware_datetime(value: object, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")


@dataclass(frozen=True, slots=True)
class HardConstraints:
    """Structured mandate limits reducible to deterministic comparisons."""

    max_total_minor: int
    max_quantity: int
    recurring_allowed: bool
    merchant_allowlist: tuple[str, ...] | None = None
    sku_allowlist: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        _require_nonnegative_int(self.max_total_minor, "max_total_minor")
        _require_positive_int(self.max_quantity, "max_quantity")
        if not isinstance(self.recurring_allowed, bool):
            raise ValueError("recurring_allowed must be a boolean")
        self._validate_allowlist(self.merchant_allowlist, "merchant_allowlist")
        self._validate_allowlist(self.sku_allowlist, "sku_allowlist")

    @staticmethod
    def _validate_allowlist(values: tuple[str, ...] | None, name: str) -> None:
        if values is None:
            return
        if not isinstance(values, tuple):
            raise ValueError(f"{name} must be a tuple or None")
        if len(values) != len(set(values)):
            raise ValueError(f"{name} must contain unique values")
        for value in values:
            _require_nonempty_string(value, name, 128)


@dataclass(frozen=True, slots=True)
class SemanticConstraint:
    """A semantic obligation carried through D2 but not evaluated by it."""

    constraint_id: str
    kind: str
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.constraint_id, str) or not _CONSTRAINT_ID_RE.fullmatch(
            self.constraint_id
        ):
            raise ValueError("constraint_id does not match the mandate schema")
        if self.kind not in _SEMANTIC_KINDS:
            raise ValueError("kind is not registered by the mandate schema")
        _require_nonempty_string(self.text, "text", 1000)


@dataclass(frozen=True, slots=True)
class MandateConstraints:
    hard: HardConstraints
    semantic: tuple[SemanticConstraint, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.hard, HardConstraints):
            raise ValueError("hard must be HardConstraints")
        if not isinstance(self.semantic, tuple) or len(self.semantic) > 32:
            raise ValueError("semantic must be a tuple with at most 32 constraints")
        if not all(isinstance(item, SemanticConstraint) for item in self.semantic):
            raise ValueError("semantic contains an invalid constraint")


@dataclass(frozen=True, slots=True)
class MandatePayload:
    """The authoritative, hashable mandate payload."""

    mandate_id: str
    nonce: str
    issued_at: datetime
    expires_at: datetime
    subject_ref: str
    currency: str
    constraints: MandateConstraints
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        if self.schema_version != "1.0":
            raise ValueError("schema_version must be 1.0")
        try:
            UUID(self.mandate_id)
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("mandate_id must be a UUID string") from error
        if not isinstance(self.nonce, str) or not _NONCE_RE.fullmatch(self.nonce):
            raise ValueError("nonce does not match the V1 mandate schema")
        _require_aware_datetime(self.issued_at, "issued_at")
        _require_aware_datetime(self.expires_at, "expires_at")
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")
        _require_nonempty_string(self.subject_ref, "subject_ref", 256)
        if not isinstance(self.currency, str) or not _CURRENCY_RE.fullmatch(self.currency):
            raise ValueError("currency must be a three-letter uppercase code")
        if not isinstance(self.constraints, MandateConstraints):
            raise ValueError("constraints must be MandateConstraints")


@dataclass(frozen=True, slots=True)
class IssuerAttestation:
    assurance: str
    issuer_id: str
    alg: str | None = None
    key_id: str | None = None
    signature_b64url: str | None = None
    attestation_ref: str | None = None

    def __post_init__(self) -> None:
        if self.assurance not in {"DECLARED_ONLY", "SIGNED_UPSTREAM"}:
            raise ValueError("assurance is not registered by the mandate schema")
        _require_nonempty_string(self.issuer_id, "issuer_id", 256)
        if self.assurance == "SIGNED_UPSTREAM":
            if self.alg != "Ed25519":
                raise ValueError("SIGNED_UPSTREAM requires Ed25519")
            _require_nonempty_string(self.key_id, "key_id", 256)
            _require_nonempty_string(self.signature_b64url, "signature_b64url", 512)
            if len(self.signature_b64url) < 16:
                raise ValueError("signature_b64url must contain at least 16 characters")
        elif self.alg not in {None, "Ed25519"}:
            raise ValueError("alg must be Ed25519 or None")
        for value, name, maximum in (
            (self.key_id, "key_id", 256),
            (self.signature_b64url, "signature_b64url", 512),
            (self.attestation_ref, "attestation_ref", 512),
        ):
            if value is not None and (not isinstance(value, str) or len(value) > maximum):
                raise ValueError(f"{name} must be null or a string of at most {maximum} characters")


@dataclass(frozen=True, slots=True)
class Mandate:
    """Mandate envelope; metadata is retained but never used for authorization."""

    payload: MandatePayload
    issuer_attestation: IssuerAttestation
    metadata: Mapping[str, MetadataValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.payload, MandatePayload):
            raise ValueError("payload must be MandatePayload")
        if not isinstance(self.issuer_attestation, IssuerAttestation):
            raise ValueError("issuer_attestation must be IssuerAttestation")
        metadata = dict(self.metadata)
        for key, value in metadata.items():
            if not isinstance(key, str):
                raise ValueError("metadata keys must be strings")
            if isinstance(value, float) or not isinstance(value, (str, int, bool, type(None))):
                raise ValueError("metadata values must be string, integer, boolean, or null")
        object.__setattr__(self, "metadata", MappingProxyType(metadata))
