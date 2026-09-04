"""The one frozen text analyzer.

Both the offline trainer and the runtime query path call this module. If they
analyzed text differently the frozen vocabulary would silently stop matching the
queries it was trained for, so there is exactly one implementation and it is
pure standard library. ``ANALYZER_VERSION`` is written into every artifact and
checked on load.
"""

from __future__ import annotations

import re
import unicodedata


ANALYZER_VERSION = "discovery-analyzer-v1"

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:['’][a-z]+)?")
_MIN_TOKEN = 2
_MAX_TOKEN = 24

#: Deliberately small. An aggressive stop list removes tokens like "no" and
#: "not" that carry mandate meaning, so only high-frequency function words with
#: no commerce sense are dropped.
STOP_WORDS: frozenset[str] = frozenset(
    """
    a an the of and or to for with in on at by from as is are was were be been
    this that these those it its their his her your our you we they them he she
    into over under about above per via such than then there here
    """.split()
)

#: Suffix rules applied in order. English plural/gerund folding only: it makes
#: "lamps" match "lamp" without pulling in a full stemmer dependency.
_SUFFIXES: tuple[tuple[str, str, int], ...] = (
    ("ies", "y", 4),
    ("sses", "ss", 5),
    ("ches", "ch", 5),
    ("shes", "sh", 5),
    ("xes", "x", 4),
    ("s", "", 4),
)


def fold(token: str) -> str:
    """Fold a single token to its indexed form."""

    for suffix, replacement, minimum in _SUFFIXES:
        if len(token) >= minimum and token.endswith(suffix):
            if suffix == "s" and token.endswith("ss"):
                return token
            return token[: -len(suffix)] + replacement
    return token


def normalize(text: str) -> str:
    """Case-fold and strip accents so `Café` and `Cafe` index identically."""

    if not isinstance(text, str):
        raise TypeError("text must be a string")
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    )
    return stripped.casefold()


def analyze(text: str) -> list[str]:
    """Return the indexed token sequence for ``text``."""

    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(normalize(text)):
        raw = match.group(0)
        if len(raw) < _MIN_TOKEN or len(raw) > _MAX_TOKEN:
            continue
        if raw in STOP_WORDS:
            continue
        folded = fold(raw)
        if len(folded) < _MIN_TOKEN or folded in STOP_WORDS:
            continue
        tokens.append(folded)
    return tokens


def analyze_unique(text: str) -> list[str]:
    """Analyzed tokens, first occurrence order preserved, duplicates removed."""

    seen: set[str] = set()
    ordered: list[str] = []
    for token in analyze(text):
        if token not in seen:
            seen.add(token)
            ordered.append(token)
    return ordered
