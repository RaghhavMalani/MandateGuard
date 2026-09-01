"""Curated presentation of immutable engineering evidence.

The product layer names repository artifacts without changing or reinterpreting
their recorded outcomes. These records are deliberately separate from the
runtime authorization trace.
"""

from __future__ import annotations

from typing import Any


INT3_RESEARCH_FINDING: dict[str, Any] = {
    "title": "EXPERIMENTAL EVIDENCE SUFFICIENCY",
    "mode": "RESEARCH_RESULT_ONLY",
    "finding": (
        "Evidence composition predicted single-execution action stability better "
        "than evidence quantity alone in a six-query engineering evaluation."
    ),
    "authorization_use": "Not used in the authorization gate.",
    "scope": (
        "62 correlated evidence subsets across six synthetic queries. This is not "
        "a safety score, authorization confidence, or probability that a transaction "
        "is safe."
    ),
    "source": (
        "artifacts/engineering/int3/"
        "sufficiency-loqo-20260831T143044Z-43f94887/RUN.md"
    ),
}


FAILURE_RECOVERY_EVIDENCE: tuple[dict[str, Any], ...] = (
    {
        "trigger": "Missing execution credentials",
        "outcome": "Safe stop before checkout or provider execution",
        "external_calls": 0,
        "source": (
            "artifacts/engineering/agentic_commerce/"
            "int1-razorpay-exec-20260830T070624Z-ae6bd048/RUN.md"
        ),
    },
    {
        "trigger": "Invalid signing configuration",
        "outcome": "Safe stop before cache, buyer, capability, or Razorpay",
        "external_calls": 0,
        "source": (
            "artifacts/engineering/agentic_commerce/"
            "int1-razorpay-exec-20260830T071906Z-b6ac8ed0/RUN.md"
        ),
    },
    {
        "trigger": "No trusted evidence retrieved",
        "outcome": "REVIEW without semantic evaluation or cache access",
        "external_calls": 0,
        "source": "docs/AGENTIC_COMMERCE_INTELLIGENCE.md",
    },
    {
        "trigger": "Corrupted semantic cache",
        "outcome": "Integrity rejection, bounded ABSTAIN, then REVIEW",
        "external_calls": 0,
        "source": (
            "artifacts/engineering/int2/"
            "stage-c-cache-live-20260830T131136Z-3946aa5/RUN.md"
        ),
    },
    {
        "trigger": "Capability replay",
        "outcome": "Rejected by the nonce ledger before a second network request",
        "additional_external_calls": 0,
        "source": (
            "artifacts/engineering/agentic_commerce/"
            "int1-razorpay-exec-20260830T074115Z-507323be/RUN.md"
        ),
    },
    {
        "trigger": "INT-3 artifact serializer failure",
        "outcome": (
            "Successful model response preserved, serializer fixed, and execution "
            "resumed without retrying the stochastic request"
        ),
        "retried_failed_request": False,
        "source": (
            "artifacts/engineering/int3/"
            "subset-live-recovery-20260831T135210Z-737beff7/RUN.md"
        ),
    },
)
