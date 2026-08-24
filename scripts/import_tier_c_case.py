"""Validate one proposed Tier C case and emit its canonical corpus line.

    python scripts/import_tier_c_case.py proposed_case.json
    cat proposed_case.json | python scripts/import_tier_c_case.py -

The importer validates typed fields, provenance metadata, family and split,
primary adjudication state, and the semantic constraint requirement; computes
the canonical content digest; and refuses a duplicate case ID or duplicate
content digest against the existing corpus.

It never calls a detector, never calls a model, and never assigns a ground
truth. A proposed case that no human has adjudicated is rejected, not labelled.

By default the validated record is printed to stdout and nothing is written, so
appending a case to the corpus stays an explicit, reviewable act.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from mandateguard.benchmark.tier_c.codec import case_record_line  # noqa: E402
from mandateguard.benchmark.tier_c.corpus import (  # noqa: E402
    CORPUS_ROOT,
    FAMILY_FILES,
    import_case,
    load_corpus,
)
from mandateguard.benchmark.tier_c.models import (  # noqa: E402
    FAMILY_SPLIT,
    TierCCaseError,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", help="path to a proposed case JSON file, or - for stdin")
    parser.add_argument("--root", type=Path, default=REPOSITORY_ROOT / CORPUS_ROOT)
    args = parser.parse_args(argv)

    raw = (
        sys.stdin.read()
        if args.case == "-"
        else Path(args.case).read_text(encoding="utf-8")
    )
    try:
        record = json.loads(raw)
    except ValueError as error:
        print(f"rejected: proposed case is not valid JSON ({error})", file=sys.stderr)
        return 1
    if not isinstance(record, dict):
        print("rejected: proposed case must be a JSON object", file=sys.stderr)
        return 1

    family_id = record.get("family_id")
    if family_id not in FAMILY_SPLIT:
        print(f"rejected: {family_id!r} is not a Tier C family", file=sys.stderr)
        return 1
    corpus = load_corpus(args.root, FAMILY_SPLIT[family_id])

    try:
        case, digest = import_case(record, corpus)
    except TierCCaseError as error:
        print(f"rejected: {error}", file=sys.stderr)
        return 1

    print(
        f"accepted {case.case_id} "
        f"family={case.family_id} split={case.split.value} "
        f"ground_truth={case.ground_truth.value} "
        f"provenance={case.provenance.value} status={case.status.value}",
        file=sys.stderr,
    )
    print(f"case_content_sha256={digest}", file=sys.stderr)
    print(
        f"append to: {args.root / FAMILY_FILES[case.family_id]}",
        file=sys.stderr,
    )
    print(case_record_line(case))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
