"""Write the sandbox world manifest that the determinism test compares against.

    python scripts/freeze_judge_sandbox.py

The manifest records counts and digests, never outcomes. Regenerating it after a
change to the construction vocabulary is expected; regenerating it to make a
failing determinism test pass is how a world quietly stops being reproducible,
so the version in `mandateguard.sandbox.templates` should change with it.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from mandateguard.sandbox.store import SANDBOX_SNAPSHOT_ID  # noqa: E402
from mandateguard.sandbox.universe import (  # noqa: E402
    build_universe,
    universe_manifest,
)


FREEZE_PATH = (
    REPOSITORY_ROOT / "data" / "eval" / "judge-playground" / "SANDBOX_FREEZE.json"
)


def main() -> int:
    universe = build_universe()
    manifest = universe_manifest(universe)
    manifest["snapshot_id"] = SANDBOX_SNAPSHOT_ID
    FREEZE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FREEZE_PATH.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"world           {manifest['world_version']}")
    print(f"products        {manifest['product_count']}")
    print(f"evidence        {manifest['evidence_count']}")
    print(f"merchants       {manifest['merchant_count']}")
    print(f"categories      {manifest['category_count']}")
    print(f"products sha256 {manifest['products_sha256']}")
    print(f"evidence sha256 {manifest['evidence_sha256']}")
    print(f"frozen to       {FREEZE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
