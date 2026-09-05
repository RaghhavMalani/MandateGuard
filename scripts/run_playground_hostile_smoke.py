"""Classify the fixed hostile Playground prompts against the v3 sandbox.

This is a retrieval test only. It records whether the direct result is the
requested product family; it never treats retrieval as authorization evidence.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mandateguard.sandbox.intent import read_intent  # noqa: E402
from mandateguard.sandbox.search import SandboxSearch  # noqa: E402
from mandateguard.sandbox.universe import build_universe  # noqa: E402


PROMPTS: tuple[tuple[str, str], ...] = (
    ("water bottle under 1000", "drinkware-water-bottles"),
    ("winter jacket below 5000", "apparel-jackets"),
    ("coffee maker under 4000", "coffee-makers"),
    ("printer below 12000", "printers"),
    ("hard disk under 6000", "computer-storage"),
    ("board game below 2000", "toys-board-games"),
    ("floor cleaner under 800", "cleaning-products"),
    ("power bank under 2000", "mobile-power"),
    ("wireless headphones under 5000", "audio-headphones"),
    ("desk lamp below 2000", "lighting-desk-lamps"),
    ("college backpack under 3000", "bags-backpacks"),
    ("running shoes below 6000", "footwear-running"),
    ("camera under 30000", "cameras"),
    ("smartwatch below 10000", "wearables-smartwatches"),
    ("keyboard under 7000", "computing-keyboards"),
    ("office chair below 15000", "furniture-office-chairs"),
)

OUTPUT = ROOT / "data" / "eval" / "judge-playground-v3" / "HOSTILE_SMOKE_REPORT.json"


def main() -> int:
    universe = build_universe()
    search = SandboxSearch(universe)
    outcomes: list[dict[str, object]] = []
    for prompt, expected_category in PROMPTS:
        result = search.search(read_intent(prompt), limit=5)
        returned = [item.product.category_id for item in result.candidates]
        if not returned:
            classification = "NO MATCH"
        elif returned[0] == expected_category:
            classification = "CORRECT DIRECT MATCH"
        else:
            classification = "WRONG MATCH"
        outcomes.append(
            {
                "prompt": prompt,
                "expected_category": expected_category,
                "classification": classification,
                "returned_category_at_1": returned[0] if returned else None,
                "returned_categories_at_5": returned,
            }
        )

    wrong = sum(item["classification"] == "WRONG MATCH" for item in outcomes)
    report = {
        "scope": "FIXED_HOSTILE_RETRIEVAL_SMOKE_TEST_AUTHORITY_NONE",
        "world_version": universe.world_version,
        "prompt_count": len(outcomes),
        "correct_direct_match_count": sum(
            item["classification"] == "CORRECT DIRECT MATCH" for item in outcomes
        ),
        "no_match_count": sum(item["classification"] == "NO MATCH" for item in outcomes),
        "wrong_match_count": wrong,
        "wrong_match_rate": round(wrong / len(outcomes), 4),
        "external_network_calls": 0,
        "outcomes": outcomes,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["wrong_match_rate"] <= 0.05 else 1


if __name__ == "__main__":
    raise SystemExit(main())
