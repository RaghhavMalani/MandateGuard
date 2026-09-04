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
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Sequence


PARSER_VERSION = "discovery-intent-parser-v2"
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

_CURRENCY_TOKEN = r"(?:₹|rs\.?|inr|\$|usd|€|eur|£|gbp)"
_NUMBER_TOKEN = r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]{1,2})?"
_MULTIPLIER_TOKEN = r"(?:lakhs?|lac|crore|k)"
_MONEY_TOKEN = (
    rf"(?:(?:{_CURRENCY_TOKEN})\s*)?{_NUMBER_TOKEN}"
    rf"(?:\s*{_MULTIPLIER_TOKEN})?(?:\s*(?:{_CURRENCY_TOKEN}))?"
)
_CEILING_WORD_PATTERN = "|".join(re.escape(word) for word in _CEILING_WORDS)
_RANGE_RE = re.compile(
    rf"\bfrom\b[\s:]+(?P<low>{_MONEY_TOKEN})\s+\bto\b\s+(?P<high>{_MONEY_TOKEN})",
    re.IGNORECASE,
)
_CEILING_RE = re.compile(
    rf"\b(?:{_CEILING_WORD_PATTERN})\b[\s:]*(?P<amount>{_MONEY_TOKEN})",
    re.IGNORECASE,
)
_POSTFIX_CEILING_RE = re.compile(
    rf"(?P<amount>{_MONEY_TOKEN})\s*\b(?:max(?:imum)?|ceiling|limit)\b",
    re.IGNORECASE,
)
_CURRENCY_AMOUNT_RE = re.compile(
    rf"(?:{_CURRENCY_TOKEN})\s*{_NUMBER_TOKEN}(?:\s*{_MULTIPLIER_TOKEN})?"
    rf"|{_NUMBER_TOKEN}(?:\s*{_MULTIPLIER_TOKEN})?\s*(?:{_CURRENCY_TOKEN})",
    re.IGNORECASE,
)
_CURRENCY_RE = re.compile(_CURRENCY_TOKEN, re.IGNORECASE)
_NUMBER_RE = re.compile(_NUMBER_TOKEN, re.IGNORECASE)
# No leading \b: in "25k" there is no boundary between the digit and the suffix,
# and dropping the multiplier silently turns a 25,000 ceiling into a 25 one.
_MULTIPLIER_RE = re.compile(rf"({_MULTIPLIER_TOKEN})\b", re.IGNORECASE)
_AMBIGUOUS_DOTTED_RE = re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{3})+(?![\d.])")
_EXPONENT_RE = re.compile(r"(?<!\w)\d+(?:\.\d+)?[eE][+-]?\d+(?!\w)")
_NEGATIVE_MONEY_RE = re.compile(
    rf"(?:\b(?:{_CEILING_WORD_PATTERN})\b[\s:]*-\s*(?:{_CURRENCY_TOKEN}\s*)?\d)"
    rf"|(?:(?:{_CURRENCY_TOKEN})\s*-\s*\d)",
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
        value = Decimal(amount.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    if multiplier:
        for suffix, factor in _MULTIPLIERS:
            if multiplier.lower() == suffix:
                value *= factor
                break
    if not value.is_finite() or value <= 0 or value > 1_000_000_000:
        return None
    minor = value * 100
    if minor != minor.to_integral_value():
        return None
    return int(minor)


INVALID_MONETARY_CONSTRAINT = "INVALID_MONETARY_CONSTRAINT"
AMBIGUOUS_MONETARY_CONSTRAINT = "AMBIGUOUS_MONETARY_CONSTRAINT"
CONFLICTING_MONETARY_CONSTRAINT = "CONFLICTING_MONETARY_CONSTRAINT"
HARD_MONETARY_PROBLEMS = frozenset(
    {
        INVALID_MONETARY_CONSTRAINT,
        AMBIGUOUS_MONETARY_CONSTRAINT,
        CONFLICTING_MONETARY_CONSTRAINT,
    }
)


class MonetaryConstraintError(ValueError):
    """A money-like phrase was unsafe to interpret as an authorization limit."""

    def __init__(self, code: str) -> None:
        if code not in HARD_MONETARY_PROBLEMS:
            raise ValueError("monetary constraint error code is not registered")
        self.code = code
        self.public_message = (
            "The spending constraint is invalid, ambiguous, or contradictory. "
            "Rewrite it as one positive amount and one currency, for example "
            "'under INR 4000'. Authorization did not run."
        )
        super().__init__(f"{code}: {self.public_message}")


def reject_monetary_problem(constraint: MonetaryConstraint) -> None:
    if constraint.problem is not None:
        raise MonetaryConstraintError(constraint.problem)


@dataclass(frozen=True, slots=True)
class MonetaryConstraint:
    """One fail-closed money parse shared by discovery and commerce."""

    max_total_minor: int | None
    currency: str
    problem: str | None = None


def _currency_in(token: str) -> str | None:
    currencies = {
        _CURRENCY_SYMBOLS[match.group(0).lower()]
        for match in _CURRENCY_RE.finditer(token)
    }
    if len(currencies) > 1:
        return CONFLICTING_MONETARY_CONSTRAINT
    return next(iter(currencies), None)


def _parse_money_token(token: str) -> tuple[int | None, str | None, str | None]:
    number = _NUMBER_RE.search(token)
    if number is None:
        return None, None, INVALID_MONETARY_CONSTRAINT
    multiplier = _MULTIPLIER_RE.search(token)
    amount = _to_minor(number.group(0), multiplier.group(1) if multiplier else None)
    currency = _currency_in(token)
    if currency == CONFLICTING_MONETARY_CONSTRAINT:
        return None, None, CONFLICTING_MONETARY_CONSTRAINT
    if amount is None:
        return None, currency, INVALID_MONETARY_CONSTRAINT
    return amount, currency, None


def _outside(span: tuple[int, int], match: re.Match[str]) -> bool:
    return match.start() < span[0] or match.end() > span[1]


def parse_monetary_constraint(text: str) -> MonetaryConstraint:
    """Parse one hard ceiling, or return an explicit fail-closed problem.

    Money is never assembled from separate clauses. Scientific notation,
    signed values, ambiguous dotted thousands, multiple ceilings, and mixed
    currencies are rejected rather than partially matched.
    """

    if _NEGATIVE_MONEY_RE.search(text) or _EXPONENT_RE.search(text):
        return MonetaryConstraint(None, DEFAULT_CURRENCY, INVALID_MONETARY_CONSTRAINT)
    if _AMBIGUOUS_DOTTED_RE.search(text):
        return MonetaryConstraint(None, DEFAULT_CURRENCY, AMBIGUOUS_MONETARY_CONSTRAINT)

    ranges = list(_RANGE_RE.finditer(text))
    ceilings = list(_CEILING_RE.finditer(text))
    postfix = list(_POSTFIX_CEILING_RE.finditer(text))
    if ranges:
        if len(ranges) != 1:
            return MonetaryConstraint(None, DEFAULT_CURRENCY, CONFLICTING_MONETARY_CONSTRAINT)
        match = ranges[0]
        # The inner "to" amounts must not also count as independent ceilings.
        independent = [item for item in ceilings + postfix if _outside(match.span(), item)]
        extras = [item for item in _CURRENCY_AMOUNT_RE.finditer(text) if _outside(match.span(), item)]
        if independent or extras:
            return MonetaryConstraint(None, DEFAULT_CURRENCY, CONFLICTING_MONETARY_CONSTRAINT)
        low, low_currency, low_problem = _parse_money_token(match.group("low"))
        high, high_currency, high_problem = _parse_money_token(match.group("high"))
        if low_problem or high_problem:
            return MonetaryConstraint(None, DEFAULT_CURRENCY, low_problem or high_problem)
        if low_currency and high_currency and low_currency != high_currency:
            return MonetaryConstraint(None, DEFAULT_CURRENCY, CONFLICTING_MONETARY_CONSTRAINT)
        currency = low_currency or high_currency or DEFAULT_CURRENCY
        if low is None or high is None or low > high:
            return MonetaryConstraint(None, currency, CONFLICTING_MONETARY_CONSTRAINT)
        return MonetaryConstraint(high, currency)

    constructs = ceilings + postfix
    if len(constructs) > 1:
        # The same amount can be matched as both prefix and postfix only in
        # contrived text such as "under 4000 max"; it still has one meaning.
        spans = {item.span("amount") for item in constructs}
        if len(spans) != 1:
            return MonetaryConstraint(None, DEFAULT_CURRENCY, CONFLICTING_MONETARY_CONSTRAINT)
        constructs = [constructs[0]]
    if constructs:
        match = constructs[0]
        span = match.span("amount")
        extras = [item for item in _CURRENCY_AMOUNT_RE.finditer(text) if _outside(span, item)]
        if extras:
            return MonetaryConstraint(None, DEFAULT_CURRENCY, CONFLICTING_MONETARY_CONSTRAINT)
        amount, currency, problem = _parse_money_token(match.group("amount"))
        return MonetaryConstraint(
            amount if problem is None else None,
            currency or DEFAULT_CURRENCY,
            problem,
        )

    # Currency-bearing amounts without a comparator are not silently promoted
    # to budgets. They may be a price observation rather than a spending cap.
    if _CURRENCY_AMOUNT_RE.search(text):
        return MonetaryConstraint(None, DEFAULT_CURRENCY, AMBIGUOUS_MONETARY_CONSTRAINT)
    # A comparator followed by numeric-looking syntax that the grammar did not
    # accept is malformed, not an absent budget.
    if re.search(rf"\b(?:{_CEILING_WORD_PATTERN})\b[^.;\n]{{0,24}}(?:\d|[-+₹$€£])", text, re.IGNORECASE):
        return MonetaryConstraint(None, DEFAULT_CURRENCY, INVALID_MONETARY_CONSTRAINT)
    return MonetaryConstraint(None, DEFAULT_CURRENCY)


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
    parse_problems: tuple[str, ...] = field(default_factory=tuple)

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
            "parse_problems": list(self.parse_problems),
        }

    def plain_english(self) -> list[str]:
        """One readable line per extracted constraint, for the product surface."""

        lines: list[str] = []
        if self.parse_problems:
            lines.append(
                "The monetary constraint is invalid or ambiguous; authorization is blocked."
            )
            return lines
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

    monetary = parse_monetary_constraint(raw)
    currency = monetary.currency
    ceiling = monetary.max_total_minor
    parse_problems = (monetary.problem,) if monetary.problem is not None else ()
    if ceiling is None and monetary.problem is None:
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
        parse_problems=parse_problems,
    )


def _search_text(raw: str) -> str:
    """Strip the constraint clauses so retrieval sees the product request."""

    text = _RANGE_RE.sub(" ", raw)
    text = _CEILING_RE.sub(" ", text)
    text = _POSTFIX_CEILING_RE.sub(" ", text)
    text = _CURRENCY_AMOUNT_RE.sub(" ", text)
    text = _EXCLUSION_RE.sub(" ", text)
    for term in _ONE_TIME_TERMS:
        text = re.sub(re.escape(term), " ", text, flags=re.IGNORECASE)
    lowered = text.casefold()
    for phrase in _QUERY_NOISE:
        lowered = re.sub(rf"\b{re.escape(phrase)}\b", " ", lowered)
    return re.sub(r"[^\w\s'’-]+", " ", re.sub(r"\s+", " ", lowered)).strip()
