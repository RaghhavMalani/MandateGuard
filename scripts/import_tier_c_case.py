"""Validate one proposed Tier C case and emit its canonical corpus line.

    python scripts/import_tier_c_case.py proposed_case.json
    cat proposed_case.json | python scripts/import_tier_c_case.py -

The importer validates typed fields, provenance metadata, family and split,
primary adjudication state, and the semantic constraint requirement; computes
the canonical content digest; and refuses a duplicate case ID or duplicate
content digest against the existing corpus. An externally adapted development
case is also refused until its registered source version and selection time are
pinned in ``benchmark/tier_c/development_sources.json``.

It never calls a detector, never calls a model, and never assigns a ground
truth. A proposed case that no human has adjudicated is rejected, not labelled.

By default the validated record is printed to stdout and nothing is written, so
appending a case to the corpus stays an explicit, reviewable act.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_REGISTRY_PATH = (
    REPOSITORY_ROOT / "benchmark" / "tier_c" / "development_sources.json"
)
_EXTERNAL_PROVENANCE = "external_defensive_corpus_adapted"
_COMMIT_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
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


def _utc_timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise TierCCaseError(f"{name} must be a populated UTC RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise TierCCaseError(
            f"{name} must be a populated UTC RFC 3339 timestamp"
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise TierCCaseError(f"{name} must use a UTC offset")
    return parsed


def require_registered_external_source_pin(record: dict, registry_path: Path) -> None:
    """Refuse an external case until its registered source is immutably pinned."""

    if record.get("provenance") != _EXTERNAL_PROVENANCE:
        return
    family_id = record.get("family_id")
    origin = record.get("provenance_origin")
    if not isinstance(origin, dict):
        raise TierCCaseError("external provenance_origin must be a JSON object")
    source_name = origin.get("source_name")
    if not isinstance(source_name, str) or not source_name:
        raise TierCCaseError("external provenance_origin.source_name is required")

    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise TierCCaseError(
            f"external source registry cannot be read: {registry_path}"
        ) from error
    if not isinstance(registry, dict) or not isinstance(registry.get("sources"), list):
        raise TierCCaseError("external source registry has an invalid structure")

    matches: list[dict] = []
    for source in registry["sources"]:
        if not isinstance(source, dict) or source.get("case_source_name") != source_name:
            continue
        allocations = source.get("intended_development_families")
        if not isinstance(allocations, list):
            continue
        if any(
            isinstance(allocation, dict)
            and allocation.get("family_id") == family_id
            for allocation in allocations
        ):
            matches.append(source)
    if len(matches) != 1:
        raise TierCCaseError(
            f"external source {source_name!r} is not uniquely registered for "
            f"family {family_id!r}"
        )

    source = matches[0]
    selected_commit_sha = source.get("selected_commit_sha")
    registry_selected_at = source.get("source_selected_at")
    if (
        not isinstance(selected_commit_sha, str)
        or not _COMMIT_SHA_RE.fullmatch(selected_commit_sha)
        or registry_selected_at is None
    ):
        raise TierCCaseError(
            f"external source {source_name!r} is not pinned; populate "
            "selected_commit_sha and source_selected_at in the development "
            "source registry before finalizing its first case"
        )

    pinned_at = _utc_timestamp(
        registry_selected_at, "registry source_selected_at"
    )
    if origin.get("source_version") != selected_commit_sha:
        raise TierCCaseError(
            "external provenance_origin.source_version must equal the registered "
            f"selected_commit_sha {selected_commit_sha}"
        )
    case_selected_at = _utc_timestamp(
        origin.get("source_selected_at"),
        "provenance_origin.source_selected_at",
    )
    if case_selected_at < pinned_at:
        raise TierCCaseError(
            "external case source_selected_at predates the registered source pin"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", help="path to a proposed case JSON file, or - for stdin")
    parser.add_argument("--root", type=Path, default=REPOSITORY_ROOT / CORPUS_ROOT)
    parser.add_argument(
        "--source-registry",
        type=Path,
        default=SOURCE_REGISTRY_PATH,
        help="development external-source registry",
    )
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
    try:
        require_registered_external_source_pin(record, args.source_registry)
    except TierCCaseError as error:
        print(f"rejected: {error}", file=sys.stderr)
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
