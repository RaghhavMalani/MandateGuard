"""The MandateGuard judge sandbox: a synthetic, evidence-complete commerce world.

The sandbox exists because the historical marketplace corpus is honest but
undemonstrable. Seventeen thousand crawled 2015-2016 listings can be searched
and classified, and almost none of them carry the authoritative merchant
evidence an authorization controller needs, so almost every journey through them
correctly ends in REVIEW. That is the right answer to the wrong question for
somebody meeting the product for the first time.

So this package builds a second, clearly separated world: synthetic merchants
that publish exactly the evidence a real merchant would have to publish. It is
generated, versioned, and labelled SYNTHETIC everywhere it surfaces. It never
borrows trust from the marketplace corpus and never lends trust to it.

What the sandbox does *not* do is change how a decision is made. Sandbox runs go
through the same ``run_agentic_checkout`` controller, the same Tier A/B/C gate,
the same capability issuance, the same replay ledger and the same consent
registry as every other run. The sandbox supplies a richer world; it has no
authority over the verdict reached in it.
"""

from mandateguard.sandbox.templates import (
    CATEGORIES,
    EvidenceFamily,
    MERCHANTS,
    SANDBOX_MERCHANT_PREFIX,
    WORLD_VERSION,
)
from mandateguard.sandbox.universe import (
    SandboxProduct,
    SandboxUniverse,
    build_universe,
    universe_manifest,
)

__all__ = [
    "CATEGORIES",
    "EvidenceFamily",
    "MERCHANTS",
    "SANDBOX_MERCHANT_PREFIX",
    "SandboxProduct",
    "SandboxUniverse",
    "WORLD_VERSION",
    "build_universe",
    "universe_manifest",
]
