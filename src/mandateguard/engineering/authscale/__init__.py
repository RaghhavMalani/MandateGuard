"""Authorization-scale measurement over a synthetic merchant universe.

Nothing in this package is a merchant network, and nothing in it is trusted
evidence about a real product. It exists to answer one question the historical
catalog cannot: how does the *existing* MandateGuard authorization architecture
behave when the evidence-complete world is large?

The historical marketplace catalog is deliberately not used here. A crawled
listing is not merchant evidence, and generating an authorization corpus from
one would quietly turn it into evidence.
"""

from mandateguard.engineering.authscale.universe import (
    FIXED_CLOCK,
    SEED,
    SKUS_PER_MERCHANT,
    WORLD_VERSION,
    AuthorizationCase,
    SyntheticMerchantUniverse,
    case_descriptor,
    descriptor_stream_sha256,
)

__all__ = [
    "FIXED_CLOCK",
    "SEED",
    "SKUS_PER_MERCHANT",
    "WORLD_VERSION",
    "AuthorizationCase",
    "SyntheticMerchantUniverse",
    "case_descriptor",
    "descriptor_stream_sha256",
]
