"""Reading an arbitrary buying instruction into a bounded purchase mandate.

This module sits on the *agent* side of the boundary. It has authority NONE: it
proposes an interpretation, and the controller decides what to do with the
mandate built from it. Getting the interpretation wrong cannot make an unsafe
purchase safe, because every constraint it produces is enforced downstream by
Tier A/B/C, and every constraint it fails to produce simply is not asserted.

That asymmetry is why two rules govern what may change here.

**Never drop a stated constraint.** A spending ceiling the user typed, an
exclusion the user typed, and a recurrence stance the user typed all survive
into the mandate verbatim. Widening the exclusion grammar (catching "nothing
involving gambling" as well as "no gambling") is always allowed, because it can
only add constraints the buyer actually stated.

**Never invent one either.** A phrase like "camera for beginners" contains the
word "for", and a naive purpose extractor turns that into a declared purchase
purpose of "beginners" that no merchant on earth has published evidence for.
The result is a REVIEW that reflects the parser, not the world. So a purpose
constraint is asserted only when the text uses a recognised purpose phrase from
the same closed vocabulary the sandbox merchants publish against.

Everything else - the search text, the brand hints - is advisory input to
retrieval and never reaches an authorization check.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from mandateguard.discovery.intent import (
    MonetaryConstraintError,
    ParsedIntent,
    parse_intent,
    reject_monetary_problem,
)
from mandateguard.intelligence.models import InterpretedPurchaseIntent

from mandateguard.sandbox.coverage import ConstraintCoverage, assess_coverage
from mandateguard.sandbox.templates import ALL_PURPOSES


INTERPRETER_VERSION = "sandbox-intent-reader-v1"

#: A mandate the user did not bound is not a mandate. When the instruction
#: states no ceiling the Playground asks for one explicitly rather than
#: inventing a number, and this is the largest it will accept.
MAX_DECLARED_CEILING_MINOR = 100_000_000  # INR 10,00,000.00

#: Purpose phrasing recognised as a *declared purchase purpose*. Each maps onto
#: the closed vocabulary the sandbox merchants publish in their intended-use
#: records, so a matched purpose is one a merchant can actually answer.
_PURPOSE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("individual study", re.compile(r"\b(?:individual study|self study|studying|study(?:ing)? at night|exam prep|revision|homework|school ?work|college work)\b", re.I)),
    ("professional development", re.compile(r"\b(?:professional development|upskilling|career development|work training|professional training|certification)\b", re.I)),
    ("office work", re.compile(r"\b(?:office work|office use|work from home|desk work|my office|the office|workspace)\b", re.I)),
    ("home use", re.compile(r"\b(?:home use|house use|at home|for my home|household use)\b", re.I)),
    ("personal use", re.compile(r"\b(?:personal use|my own use|everyday use|daily use)\b", re.I)),
    ("fitness training", re.compile(r"\b(?:fitness training|gym|working out|workouts?|running training|marathon training|exercise sessions?)\b", re.I)),
    ("travel use", re.compile(r"\b(?:travel use|travelling|traveling|for travel|for trips?|commuting|my commute)\b", re.I)),
    ("photography work", re.compile(r"\b(?:photography work|photo shoots?|shooting photos|photography practice)\b", re.I)),
    ("kitchen use", re.compile(r"\b(?:kitchen use|cooking at home|in the kitchen|for cooking)\b", re.I)),
)

#: An explicit purpose clause the user wrote out in full, e.g. "for the purpose
#: of individual study". Matched against the canonical vocabulary as well, so a
#: user who names a purpose the sandbox has no vocabulary for still gets that
#: purpose asserted rather than silently dropped.
_EXPLICIT_PURPOSE_RE = re.compile(
    r"\bfor the purpose of\s+([^.;,\n]{3,60})", re.IGNORECASE
)


class SandboxIntentError(ValueError):
    """The instruction could not be read into a bounded mandate."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.public_message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class SandboxIntent:
    """One reading of a buying instruction, plus how it was reached."""

    raw_text: str
    search_text: str
    max_total_minor: int | None
    ceiling_source: str
    currency: str
    quantity: int
    recurring_allowed: bool
    recurrence_stated: bool
    purpose: str | None
    exclusions: tuple[str, ...]
    brand_hints: tuple[str, ...]
    product_family_label: str | None
    product_family_allowlist: tuple[str, ...] | None
    product_family_match: str | None
    product_family_available: bool | None
    parsed: ParsedIntent
    coverage: ConstraintCoverage

    def to_mapping(self) -> dict[str, Any]:
        product_family = (
            {
                "label": self.product_family_label,
                "allowed_category_ids": list(self.product_family_allowlist or ()),
                "matched_phrase": self.product_family_match,
                "available_in_sandbox": self.product_family_available,
                "enforcement": "HARD_DETERMINISTIC",
            }
            if self.product_family_label is not None
            else None
        )
        return {
            "interpreter_version": INTERPRETER_VERSION,
            "raw_text": self.raw_text,
            "search_text": self.search_text,
            "max_total_minor": self.max_total_minor,
            "ceiling_source": self.ceiling_source,
            "currency": self.currency,
            "quantity": self.quantity,
            "recurring_allowed": self.recurring_allowed,
            "recurrence_stated": self.recurrence_stated,
            "purpose": self.purpose,
            "exclusions": list(self.exclusions),
            "brand_hints": list(self.brand_hints),
            "product_family": product_family,
            "coverage": self.coverage.to_mapping(),
            "authority": "NONE",
        }

    def plain_english(self) -> list[str]:
        """The mandate in the words a person would use to check it."""

        lines: list[str] = []
        if self.max_total_minor is None:
            lines.append(
                "No spending limit was stated. MandateGuard will not authorize an "
                "unbounded purchase, so a limit has to be set before the check runs."
            )
        else:
            lines.append(
                f"Spend at most {self.currency} {self.max_total_minor / 100:,.2f} in total"
                + (
                    " (taken from your instruction)."
                    if self.ceiling_source == "STATED_IN_INSTRUCTION"
                    else " (you set this limit for this check)."
                )
            )
        if self.quantity != 1:
            lines.append(f"Buy {self.quantity} units.")
        if self.recurring_allowed:
            lines.append("A recurring charge is acceptable.")
        elif self.recurrence_stated:
            lines.append("One-time payment only. No subscription or recurring charge.")
        else:
            lines.append(
                "No recurring charge, because you did not ask for one and a "
                "renewing plan is not assumed."
            )
        if self.purpose is not None:
            lines.append(f"Declared purpose: {self.purpose}.")
        for item in self.exclusions:
            lines.append(f"Nothing involving {item}.")
        for item in self.brand_hints:
            lines.append(f"Prefer the brand {item}.")
        if self.product_family_label is not None:
            lines.append(
                f"Product family: {self.product_family_label} "
                "(deterministically enforced)."
            )
        # Say what was *not* understood in the same list as what was. A
        # requirement the mandate does not carry is the one a person most needs
        # to see, so it is never relegated to a separate panel.
        for quote in self.coverage.quoted:
            lines.append(
                f"“{quote}” could not be interpreted as an enforceable "
                "constraint, so MandateGuard will not authorize on it."
            )
        return lines

    def interpreted(
        self,
        *,
        merchant_allowlist: tuple[str, ...] | None = None,
        sku_allowlist: tuple[str, ...] | None = None,
    ) -> InterpretedPurchaseIntent:
        """Project into the typed intent the mandate builder consumes."""

        if self.max_total_minor is None:
            raise SandboxIntentError(
                "SPENDING_LIMIT_REQUIRED",
                "A spending limit is required before authorization can run.",
            )
        # Last line of defence. ``PlaygroundSurface.plan_for`` refuses an
        # unresolved instruction before a run is ever created; this refuses it
        # again at the point the mandate would be built, so a future caller
        # that reaches the mandate by another route cannot skip the check.
        if self.coverage.blocks_authorization:
            raise SandboxIntentError(
                "INPUT_CLARIFICATION_REQUIRED",
                self.coverage.clarification_message(),
            )
        return InterpretedPurchaseIntent(
            max_total_minor=self.max_total_minor,
            quantity=self.quantity,
            currency=self.currency,
            purpose=self.purpose,
            recurring_allowed=self.recurring_allowed,
            exclusions=self.exclusions,
            merchant_allowlist=merchant_allowlist,
            sku_allowlist=sku_allowlist,
            product_family_allowlist=self.product_family_allowlist,
        )


def _purpose_in(text: str) -> tuple[str | None, tuple[int, int] | None]:
    """The declared purpose and the span it was read from.

    The span is returned so the coverage auditor can tell that these words were
    accounted for. Without it, "for the purpose of ..." would be re-read as
    unexplained language and produce a clarification prompt for a constraint
    that was in fact recognised.
    """

    explicit = _EXPLICIT_PURPOSE_RE.search(text)
    if explicit is not None:
        stated = explicit.group(1).strip(" -\"'")
        for canonical, pattern in _PURPOSE_PATTERNS:
            if pattern.search(stated):
                return canonical, explicit.span()
        return (stated[:120] if stated else None), explicit.span()
    for canonical, pattern in _PURPOSE_PATTERNS:
        found = pattern.search(text)
        if found is not None:
            return canonical, found.span()
    return None, None


def read_intent(
    text: str,
    *,
    known_brands: tuple[str, ...] = (),
    declared_ceiling_minor: int | None = None,
) -> SandboxIntent:
    """Read one buying instruction. Fails closed on unsafe monetary text.

    ``declared_ceiling_minor`` is a limit the person set in the Playground for
    this check. It is used **only** when the instruction itself states no
    ceiling. A ceiling written into the instruction always wins, so a client
    cannot quietly raise a budget its user typed.
    """

    if not isinstance(text, str) or not text.strip():
        raise SandboxIntentError("EMPTY_INSTRUCTION", "Type what you want to buy.")
    parsed = parse_intent(text, known_brands=known_brands)
    # Fail closed before anything else looks at the number.
    reject_monetary_problem_for(parsed)

    ceiling = parsed.max_total_minor
    ceiling_source = "STATED_IN_INSTRUCTION"
    if ceiling is None:
        if declared_ceiling_minor is None:
            ceiling_source = "NOT_STATED"
        else:
            if (
                isinstance(declared_ceiling_minor, bool)
                or not isinstance(declared_ceiling_minor, int)
                or not 1 <= declared_ceiling_minor <= MAX_DECLARED_CEILING_MINOR
            ):
                raise SandboxIntentError(
                    "INVALID_SPENDING_LIMIT",
                    "The spending limit must be a positive amount in minor units, "
                    f"at most {MAX_DECLARED_CEILING_MINOR // 100:,} INR.",
                )
            ceiling = declared_ceiling_minor
            ceiling_source = "SET_FOR_THIS_CHECK"

    purpose, purpose_span = _purpose_in(parsed.raw_text)
    search_text = parsed.search_text or parsed.raw_text.strip().lower()
    # Read here, not at the surface. The product family is the one reading on
    # this side that becomes a *hard* mandate constraint, so it has to travel
    # with every SandboxIntent rather than being attached by whichever caller
    # remembered to. Imported inside the function because the search module
    # reads SandboxIntent from here; at call time both modules exist.
    from mandateguard.sandbox.search import infer_product_family

    family = infer_product_family(search_text)
    return SandboxIntent(
        raw_text=parsed.raw_text,
        search_text=search_text,
        max_total_minor=ceiling,
        ceiling_source=ceiling_source,
        currency=parsed.currency,
        quantity=parsed.quantity,
        # Absent an explicit "a subscription is fine", a renewing charge is not
        # permitted. Silence is not consent to be billed again next month.
        recurring_allowed=parsed.recurring_allowed is True,
        recurrence_stated=parsed.recurring_allowed is not None,
        purpose=purpose,
        exclusions=parsed.exclusions,
        brand_hints=parsed.brand_hints,
        product_family_label=family.label if family is not None else None,
        product_family_allowlist=(
            family.category_ids if family is not None else None
        ),
        product_family_match=family.matched_phrase if family is not None else None,
        product_family_available=family.available if family is not None else None,
        parsed=parsed,
        coverage=assess_coverage(
            parsed.raw_text,
            parsed=parsed,
            purpose=purpose,
            purpose_span=purpose_span,
        ),
    )


def reject_monetary_problem_for(parsed: ParsedIntent) -> None:
    """Raise the shared monetary error for an instruction that cannot be trusted."""

    if parsed.parse_problems:
        raise MonetaryConstraintError(parsed.parse_problems[0])
