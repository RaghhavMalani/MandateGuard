"""Deterministic network-free semantic doubles for demos and tests."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from time import perf_counter

from mandateguard.semantic.models import SemanticRequest
from mandateguard.semantic.verifier import SemanticModel


_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "be",
        "declared",
        "evidence",
        "for",
        "is",
        "must",
        "product",
        "purchase",
        "the",
        "this",
        "trusted",
    }
)


def _usage_token(usage: object, name: str) -> int | None:
    value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


class ResponsesUsageCapture:
    """Transparent Responses resource proxy that retains only token counts."""

    __slots__ = ("delegate", "last_input_tokens", "last_output_tokens")

    def __init__(self, delegate: object) -> None:
        if delegate is None or not callable(getattr(delegate, "create", None)):
            raise TypeError("delegate must provide responses.create")
        self.delegate = delegate
        self.last_input_tokens: int | None = None
        self.last_output_tokens: int | None = None

    def create(self, **kwargs: object) -> object:
        response = self.delegate.create(**kwargs)
        usage = getattr(response, "usage", None)
        self.last_input_tokens = _usage_token(usage, "input_tokens")
        self.last_output_tokens = _usage_token(usage, "output_tokens")
        return response


def _root(token: str) -> str:
    token = token.lower()
    if len(token) > 4 and token.endswith("s"):
        return token[:-1]
    return token


def _subject(text: str) -> tuple[str, ...]:
    candidate = text.split(":", 1)[-1].split(".", 1)[0]
    return tuple(
        _root(token)
        for token in _TOKEN_RE.findall(candidate)
        if token.lower() not in _STOPWORDS
    )


def _evidence_tokens(request: SemanticRequest) -> tuple[str, ...]:
    return tuple(
        _root(token)
        for entry in request.selected_evidence
        for token in _TOKEN_RE.findall(entry.text)
    )


def _negated(tokens: tuple[str, ...], subject: str) -> bool:
    for index, token in enumerate(tokens):
        if token != subject:
            continue
        window = tokens[max(0, index - 3) : index]
        if any(item in {"no", "not", "without", "exclude"} for item in window):
            return True
    return False


@dataclass(slots=True)
class DeterministicSemanticModel:
    """Small semantic fake driven by constraint/evidence text, never scenario IDs."""

    model_id: str = "offline-semantic-fake-v1"
    calls: list[SemanticRequest] = field(default_factory=list)

    def evaluate(self, request: SemanticRequest) -> object:
        self.calls.append(request)
        evidence = _evidence_tokens(request)
        evidence_set = frozenset(evidence)
        results: list[dict[str, str]] = []
        for constraint in request.constraints:
            subjects = _subject(constraint.text)
            if constraint.kind == "purpose":
                matched = bool(subjects) and all(
                    subject in evidence_set for subject in subjects
                )
                status = "PASS" if matched else "ABSTAIN"
                reason = (
                    "trusted evidence states the declared purpose"
                    if matched
                    else "trusted evidence does not establish the declared purpose"
                )
            elif constraint.kind == "exclusion":
                mentioned = [subject for subject in subjects if subject in evidence_set]
                explicitly_absent = bool(mentioned) and all(
                    _negated(evidence, subject) for subject in mentioned
                )
                one_time_subscription = (
                    "subscription" in subjects
                    and "one" in evidence_set
                    and "time" in evidence_set
                    and "purchase" in evidence_set
                )
                if explicitly_absent or one_time_subscription:
                    status = "PASS"
                    reason = "trusted evidence explicitly excludes the prohibited characteristic"
                elif mentioned:
                    status = "VIOLATION"
                    reason = "trusted evidence includes the prohibited characteristic"
                else:
                    status = "ABSTAIN"
                    reason = "trusted evidence is insufficient for the exclusion"
            else:
                status = "ABSTAIN"
                reason = "offline semantic fake does not cover this constraint kind"
            results.append(
                {
                    "constraint_id": constraint.constraint_id,
                    "status": status,
                    "reason": reason,
                }
            )
        return {"constraint_results": results}


class TimedSemanticModel:
    """Observability wrapper around the existing SemanticModel protocol."""

    __slots__ = (
        "delegate",
        "model_id",
        "last_latency_ms",
        "last_input_tokens",
        "last_output_tokens",
        "usage_source",
    )

    def __init__(self, delegate: SemanticModel, *, usage_source: object | None = None) -> None:
        if not isinstance(delegate, SemanticModel):
            raise TypeError("delegate must implement SemanticModel")
        self.delegate = delegate
        self.model_id = delegate.model_id
        self.last_latency_ms = 0.0
        self.last_input_tokens: int | None = None
        self.last_output_tokens: int | None = None
        self.usage_source = usage_source

    def evaluate(self, request: SemanticRequest) -> object:
        started = perf_counter()
        try:
            return self.delegate.evaluate(request)
        finally:
            self.last_latency_ms = (perf_counter() - started) * 1000.0
            source = self.usage_source or self.delegate
            self.last_input_tokens = getattr(source, "last_input_tokens", None)
            self.last_output_tokens = getattr(source, "last_output_tokens", None)
