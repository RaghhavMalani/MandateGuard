"""Recompute the deterministic second-review selection for one split.

The selection is a pure function of the corpus content digests (protocol 5.1),
so anyone - including an external auditor - can run this and obtain byte-equal
output. There is no seed, no sampling option, and no way to re-draw a sample:
this script accepts no argument that could change which cases are selected.

    python scripts/select_tier_c_second_review.py --split dev

At D8-A the corpus is empty and the selection is empty.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from mandateguard.benchmark.tier_c.corpus import CORPUS_ROOT, load_corpus  # noqa: E402
from mandateguard.benchmark.tier_c.models import Split  # noqa: E402
from mandateguard.benchmark.tier_c.second_review import (  # noqa: E402
    candidate_from_case,
    second_review_rank,
    select_second_review,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=["dev", "held_out"], default="dev")
    parser.add_argument("--root", type=Path, default=REPOSITORY_ROOT / CORPUS_ROOT)
    args = parser.parse_args(argv)

    corpus = load_corpus(args.root, Split(args.split))
    candidates = tuple(
        candidate_from_case(case, corpus.content_hashes[case.case_id])
        for case in corpus.cases
        if case.ground_truth is not None and case.exclusion is None
    )
    selection = select_second_review(candidates)

    print(f"corpus root: {args.root}")
    print(f"split: {args.split}")
    print(f"strata: {len(selection.strata)}")
    for stratum in selection.strata:
        print(
            f"  {stratum.family_id}/{stratum.ground_truth.value}/"
            f"{stratum.provenance.value}: size={stratum.stratum_size} "
            f"required={stratum.required_count} "
            f"ambiguous={len(stratum.ambiguous_additions)}"
        )
        for case_id in stratum.required_second_review:
            digest = corpus.content_hashes[case_id]
            print(f"    {case_id} rank={second_review_rank(digest)}")
    print(f"total requiring second review: {selection.total_required}")
    if not candidates:
        print(
            "note: no adjudicated Tier C case exists yet; the selection is "
            "empty by construction."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
