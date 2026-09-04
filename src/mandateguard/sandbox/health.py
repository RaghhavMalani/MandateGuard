"""Measuring whether the sandbox is fit for somebody to try things in.

The question this answers is not "is the controller correct" - that is what the
authorization test suite is for - but "does an ordinary request reach an
ordinary outcome". A world in which every honest question ends in REVIEW is
technically impeccable and useless to demonstrate with, and the only way to know
which one has been built is to run a frozen set of realistic instructions
through the whole pathway and count what comes back.

Two rules keep the measurement honest.

**The frozen query set records no expected verdict.** Each entry names the kind
of question it asks, never the answer. There is nothing here to tune the world
towards, because there is no per-query target to hit.

**Every outcome comes from the real controller.** This module runs searches and
authorization runs; it never derives a decision itself, and the numbers it
reports are counts of what the controller actually said.

When the distribution is unhealthy, the thing to fix is the sandbox data. Never
the controller.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Mapping
from uuid import uuid4


QUERY_SET_PATH = Path("fixtures") / "playground" / "judge_queries.json"

#: Experience targets, not safety contracts. A miss here says the sandbox is
#: hard to demonstrate with, not that anything decided incorrectly.
TARGET_CANDIDATE_FOUND_RATE = 0.95
TARGET_ORDINARY_ALLOW_RATE = 0.60
TARGET_MAX_ORDINARY_REVIEW_RATE = 0.30


@dataclass(frozen=True, slots=True)
class QueryOutcome:
    query_id: str
    cohort: str
    text: str
    candidates_found: int
    candidate_name: str | None
    candidate_price_minor: int | None
    ceiling_source: str
    decision: str | None
    reason: str | None
    state: str
    search_ms: float
    authorization_ms: float

    def to_mapping(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "cohort": self.cohort,
            "text": self.text,
            "candidates_found": self.candidates_found,
            "candidate": self.candidate_name,
            "candidate_price_minor": self.candidate_price_minor,
            "ceiling_source": self.ceiling_source,
            "decision": self.decision,
            "reason": self.reason,
            "state": self.state,
            "search_ms": round(self.search_ms, 3),
            "authorization_ms": round(self.authorization_ms, 3),
        }


def load_query_set(repository_root: Path) -> Mapping[str, Any]:
    path = repository_root / QUERY_SET_PATH
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("queries"), list):
        raise ValueError("judge query set is malformed")
    return data


def run_query(service: Any, entry: Mapping[str, Any], session_id: str) -> QueryOutcome:
    """Search, take the top candidate, and let the controller decide.

    "Top candidate" is the ranker's first result and nothing more considered
    than that. Choosing more cleverly - skipping listings whose evidence looks
    awkward, say - would measure the chooser rather than the world.
    """

    text = str(entry["text"])
    started = perf_counter()
    search = service.playground_search(intent=text, top_k=8, session_id=session_id)
    search_ms = (perf_counter() - started) * 1000.0
    candidates = search["candidates"]
    if not candidates:
        return QueryOutcome(
            query_id=str(entry["id"]),
            cohort=str(entry["cohort"]),
            text=text,
            candidates_found=0,
            candidate_name=None,
            candidate_price_minor=None,
            ceiling_source=search["mandate"]["ceiling_source"],
            decision=None,
            reason="No sandbox listing matched the stated constraints.",
            state="NO_CANDIDATE",
            search_ms=search_ms,
            authorization_ms=0.0,
        )
    top = candidates[0]
    # When the instruction states no limit the Playground asks for one, and
    # pre-fills the chosen listing's price. Mirroring that here is what makes
    # the measurement match the experience; the report separates the cohorts
    # so the effect on the numbers stays visible.
    declared_ceiling = (
        None
        if search["mandate"]["max_total_minor"] is not None
        else int(top["price_minor"])
    )
    started = perf_counter()
    run, _deduplicated, _session = service.playground_authorize(
        intent=text,
        catalog_product_id=top["catalog_product_id"],
        request_id="health_" + uuid4().hex,
        session_id=session_id,
        max_total_minor=declared_ceiling,
    )
    run.completion.wait(60)
    authorization_ms = (perf_counter() - started) * 1000.0
    snapshot = run.snapshot()
    result = snapshot.get("result")
    decision = result["decision"] if isinstance(result, dict) else None
    reason = result["decision_reason"] if isinstance(result, dict) else None
    if snapshot["state"] == "ERROR":
        reason = str((snapshot.get("error") or {}).get("message"))
    return QueryOutcome(
        query_id=str(entry["id"]),
        cohort=str(entry["cohort"]),
        text=text,
        candidates_found=len(candidates),
        candidate_name=str(top["name"]),
        candidate_price_minor=int(top["price_minor"]),
        ceiling_source=(
            "STATED_IN_INSTRUCTION" if declared_ceiling is None else "SET_FOR_THIS_CHECK"
        ),
        decision=decision,
        reason=reason,
        state=snapshot["state"],
        search_ms=search_ms,
        authorization_ms=authorization_ms,
    )


def run_insistent_query(
    service: Any, entry: Mapping[str, Any], session_id: str
) -> QueryOutcome | None:
    """Authorize a listing the agent had already flagged, if there was one.

    The default pass measures what a person is *offered*: search withholds
    listings that break a stated constraint, so its top result almost always
    satisfies the mandate and the controller almost always permits it. That is
    the agent behaving well, and it measures the agent rather than the gate.

    This pass measures the gate. It takes the first listing search set aside -
    over budget, or a renewing plan the buyer did not ask for - and puts it
    through authorization anyway, exactly as a person clicking past the warning
    would. Nothing here predicts the verdict; it records what the controller
    said when the safe choice was refused.
    """

    text = str(entry["text"])
    started = perf_counter()
    search = service.playground_search(intent=text, top_k=8, session_id=session_id)
    search_ms = (perf_counter() - started) * 1000.0
    near_misses = search["near_misses"]
    if not near_misses:
        return None
    chosen = near_misses[0]
    declared_ceiling = (
        None
        if search["mandate"]["max_total_minor"] is not None
        else int(chosen["price_minor"])
    )
    started = perf_counter()
    run, _deduplicated, _session = service.playground_authorize(
        intent=text,
        catalog_product_id=chosen["catalog_product_id"],
        request_id="insist_" + uuid4().hex,
        session_id=session_id,
        max_total_minor=declared_ceiling,
    )
    run.completion.wait(60)
    authorization_ms = (perf_counter() - started) * 1000.0
    snapshot = run.snapshot()
    result = snapshot.get("result")
    return QueryOutcome(
        query_id=str(entry["id"]),
        cohort=str(entry["cohort"]),
        text=text,
        candidates_found=len(near_misses),
        candidate_name=str(chosen["name"]),
        candidate_price_minor=int(chosen["price_minor"]),
        ceiling_source=str(chosen["excluded_by"]),
        decision=result["decision"] if isinstance(result, dict) else None,
        reason=(
            result["decision_reason"]
            if isinstance(result, dict)
            else str((snapshot.get("error") or {}).get("message"))
        ),
        state=snapshot["state"],
        search_ms=search_ms,
        authorization_ms=authorization_ms,
    )


def _distribution(outcomes: list[QueryOutcome]) -> dict[str, Any]:
    total = len(outcomes)
    counts = {"ALLOW": 0, "BLOCK": 0, "REVIEW": 0, "NO_RESULT": 0, "ERROR": 0}
    for outcome in outcomes:
        if outcome.state == "NO_CANDIDATE":
            counts["NO_RESULT"] += 1
        elif outcome.state == "ERROR" or outcome.decision is None:
            counts["ERROR"] += 1
        else:
            counts[outcome.decision] += 1
    return {
        "total": total,
        "counts": counts,
        "rates": {
            key: (round(value / total, 4) if total else 0.0)
            for key, value in counts.items()
        },
        "candidate_found_rate": (
            round(sum(1 for item in outcomes if item.candidates_found > 0) / total, 4)
            if total
            else 0.0
        ),
    }


def evaluate(service: Any, repository_root: Path) -> dict[str, Any]:
    """Run the whole frozen set and report what the controller answered."""

    query_set = load_query_set(repository_root)
    session_id = service.open_judge_session()["session_id"]
    outcomes = [
        run_query(service, entry, session_id) for entry in query_set["queries"]
    ]
    insistent = [
        outcome
        for outcome in (
            run_insistent_query(service, entry, session_id)
            for entry in query_set["queries"]
        )
        if outcome is not None
    ]
    by_cohort: dict[str, list[QueryOutcome]] = {}
    for outcome in outcomes:
        by_cohort.setdefault(outcome.cohort, []).append(outcome)
    ordinary = [
        outcome
        for outcome in outcomes
        if outcome.cohort in {"ORDINARY", "ORDINARY_NO_BUDGET", "AWKWARD"}
    ]
    overall = _distribution(outcomes)
    ordinary_distribution = _distribution(ordinary)
    search_times = sorted(item.search_ms for item in outcomes)
    authorization_times = sorted(
        item.authorization_ms for item in outcomes if item.authorization_ms > 0
    )
    manifest = service.playground.manifest
    return {
        "query_set_version": query_set["query_set_version"],
        "world_version": manifest["world_version"],
        "products_sha256": manifest["products_sha256"],
        "controller": "EXISTING_FROZEN_MANDATEGUARD_CONTROLLER",
        "queries": len(outcomes),
        "overall": overall,
        "ordinary": ordinary_distribution,
        "insistent_selection": {
            "note": (
                "Authorizing the first listing search had set aside for breaking a "
                "stated constraint. Measures the gate rather than the agent."
            ),
            **_distribution(insistent),
        },
        "by_cohort": {
            cohort: _distribution(items) for cohort, items in sorted(by_cohort.items())
        },
        "latency_ms": {
            "search_p50": _percentile(search_times, 0.50),
            "search_p95": _percentile(search_times, 0.95),
            "authorization_p50": _percentile(authorization_times, 0.50),
            "authorization_p95": _percentile(authorization_times, 0.95),
        },
        "experience_targets": {
            "candidate_found_rate_at_least": TARGET_CANDIDATE_FOUND_RATE,
            "ordinary_allow_rate_at_least": TARGET_ORDINARY_ALLOW_RATE,
            "ordinary_review_rate_at_most": TARGET_MAX_ORDINARY_REVIEW_RATE,
            "status": "EXPERIENCE_TARGET_NOT_A_SAFETY_CONTRACT",
        },
        "outcomes": [item.to_mapping() for item in outcomes],
        "insistent_outcomes": [item.to_mapping() for item in insistent],
    }


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    index = min(len(values) - 1, max(0, int(round(fraction * (len(values) - 1)))))
    return round(values[index], 3)
