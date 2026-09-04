"""Checking whether the parser accounted for every hard restriction a person wrote.

The intent reader has two rules: never drop a stated constraint, never invent
one. The second rule is enforced by construction - a constraint is only asserted
from a recognised phrase. The first rule was, until this module existed,
enforced by nothing at all.

That asymmetry is not neutral. A dropped ceiling is loud, because a mandate
without a ceiling refuses to run. A dropped *exclusion* is silent, and silence
here widens authority: "no leather" is read as an exclusion and reaches Tier C,
while "vegan materials only" matches no exclusion pattern, produces no
constraint, and leaves a mandate that permits every backpack in the catalogue.
The person typed a hard restriction and got an unrestricted ALLOW.

So this module audits the parse rather than extending it. It answers one
question - *is there hard-restriction language in this instruction that no
recognised constraint accounts for?* - and it answers it deterministically, by
comparing character spans. It maps nothing onto meaning. It does not know what
"vegan" is, and deliberately never will: turning an unrecognised phrase into an
enforceable constraint is exactly the semantic authorization this architecture
refuses to perform. Its whole output is *we could not account for these words*,
which the Playground turns into a request for clarification.

The safety asymmetry runs the other way here, which is why the cue list errs
towards catching more. A false positive costs one clarification prompt. A false
negative is an authorized purchase the buyer forbade.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable

from mandateguard.discovery.intent import (
    ParsedIntent,
    _CEILING_RE,
    _CLAUSE_SPLIT_RE,
    _EXCLUSION_RE,
    _ONE_TIME_TERMS,
    _POSTFIX_CEILING_RE,
    _QUANTITY_RE,
    _QUANTITY_WORD_RE,
    _RANGE_RE,
    _RECURRING_TERMS,
    _clean_exclusion,
    _exclusion_parts,
)


COVERAGE_VERSION = "sandbox-constraint-coverage-v1"

#: Every recognised hard restriction is accounted for.
COMPLETE = "COMPLETE"
#: Restriction language remained that no constraint accounts for. The words are
#: unambiguously restrictive, so this is treated as a stated constraint that
#: MandateGuard cannot enforce.
UNRESOLVED_HARD_CONSTRAINT = "UNRESOLVED_HARD_CONSTRAINT"
#: Language that *may* be a restriction remained unaccounted for. Weaker
#: evidence of intent, identical consequence: nothing is authorized on a guess.
AMBIGUOUS = "AMBIGUOUS"

STRONG = "STRONG"
WEAK = "WEAK"

#: Phrases that state a hard restriction outright. If one of these is not
#: accounted for by a constraint the parser produced, the buyer restricted the
#: purchase in a way the mandate does not carry.
#:
#: "no" and "nothing" are here even though the exclusion grammar usually
#: catches them, because "usually" is the whole problem: "nothing refurbished"
#: is not matched by that grammar, and this list is what notices.
_STRONG_CUES: tuple[str, ...] = (
    "must not be",
    "must not have",
    "should not be",
    "should not have",
    "cannot contain",
    "can not contain",
    "must not",
    "should not",
    "shouldn't",
    "mustn't",
    "cannot be",
    "can't be",
    "cannot have",
    "can't have",
    "must have",
    "must be",
    "must",
    "has to be",
    "have to be",
    "needs to be",
    "need to be",
    "required to be",
    "strictly",
    "only",
    "nothing",
    "none",
    "never",
    "without",
    "avoid",
    "excluding",
    "exclude",
    "free of",
    "free from",
    "not any",
    "no",
    "at least",
    "minimum",
    "exactly",
)

#: Phrases that are restrictive in some readings and ordinary prose in others.
#: The comparators live here because an accounted comparator is the normal case
#: - "under 3000" resolves into the ceiling and never reaches this list - so one
#: that did *not* resolve is a comparison the parser failed to turn into a
#: limit, which is worth a question rather than a silent pass.
_WEAK_CUES: tuple[str, ...] = (
    "no more than",
    "not more than",
    "cheaper than",
    "less than",
    "more than",
    "at most",
    "maximum",
    "up to",
    "upto",
    "within",
    "under",
    "below",
    "above",
    "over",
    "max",
    "budget",
    "not",
)


def _cue_pattern(cues: Iterable[str]) -> re.Pattern[str]:
    """One alternation, longest phrase first so "must be" beats "must"."""

    ordered = sorted(set(cues), key=lambda item: (-len(item), item))
    body = "|".join(re.escape(item).replace(r"\ ", r"\s+") for item in ordered)
    return re.compile(rf"(?<![\w-])(?:{body})(?![\w-])", re.IGNORECASE)


_STRONG_RE = _cue_pattern(_STRONG_CUES)
_WEAK_RE = _cue_pattern(_WEAK_CUES)

#: How much of the surrounding clause is quoted back to the person.
_MAX_QUOTE_CHARS = 80


@dataclass(frozen=True, slots=True)
class UnresolvedSpan:
    """One stretch of restriction language no constraint accounts for."""

    cue: str
    strength: str
    start: int
    end: int
    text: str

    def to_mapping(self) -> dict[str, Any]:
        return {
            "cue": self.cue,
            "strength": self.strength,
            "start": self.start,
            "end": self.end,
            "text": self.text,
        }


@dataclass(frozen=True, slots=True)
class ConstraintCoverage:
    """What the parse accounted for, and what it did not."""

    recognized_constraints: tuple[str, ...]
    unresolved_constraint_spans: tuple[UnresolvedSpan, ...]
    coverage_status: str

    @property
    def blocks_authorization(self) -> bool:
        """Anything short of COMPLETE stops the run.

        AMBIGUOUS and UNRESOLVED_HARD_CONSTRAINT differ in how confidently the
        words read as a restriction, and not at all in what happens next. The
        distinction exists to word the question well, never to decide whether
        to ask it: authorizing on a maybe is the failure this module exists to
        prevent.
        """

        return self.coverage_status != COMPLETE

    @property
    def quoted(self) -> tuple[str, ...]:
        seen: list[str] = []
        for span in self.unresolved_constraint_spans:
            if span.text not in seen:
                seen.append(span.text)
        return tuple(seen)

    def clarification_message(self) -> str:
        """The sentence a person is asked to act on."""

        quoted = self.quoted
        if not quoted:
            return (
                "One of your requirements could not be interpreted safely. "
                "Clarify it before MandateGuard can authorize payment."
            )
        if len(quoted) == 1:
            subject = f"one of your requirements:\n\n“{quoted[0]}”"
        else:
            listed = "\n".join(f"“{item}”" for item in quoted)
            subject = f"some of your requirements:\n\n{listed}"
        return (
            f"I found matching products, but I could not safely interpret "
            f"{subject}\n\nClarify this requirement before MandateGuard can "
            "authorize payment."
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "coverage_version": COVERAGE_VERSION,
            "coverage_status": self.coverage_status,
            "recognized_constraints": list(self.recognized_constraints),
            "unresolved_constraint_spans": [
                span.to_mapping() for span in self.unresolved_constraint_spans
            ],
            "blocks_authorization": self.blocks_authorization,
            "authority": "NONE_DETECTION_ONLY_NEVER_INTERPRETATION",
        }


def _money_spans(raw: str, parsed: ParsedIntent) -> list[tuple[int, int]]:
    """Spans the monetary grammar consumed, but only if a ceiling resulted.

    A comparator that produced no limit is not accounted for by anything, and
    is exactly the case worth asking about.
    """

    if parsed.max_total_minor is None:
        return []
    spans: list[tuple[int, int]] = []
    for pattern in (_RANGE_RE, _CEILING_RE, _POSTFIX_CEILING_RE):
        spans.extend(match.span() for match in pattern.finditer(raw))
    return spans


def _exclusion_spans(raw: str, parsed: ParsedIntent) -> list[tuple[int, int]]:
    """Spans of exclusion matches that actually survived into the mandate.

    ``_clean_exclusion`` can reject what it extracted - a fragment under two
    characters, a clause over eighty - and when it does, the exclusion the
    person wrote is gone. Accounting for the match regardless would let that
    drop hide behind its own pattern, so a match only counts when at least one
    of its parts reached ``parsed.exclusions``.
    """

    kept = {item.casefold() for item in parsed.exclusions}
    spans: list[tuple[int, int]] = []
    for match in _EXCLUSION_RE.finditer(raw):
        for part in _exclusion_parts(match.group(1)):
            item = _clean_exclusion(part)
            if item is not None and item.casefold() in kept:
                spans.append(match.span())
                break
    return spans


def _term_spans(raw: str, terms: Iterable[str]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for term in terms:
        for match in re.finditer(rf"(?<![\w-]){re.escape(term)}(?![\w-])", raw, re.I):
            spans.append(match.span())
    return spans


def _purpose_spans(raw: str, purpose_span: tuple[int, int] | None) -> list[tuple[int, int]]:
    return [purpose_span] if purpose_span is not None else []


def _quantity_spans(raw: str, parsed: ParsedIntent) -> list[tuple[int, int]]:
    if parsed.quantity == 1:
        return []
    spans: list[tuple[int, int]] = []
    for pattern in (_QUANTITY_RE, _QUANTITY_WORD_RE):
        spans.extend(match.span() for match in pattern.finditer(raw))
    return spans


def _overlaps(span: tuple[int, int], spans: Iterable[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < other_end and other_start < end for other_start, other_end in spans)


def _clause_around(raw: str, start: int, end: int) -> str:
    """The clause the cue sits in, which is what makes the quote readable.

    Quoting forward from the cue is useless for a trailing modifier: "only" in
    "vegan materials only" would quote the word "only" and ask the person to
    clarify it. The clause carries the meaning, so the clause is quoted.
    """

    boundaries = [0] + [match.end() for match in _CLAUSE_SPLIT_RE.finditer(raw)] + [len(raw)]
    left = max((bound for bound in boundaries if bound <= start), default=0)
    right = min((bound for bound in boundaries if bound >= end), default=len(raw))
    clause = raw[left:right].strip(" \t\r\n,.;:—-")
    if len(clause) <= _MAX_QUOTE_CHARS:
        return clause
    # An unpunctuated instruction can put the whole sentence in one clause.
    # Keep the cue in view rather than truncating from the front.
    window_start = max(left, start - _MAX_QUOTE_CHARS // 2)
    return raw[window_start : window_start + _MAX_QUOTE_CHARS].strip(" \t\r\n,.;:—-")


def _recognized(parsed: ParsedIntent, purpose: str | None) -> tuple[str, ...]:
    recognized: list[str] = []
    if parsed.max_total_minor is not None:
        recognized.append(
            f"MAX_TOTAL: {parsed.currency} {parsed.max_total_minor / 100:,.2f}"
        )
    if parsed.quantity != 1:
        recognized.append(f"QUANTITY: {parsed.quantity}")
    if parsed.recurring_allowed is False:
        recognized.append("RECURRENCE: one-time payment only")
    elif parsed.recurring_allowed is True:
        recognized.append("RECURRENCE: recurring charge permitted")
    if purpose is not None:
        recognized.append(f"PURPOSE: {purpose}")
    for item in parsed.exclusions:
        recognized.append(f"EXCLUSION: {item}")
    return tuple(recognized)


def assess_coverage(
    raw: str,
    *,
    parsed: ParsedIntent,
    purpose: str | None = None,
    purpose_span: tuple[int, int] | None = None,
) -> ConstraintCoverage:
    """Report restriction language the recognised constraints do not account for.

    This reads the finished parse and the original words. It produces no
    constraint, alters no constraint, and has authority NONE over the decision;
    the only thing it can cause is a question.
    """

    accounted: list[tuple[int, int]] = []
    accounted.extend(_money_spans(raw, parsed))
    accounted.extend(_exclusion_spans(raw, parsed))
    accounted.extend(_term_spans(raw, _ONE_TIME_TERMS))
    if parsed.recurring_allowed is not None:
        accounted.extend(_term_spans(raw, _RECURRING_TERMS))
    accounted.extend(_purpose_spans(raw, purpose_span))
    accounted.extend(_quantity_spans(raw, parsed))

    unresolved: list[UnresolvedSpan] = []
    claimed: list[tuple[int, int]] = []
    for strength, pattern in ((STRONG, _STRONG_RE), (WEAK, _WEAK_RE)):
        for match in pattern.finditer(raw):
            span = match.span()
            if _overlaps(span, accounted) or _overlaps(span, claimed):
                continue
            claimed.append(span)
            unresolved.append(
                UnresolvedSpan(
                    cue=match.group(0),
                    strength=strength,
                    start=span[0],
                    end=span[1],
                    text=_clause_around(raw, span[0], span[1]),
                )
            )

    unresolved.sort(key=lambda item: item.start)
    if not unresolved:
        status = COMPLETE
    elif any(item.strength == STRONG for item in unresolved):
        status = UNRESOLVED_HARD_CONSTRAINT
    else:
        status = AMBIGUOUS
    return ConstraintCoverage(
        recognized_constraints=_recognized(parsed, purpose),
        unresolved_constraint_spans=tuple(unresolved),
        coverage_status=status,
    )
