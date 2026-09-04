"""Deterministic parsing of arbitrary buyer intent.

"Buy Sony headphones under ₹5000" is not free-form to the parts that matter for
money. The price ceiling, the currency, the quantity, the recurrence stance, and
the exclusions are extracted by rules, not by a model, because those are exactly
the fields a wrong guess would turn into a wrong payment. What stays free text
is the *search* intent, which is where retrieval and the embedding model earn
their place.

Anything this parser cannot extract is reported as unresolved rather than
defaulted. An unresolved price ceiling means no ceiling was applied, and the
product says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Sequence


PARSER_VERSION = "discovery-intent-parser-v1"
DEFAULT_CURRENCY = "INR"
MAX_INTENT_CHARS = 4000

_MULTIPLIERS: tuple[tuple[str, int], ...] = (
    ("lakh", 100_000),
    ("lakhs", 100_000),
    ("lac", 100_000),
    ("crore", 10_000_000),
    ("k", 1_000),
)

_CURRENCY_SYMBOLS = {
    "₹": "INR",
    "rs": "INR",
    "rs.": "INR",
    "inr": "INR",
    "$": "USD",
    "usd": "USD",
    "€": "EUR",
    "eur": "EUR",
    "£": "GBP",
    "gbp": "GBP",
}

_CEILING_WORDS = (
    "under",
    "below",
    "less than",
    "cheaper than",
    "no more than",
    "not more than",
    "at most",
    "up to",
    "upto",
    "within",
    "max",
    "maximum",
    "budget of",
    "budget",
)

_AMOUNT = r"(?:₹|rs\.?|inr|\$|usd|€|eur|£|gbp)?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*(lakhs?|lac|crore|k)?"
_CEILING_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(word) for word in _CEILING_WORDS) + r")\b[\s:]*" + _AMOUNT,
    re.IGNORECASE,
)
_BARE_CURRENCY_RE = re.compile(
    r"(₹|rs\.?|inr|\$|usd|€|eur|£|gbp)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*(lakhs?|lac|crore|k)?",
    re.IGNORECASE,
)
_QUANTITY_RE = re.compile(
    r"\b(\d{1,3})\s*(?:x|units?|pieces?|pcs?|packs?|sets?|nos?)\b", re.IGNORECASE
)
_QUANTITY_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_QUANTITY_WORD_RE = re.compile(
    r"\b(" + "|".join(_QUANTITY_WORDS) + r")\s+(?:units?|pieces?|pcs?|packs?|sets?)\b",
    re.IGNORECASE,
)

_EXCLUSION_RE = re.compile(
    r"\b(?:no|without|avoid|exclude|excluding|not?\s+any|nothing\s+(?:about|with|related\s+to))\b"
    r"[\s:]+([^.;,\n]{2,80})",
    re.IGNORECASE,
)
_RECURRING_TERMS = (
    "subscription",
    "subscriptions",
    "recurring",
    "auto-renew",
    "auto renew",
    "autorenew",
    "monthly plan",
    "membership",
)
_ONE_TIME_TERMS = ("one-time", "one time", "single payment", "onetime")

_EXCLUSION_STOP_TAIL = re.compile(
    r"\s+(?:and|or|but|please|thanks?)\s*$", re.IGNORECASE
)

#: Phrases that are grammar around the request rather than search intent.
_QUERY_NOISE = (
    "please", "i want to", "i want", "i would like to", "i'd like to", "i need",
    "can you", "could you", "buy me", "buy", "get me", "get", "find me", "find",
    "purchase", "order me", "order", "pick up", "look for", "search for",
    "some", "a good", "the best", "best",
)


def _to_minor(amount: str, multiplier: str | None) -> int | None:
    try:
        value = float(amount.replace(",", ""))
    except ValueError:
        return None
    if multiplier:
        for suffix, factor in _MULTIPLIERS:
            if multiplier.lower() == suffix:
                value *= factor
                break
    if value <= 0 or value > 1_000_000_000:
        return None
    return int(round(value * 100))


@dataclass(frozen=True, slots=True)
class ParsedIntent:
    """Deterministic constraints plus the free text handed to retrieval."""

    raw_text: str
    search_text: str
    max_total_minor: int | None
    currency: str
    quantity: int
    recurring_allowed: bool | None
    exclusions: tuple[str, ...]
    brand_hints: tuple[str, ...]
    unresolved: tuple[str, ...] = field(default_factory=tuple)

    @property
    def max_unit_price_minor(self) -> int | None:
        if self.max_total_minor is None:
            return None
        return self.max_total_minor // max(1, self.quantity)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "parser_version": PARSER_VERSION,
            "raw_text": self.raw_text,
            "search_text": self.search_text,
            "max_total_minor": self.max_total_minor,
            "max_unit_price_minor": self.max_unit_price_minor,
            "currency": self.currency,
            "quantity": self.quantity,
            "recurring_allowed": self.recurring_allowed,
            "exclusions": list(self.exclusions),
            "brand_hints": list(self.brand_hints),
            "unresolved": list(self.unresolved),
        }

    def plain_english(self) -> list[str]:
        """One readable line per extracted constraint, for the product surface."""

        lines: list[str] = []
        if self.max_total_minor is not None:
            lines.append(
                f"Spend no more than {self.currency} "
                f"{self.max_total_minor / 100:,.2f} in total."
            )
        else:
            lines.append("No spending ceiling was stated, so none is enforced.")
        if self.quantity != 1:
            lines.append(f"Buy {self.quantity} units.")
        if self.recurring_allowed is False:
            lines.append("One-time payment only. No subscription or recurring charge.")
        elif self.recurring_allowed is True:
            lines.append("A recurring charge is acceptable.")
        else:
            lines.append("Recurrence was not stated, so it must be evidenced, not assumed.")
        for item in self.exclusions:
            lines.append(f"Nothing involving {item}.")
        for item in self.brand_hints:
            lines.append(f"Prefer the brand {item}.")
        return lines


def _clean_exclusion(text: str) -> str | None:
    item = _EXCLUSION_STOP_TAIL.sub("", text.strip(" -:\"'"))
    item = re.sub(r"^(?:any|the|a|an)\s+", "", item, flags=re.IGNORECASE).strip()
    item = re.sub(r"\s+related\s+products?$", "", item, flags=re.IGNORECASE).strip()
    if len(item) < 2 or len(item) > 80:
        return None
    return item


def _extract_brand_hints(text: str, known_brands: Sequence[str]) -> tuple[str, ...]:
    """Match the intent against brands the catalog actually carries."""

    lowered = f" {text.casefold()} "
    hits: list[str] = []
    for brand in known_brands:
        needle = f" {brand.casefold()} "
        if len(brand) >= 3 and needle in lowered:
            hits.append(brand)
    hits.sort(key=lambda item: (-len(item), item.casefold()))
    return tuple(hits[:3])


def parse_intent(text: str, *, known_brands: Sequence[str] = ()) -> ParsedIntent:
    """Extract the money-critical constraints from arbitrary buyer text."""

    if not isinstance(text, str) or not text.strip():
        raise ValueError("intent must be a non-empty string")
    if len(text) > MAX_INTENT_CHARS:
        raise ValueError(f"intent must be at most {MAX_INTENT_CHARS} characters")
    raw = text.strip()
    lowered = raw.casefold()
    unresolved: list[str] = []

    currency = DEFAULT_CURRENCY
    symbol_match = _BARE_CURRENCY_RE.search(raw)
    if symbol_match:
        currency = _CURRENCY_SYMBOLS.get(symbol_match.group(1).lower(), DEFAULT_CURRENCY)

    ceiling: int | None = None
    ceiling_match = _CEILING_RE.search(raw)
    if ceiling_match:
        ceiling = _to_minor(ceiling_match.group(1), ceiling_match.group(2))
    elif symbol_match:
        # A bare "₹5000" with no comparator is read as a ceiling, and the
        # ambiguity is reported rather than hidden.
        ceiling = _to_minor(symbol_match.group(2), symbol_match.group(3))
        if ceiling is not None:
            unresolved.append(
                "PRICE_CEILING_INFERRED_FROM_BARE_AMOUNT"
            )
    if ceiling is None:
        unresolved.append("PRICE_CEILING_ABSENT")

    quantity = 1
    quantity_match = _QUANTITY_RE.search(raw)
    if quantity_match:
        candidate = int(quantity_match.group(1))
        if 1 <= candidate <= 100:
            quantity = candidate
    else:
        word_match = _QUANTITY_WORD_RE.search(raw)
        if word_match:
            quantity = _QUANTITY_WORDS[word_match.group(1).lower()]

    recurring: bool | None = None
    if any(term in lowered for term in _ONE_TIME_TERMS):
        recurring = False
    exclusions: list[str] = []
    seen: set[str] = set()
    for match in _EXCLUSION_RE.finditer(raw):
        for part in re.split(r"\bor\b|\band\b|/", match.group(1), flags=re.IGNORECASE):
            item = _clean_exclusion(part)
            if item and item.casefold() not in seen:
                exclusions.append(item)
                seen.add(item.casefold())
    if any(
        term in item.casefold() for item in exclusions for term in _RECURRING_TERMS
    ):
        recurring = False
    elif recurring is None and any(term in lowered for term in _RECURRING_TERMS):
        recurring = True
    if recurring is None:
        unresolved.append("RECURRENCE_STANCE_ABSENT")

    search_text = _search_text(raw)
    if not search_text:
        unresolved.append("SEARCH_TERMS_ABSENT")

    return ParsedIntent(
        raw_text=raw,
        search_text=search_text,
        max_total_minor=ceiling,
        currency=currency,
        quantity=quantity,
        recurring_allowed=recurring,
        exclusions=tuple(exclusions),
        brand_hints=_extract_brand_hints(raw, known_brands),
        unresolved=tuple(unresolved),
    )


def _search_text(raw: str) -> str:
    """Strip the constraint clauses so retrieval sees the product request."""

    text = _CEILING_RE.sub(" ", raw)
    text = _BARE_CURRENCY_RE.sub(" ", text)
    text = _EXCLUSION_RE.sub(" ", text)
    for term in _ONE_TIME_TERMS:
        text = re.sub(re.escape(term), " ", text, flags=re.IGNORECASE)
    lowered = text.casefold()
    for phrase in _QUERY_NOISE:
        lowered = re.sub(rf"\b{re.escape(phrase)}\b", " ", lowered)
    return re.sub(r"[^\w\s'’-]+", " ", re.sub(r"\s+", " ", lowered)).strip()
