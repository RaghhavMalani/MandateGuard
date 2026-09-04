"""Large-catalog discovery: retrieval, classification, and diagnostics.

Everything here is advisory. The authoritative money-moving controller lives in
`mandateguard.policy`, `mandateguard.semantic`, and `mandateguard.execution`,
and nothing in this package can reach it. See `mandateguard.discovery.trust`.
"""

from mandateguard.discovery.catalog import (
    CatalogUnavailableError,
    DiscoveryCatalog,
    load_catalog,
)
from mandateguard.discovery.intent import ParsedIntent, parse_intent
from mandateguard.discovery.schema import (
    DISCOVERY_TRUST_TIER,
    DiscoveryProduct,
    DiscoverySchemaError,
    SCHEMA_VERSION,
)
from mandateguard.discovery.trust import (
    AdvisorySignal,
    BOUNDARY_STATEMENT,
    DISCOVERY_ONLY_STAGES,
    TrustBoundaryViolation,
    assert_advisory_only,
    boundary_declaration,
)

__all__ = [
    "AdvisorySignal",
    "BOUNDARY_STATEMENT",
    "CatalogUnavailableError",
    "DISCOVERY_ONLY_STAGES",
    "DISCOVERY_TRUST_TIER",
    "DiscoveryCatalog",
    "DiscoveryProduct",
    "DiscoverySchemaError",
    "ParsedIntent",
    "SCHEMA_VERSION",
    "TrustBoundaryViolation",
    "assert_advisory_only",
    "boundary_declaration",
    "load_catalog",
    "parse_intent",
]
