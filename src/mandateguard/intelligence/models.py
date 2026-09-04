"""Strict value objects for agentic commerce discovery and tracing."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Mapping


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{name} must be a bounded identifier")
    return value


def _bounded_text(
    value: object, name: str, maximum: int, *, nullable: bool = False
) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        suffix = " or null" if nullable else ""
        raise ValueError(
            f"{name} must be a non-empty string of at most {maximum} characters{suffix}"
        )
    return value.strip()


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _string_tuple(
    value: object,
    name: str,
    *,
    maximum_items: int,
    identifiers: bool = False,
    nullable: bool = False,
) -> tuple[str, ...] | None:
    if nullable and value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) > maximum_items:
        suffix = " or null" if nullable else ""
        raise ValueError(
            f"{name} must contain at most {maximum_items} strings{suffix}"
        )
    parsed: list[str] = []
    for item in value:
        if identifiers:
            parsed.append(_identifier(item, name))
        else:
            parsed.append(str(_bounded_text(item, name, 256)))
    if len(parsed) != len(set(parsed)):
        raise ValueError(f"{name} must contain unique values")
    return tuple(parsed)


def _exact_mapping(
    value: object, fields: frozenset[str], name: str
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or frozenset(value) != fields:
        raise ValueError(f"{name} must contain exactly the declared fields")
    return value


#: The stores a selected identity may be resolved from. Both are server-owned,
#: schema-valid, versioned catalogues bound to exact merchant/SKU pairs; they
#: are listed separately so a trace says which world a selection came from. A
#: crawled marketplace source is not on this list and cannot be added to it by
#: anything a request carries.
REGISTERED_SELECTION_SOURCES = frozenset({"mandateguard", "mandateguard-sandbox"})


@dataclass(frozen=True, slots=True)
class SelectedProductIdentity:
    """Server-resolved identity for a clicked registered catalog listing.

    This value never comes from buyer prose. Once present, every proposal must
    match its exact merchant and SKU or authorization stops.
    """

    merchant_id: str
    sku: str
    catalog_product_id: str
    source: str
    source_product_id: str

    def __post_init__(self) -> None:
        _identifier(self.merchant_id, "merchant_id")
        _identifier(self.sku, "sku")
        _identifier(self.catalog_product_id, "catalog_product_id")
        _identifier(self.source, "source")
        _bounded_text(self.source_product_id, "source_product_id", 512)
        if self.source not in REGISTERED_SELECTION_SOURCES:
            raise ValueError("selected product must come from the registered source")
        if self.source_product_id != f"{self.merchant_id}/{self.sku}":
            raise ValueError("selected product source identity does not match merchant and SKU")

    @classmethod
    def from_mapping(cls, value: object) -> SelectedProductIdentity:
        data = _exact_mapping(
            value,
            frozenset(
                {
                    "merchant_id",
                    "sku",
                    "catalog_product_id",
                    "source",
                    "source_product_id",
                }
            ),
            "selected_product_identity",
        )
        return cls(
            merchant_id=data["merchant_id"],
            sku=data["sku"],
            catalog_product_id=data["catalog_product_id"],
            source=data["source"],
            source_product_id=data["source_product_id"],
        )

    def to_mapping(self) -> dict[str, str]:
        return {
            "merchant_id": self.merchant_id,
            "sku": self.sku,
            "catalog_product_id": self.catalog_product_id,
            "source": self.source,
            "source_product_id": self.source_product_id,
        }


@dataclass(frozen=True, slots=True)
class InterpretedPurchaseIntent:
    """Typed interpretation used to construct the authoritative mandate."""

    max_total_minor: int
    quantity: int
    currency: str
    purpose: str | None
    recurring_allowed: bool
    exclusions: tuple[str, ...]
    merchant_allowlist: tuple[str, ...] | None = None
    sku_allowlist: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        _nonnegative_int(self.max_total_minor, "max_total_minor")
        _positive_int(self.quantity, "quantity")
        if not isinstance(self.currency, str) or not _CURRENCY_RE.fullmatch(
            self.currency
        ):
            raise ValueError("currency must be a three-letter uppercase code")
        _bounded_text(self.purpose, "purpose", 500, nullable=True)
        if not isinstance(self.recurring_allowed, bool):
            raise ValueError("recurring_allowed must be boolean")
        exclusions = _string_tuple(
            self.exclusions, "exclusions", maximum_items=16
        )
        merchant_allowlist = _string_tuple(
            self.merchant_allowlist,
            "merchant_allowlist",
            maximum_items=32,
            identifiers=True,
            nullable=True,
        )
        sku_allowlist = _string_tuple(
            self.sku_allowlist,
            "sku_allowlist",
            maximum_items=64,
            identifiers=True,
            nullable=True,
        )
        object.__setattr__(self, "exclusions", exclusions)
        object.__setattr__(self, "merchant_allowlist", merchant_allowlist)
        object.__setattr__(self, "sku_allowlist", sku_allowlist)

    @classmethod
    def from_mapping(cls, value: object) -> InterpretedPurchaseIntent:
        data = _exact_mapping(
            value,
            frozenset(
                {
                    "max_total_minor",
                    "quantity",
                    "currency",
                    "purpose",
                    "recurring_allowed",
                    "exclusions",
                    "merchant_allowlist",
                    "sku_allowlist",
                }
            ),
            "interpreted_intent",
        )
        return cls(
            max_total_minor=data["max_total_minor"],
            quantity=data["quantity"],
            currency=data["currency"],
            purpose=data["purpose"],
            recurring_allowed=data["recurring_allowed"],
            exclusions=data["exclusions"],
            merchant_allowlist=data["merchant_allowlist"],
            sku_allowlist=data["sku_allowlist"],
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "max_total_minor": self.max_total_minor,
            "quantity": self.quantity,
            "currency": self.currency,
            "purpose": self.purpose,
            "recurring_allowed": self.recurring_allowed,
            "exclusions": list(self.exclusions),
            "merchant_allowlist": (
                list(self.merchant_allowlist)
                if self.merchant_allowlist is not None
                else None
            ),
            "sku_allowlist": (
                list(self.sku_allowlist) if self.sku_allowlist is not None else None
            ),
        }


@dataclass(frozen=True, slots=True)
class PurchaseProposal:
    """The buyer's bounded proposal. It contains no execution credentials."""

    merchant_id: str
    sku: str
    quantity: int
    declared_total_minor: int
    currency: str
    reason: str
    selected_evidence_ids: tuple[str, ...]
    user_intent_summary: str | None = None

    def __post_init__(self) -> None:
        _identifier(self.merchant_id, "merchant_id")
        _identifier(self.sku, "sku")
        _positive_int(self.quantity, "quantity")
        _nonnegative_int(self.declared_total_minor, "declared_total_minor")
        if not isinstance(self.currency, str) or not _CURRENCY_RE.fullmatch(
            self.currency
        ):
            raise ValueError("currency must be a three-letter uppercase code")
        _bounded_text(self.reason, "reason", 1000)
        evidence_ids = _string_tuple(
            self.selected_evidence_ids,
            "selected_evidence_ids",
            maximum_items=32,
            identifiers=True,
        )
        _bounded_text(
            self.user_intent_summary,
            "user_intent_summary",
            1000,
            nullable=True,
        )
        object.__setattr__(self, "selected_evidence_ids", evidence_ids)

    @classmethod
    def from_mapping(cls, value: object) -> PurchaseProposal:
        data = _exact_mapping(
            value,
            frozenset(
                {
                    "merchant_id",
                    "sku",
                    "quantity",
                    "declared_total_minor",
                    "currency",
                    "reason",
                    "selected_evidence_ids",
                    "user_intent_summary",
                }
            ),
            "purchase_proposal",
        )
        return cls(
            merchant_id=data["merchant_id"],
            sku=data["sku"],
            quantity=data["quantity"],
            declared_total_minor=data["declared_total_minor"],
            currency=data["currency"],
            reason=data["reason"],
            selected_evidence_ids=data["selected_evidence_ids"],
            user_intent_summary=data["user_intent_summary"],
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "merchant_id": self.merchant_id,
            "sku": self.sku,
            "quantity": self.quantity,
            "declared_total_minor": self.declared_total_minor,
            "currency": self.currency,
            "reason": self.reason,
            "selected_evidence_ids": list(self.selected_evidence_ids),
            "user_intent_summary": self.user_intent_summary,
        }


@dataclass(frozen=True, slots=True)
class BuyerOutput:
    proposal: PurchaseProposal
    interpreted_intent: InterpretedPurchaseIntent
    model_id: str
    input_tokens: int | None = None
    output_tokens: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.proposal, PurchaseProposal):
            raise TypeError("proposal must be PurchaseProposal")
        if not isinstance(self.interpreted_intent, InterpretedPurchaseIntent):
            raise TypeError("interpreted_intent must be InterpretedPurchaseIntent")
        _bounded_text(self.model_id, "model_id", 256)
        for value, name in (
            (self.input_tokens, "input_tokens"),
            (self.output_tokens, "output_tokens"),
        ):
            if value is not None:
                _nonnegative_int(value, name)


@dataclass(frozen=True, slots=True)
class CommerceProduct:
    merchant_id: str
    sku: str
    name: str
    description: str
    effective_unit_price_minor: int
    currency: str
    recurring: bool
    tags: tuple[str, ...]
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _identifier(self.merchant_id, "merchant_id")
        _identifier(self.sku, "sku")
        _bounded_text(self.name, "name", 256)
        _bounded_text(self.description, "description", 2000)
        _nonnegative_int(
            self.effective_unit_price_minor, "effective_unit_price_minor"
        )
        if not isinstance(self.currency, str) or not _CURRENCY_RE.fullmatch(
            self.currency
        ):
            raise ValueError("currency must be a three-letter uppercase code")
        if not isinstance(self.recurring, bool):
            raise ValueError("recurring must be boolean")
        tags = _string_tuple(self.tags, "tags", maximum_items=32)
        evidence_ids = _string_tuple(
            self.evidence_ids,
            "evidence_ids",
            maximum_items=32,
            identifiers=True,
        )
        object.__setattr__(self, "tags", tags)
        object.__setattr__(self, "evidence_ids", evidence_ids)

    def discovery_mapping(self) -> dict[str, Any]:
        return {
            "merchant_id": self.merchant_id,
            "sku": self.sku,
            "name": self.name,
            "description": self.description,
            "effective_unit_price_minor": self.effective_unit_price_minor,
            "currency": self.currency,
            "recurring": self.recurring,
            "tags": list(self.tags),
            "evidence_ids": list(self.evidence_ids),
        }


class RetrievalSource(str, Enum):
    MANDATE_CLAUSE = "mandate_clause"
    MERCHANT_EVIDENCE = "merchant_evidence"
    DECISION_MEMORY = "decision_memory"


@dataclass(frozen=True, slots=True)
class RetrievalDocument:
    document_id: str
    source_type: RetrievalSource
    text: str
    merchant_id: str | None = None
    sku: str | None = None
    evidence_id: str | None = None

    def __post_init__(self) -> None:
        _identifier(self.document_id, "document_id")
        if not isinstance(self.source_type, RetrievalSource):
            raise TypeError("source_type must be RetrievalSource")
        _bounded_text(self.text, "text", 20_000)
        for value, name in (
            (self.merchant_id, "merchant_id"),
            (self.sku, "sku"),
            (self.evidence_id, "evidence_id"),
        ):
            if value is not None:
                _identifier(value, name)
        if self.source_type is RetrievalSource.MERCHANT_EVIDENCE:
            if self.merchant_id is None or self.evidence_id is None:
                raise ValueError(
                    "merchant evidence documents require merchant_id and evidence_id"
                )


@dataclass(frozen=True, slots=True)
class RetrievalScore:
    document_id: str
    source_type: RetrievalSource
    lexical_score: float
    semantic_score: float
    hybrid_score: float

    def __post_init__(self) -> None:
        _identifier(self.document_id, "document_id")
        if not isinstance(self.source_type, RetrievalSource):
            raise TypeError("source_type must be RetrievalSource")
        for value, name in (
            (self.lexical_score, "lexical_score"),
            (self.semantic_score, "semantic_score"),
            (self.hybrid_score, "hybrid_score"),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0.0 <= float(value) <= 1.0
            ):
                raise ValueError(f"{name} must be within [0, 1]")


@dataclass(frozen=True, slots=True)
class RankedDocument:
    document: RetrievalDocument
    score: RetrievalScore

    def __post_init__(self) -> None:
        if not isinstance(self.document, RetrievalDocument):
            raise TypeError("document must be RetrievalDocument")
        if not isinstance(self.score, RetrievalScore):
            raise TypeError("score must be RetrievalScore")
        if self.document.document_id != self.score.document_id:
            raise ValueError("document and score IDs must match")


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    query: str
    query_sha256: str
    ranked_documents: tuple[RankedDocument, ...]
    alpha: float
    top_k: int
    embedding_latency_ms: float
    input_tokens: int | None = None

    def __post_init__(self) -> None:
        _bounded_text(self.query, "query", 50_000)
        if not isinstance(self.query_sha256, str) or not _SHA256_RE.fullmatch(
            self.query_sha256
        ):
            raise ValueError("query_sha256 must be a lowercase SHA-256 digest")
        if not isinstance(self.ranked_documents, tuple) or not all(
            isinstance(item, RankedDocument) for item in self.ranked_documents
        ):
            raise TypeError("ranked_documents must be a tuple of RankedDocument")
        if (
            isinstance(self.alpha, bool)
            or not isinstance(self.alpha, (int, float))
            or not 0.0 <= float(self.alpha) <= 1.0
        ):
            raise ValueError("alpha must be within [0, 1]")
        _positive_int(self.top_k, "top_k")
        if self.embedding_latency_ms < 0:
            raise ValueError("embedding_latency_ms must be non-negative")
        if self.input_tokens is not None:
            _nonnegative_int(self.input_tokens, "input_tokens")


class CacheStatus(str, Enum):
    HIT = "HIT"
    MISS = "MISS"


class ExecutionStatus(str, Enum):
    NOT_REQUESTED = "not_requested"
    AUTHORIZED_NOT_EXECUTED = "authorized_not_executed"
    EXECUTED = "executed"
    NOT_AUTHORIZED = "not_authorized"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class AgenticCheckoutTrace:
    user_intent: str
    buyer: Mapping[str, Any]
    retrieval: Mapping[str, Any]
    authorization: Mapping[str, Any]
    cache: Mapping[str, Any]
    decision: str
    execution: Mapping[str, Any]
    timings: Mapping[str, float]
    models: Mapping[str, str]
    usage: Mapping[str, int | None]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "user_intent": self.user_intent,
            "buyer": dict(self.buyer),
            "retrieval": dict(self.retrieval),
            "authorization": dict(self.authorization),
            "cache": dict(self.cache),
            "decision": self.decision,
            "execution": dict(self.execution),
            "timings": dict(self.timings),
            "models": dict(self.models),
            "usage": dict(self.usage),
        }
