"""Validate the frozen Resolve preregistration structurally, without outcomes.

This script decodes the frozen plan and freeze record, loads every world
fixture, rebuilds the shared trusted source registry, and re-derives every
committed hash. It authorizes nothing, acquires nothing, and calls no model or
payment provider, so it is safe to run before and after the freeze.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPOSITORY_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from mandateguard.engineering.resolve_eval.preregistration import (  # noqa: E402
    PreregistrationError,
    structural_report,
)


def main() -> int:
    try:
        report = structural_report(REPOSITORY_ROOT)
    except PreregistrationError as error:
        print(f"PREREGISTRATION INVALID: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
