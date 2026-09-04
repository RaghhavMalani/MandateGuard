"""Deterministic text, price, and taxonomy normalization for imported rows.

Every rule here is mechanical and reversible-in-principle: it never invents a
value the source did not contain. A field the source omits stays ``None`` and is
reported as unresolved by the transactability diagnostic rather than filled in.
"""

from __future__ import annotations

import ast
from hashlib import sha256
import json
import re
from typing import Mapping


MAX_DESCRIPTION_CHARS = 2000
MAX_CATEGORY_DEPTH = 6
MIN_TOP_CATEGORY_SUPPORT = 25
UNCATEGORIZED = "Uncategorized"

_WHITESPACE_RE = re.compile(r"\s+")
_PRICE_FRAGMENT_RE = re.compile(r"\bPrice:\s*Rs\.?\s*[\d,]+(?:\.\d+)?\s*", re.IGNORECASE)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def collapse_text(value: str | None) -> str:
    """Collapse whitespace and drop control characters."""

    if not value:
        return ""
    return _WHITESPACE_RE.sub(" ", _CONTROL_RE.sub(" ", str(value))).strip()


def normalize_description(
    description: str | None, *, title: str
) -> tuple[str, bool, bool]:
    """Return ``(text, title_prefix_stripped, price_fragment_stripped)``.

    Two source artefacts are removed:

    * a verbatim repetition of the product title at the head of the description,
      which would otherwise make every listing look self-consistent to the
      title/description agreement feature; and
    * the ``Price: Rs. N`` fragment the crawler inlined into the prose, which is
      a stale 2016 figure and is never the authoritative price.
    """

    text = collapse_text(description)
    if not text:
        return "", False, False
    stripped_price = False
    replaced = _PRICE_FRAGMENT_RE.sub(" ", text)
    if replaced != text:
        stripped_price = True
        text = collapse_text(replaced)
    stripped_title = False
    clean_title = collapse_text(title)
    if clean_title and text.lower().startswith(clean_title.lower()):
        remainder = collapse_text(text[len(clean_title) :])
        if remainder:
            text = remainder
            stripped_title = True
    if len(text) > MAX_DESCRIPTION_CHARS:
        text = text[:MAX_DESCRIPTION_CHARS].rsplit(" ", 1)[0]
    return text, stripped_title, stripped_price


def parse_price_minor(*candidates: str | None) -> int | None:
    """Return the first parsable price in minor units, else ``None``."""

    for candidate in candidates:
        if candidate is None:
            continue
        text = str(candidate).strip().replace(",", "")
        if not text:
            continue
        try:
            value = float(text)
        except ValueError:
            continue
        if value < 0 or value != value or value in (float("inf"), float("-inf")):
            continue
        return int(round(value * 100))
    return None


def parse_rating(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        rating = float(text)
    except ValueError:
        return None
    if not 0.0 <= rating <= 5.0:
        return None
    return round(rating, 2)


def parse_category_tree(raw: str | None) -> tuple[str, ...]:
    """Parse Flipkart's Python-literal ``["A >> B >> C"]`` category column."""

    if not raw:
        return ()
    text = str(raw).strip()
    if not text:
        return ()
    try:
        parsed = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        parsed = text
    if isinstance(parsed, (list, tuple)):
        parsed = parsed[0] if parsed else ""
    if not isinstance(parsed, str):
        return ()
    segments = [collapse_text(part) for part in parsed.split(">>")]
    kept = tuple(part for part in segments if part)[:MAX_CATEGORY_DEPTH]
    return kept


def apply_taxonomy_floor(
    path: tuple[str, ...], *, supported_top_categories: frozenset[str]
) -> tuple[str, ...]:
    """Demote a top segment the source does not actually use as a category.

    In the Flipkart export a malformed row repeats the product name in the
    category column. Those segments occur a handful of times each and are not
    taxonomy nodes, so they are demoted under ``Uncategorized`` rather than
    silently accepted as 265 top-level categories.
    """

    if not path:
        return (UNCATEGORIZED,)
    if path[0] in supported_top_categories:
        return path
    return (UNCATEGORIZED,) + path[:MAX_CATEGORY_DEPTH - 1]


def raw_row_sha256(row: Mapping[str, object]) -> str:
    """Commit to the exact upstream row this listing was derived from."""

    canonical = json.dumps(
        {str(key): row[key] for key in sorted(row)},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()
