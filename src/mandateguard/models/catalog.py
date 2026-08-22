"""Independent merchant catalog snapshot models."""

from __future__ import annotations

from dataclasses import dataclass
import re


_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


def _nonempty(value: object, name: str, maximum: int = 256) -> None:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{name} must be a non-empty string of at most {maximum} characters")


@dataclass(frozen=True, slots=True)
class CatalogItem:
    sku: str
    merchant_id: str
    price_minor: int
    recurring: bool

    def __post_init__(self) -> None:
        _nonempty(self.sku, "sku", 128)
        _nonempty(self.merchant_id, "merchant_id", 128)
        if isinstance(self.price_minor, bool) or not isinstance(self.price_minor, int) or self.price_minor < 0:
            raise ValueError("price_minor must be a non-negative integer in minor units")
        if not isinstance(self.recurring, bool):
            raise ValueError("recurring must be a boolean")


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    snapshot_id: str
    merchant_id: str
    currency: str
    items: tuple[CatalogItem, ...]

    def __post_init__(self) -> None:
        _nonempty(self.snapshot_id, "snapshot_id")
        _nonempty(self.merchant_id, "merchant_id", 128)
        if not isinstance(self.currency, str) or not _CURRENCY_RE.fullmatch(self.currency):
            raise ValueError("currency must be a three-letter uppercase code")
        if not isinstance(self.items, tuple):
            raise ValueError("items must be a tuple")
        if not all(isinstance(item, CatalogItem) for item in self.items):
            raise ValueError("items contains an invalid catalog item")
        skus = [item.sku for item in self.items]
        if len(skus) != len(set(skus)):
            raise ValueError("catalog SKUs must be unique within a snapshot")
        object.__setattr__(self, "items", tuple(sorted(self.items, key=lambda item: item.sku)))

    def item_by_sku(self, sku: str) -> CatalogItem | None:
        return next((item for item in self.items if item.sku == sku), None)
