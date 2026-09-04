"""What the public image actually contains, and what it must never contain.

The failure this guards against is specific: the interface advertised SYSTEM
SCALE and MODEL QUALITY numbers, `.dockerignore` excluded every file those
surfaces read, and the deployed container therefore rendered "no measurement has
been recorded" under headings that promised measurements.

The Docker daemon is not available in this environment, so **image construction
itself is not measured here**. What is measured is the build context - which
files `.dockerignore` admits - and, more usefully, a container-equivalent
runtime: a temporary tree containing only the admitted files, from which the
public evidence is loaded for real.
"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
import shutil

import pytest

from mandateguard.product.scale_evidence import (
    ANOMALY_REPORT,
    ARTIFACT_DIR,
    RETRIEVAL_REPORT,
    SCALE_REPORT,
    TRAINING_REPORT,
    model_quality,
    system_scale,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DOCKERIGNORE = REPOSITORY_ROOT / ".dockerignore"
DOCKERFILE = REPOSITORY_ROOT / "Dockerfile"

#: Exactly the files `scale_evidence` opens. Derived from its own constants, so
#: adding a report there and forgetting the image fails this test.
REQUIRED_IN_IMAGE = (
    f"{ARTIFACT_DIR.as_posix()}/{SCALE_REPORT}",
    f"{ARTIFACT_DIR.as_posix()}/{RETRIEVAL_REPORT}",
    f"{ARTIFACT_DIR.as_posix()}/{ANOMALY_REPORT}",
    f"data/models/{TRAINING_REPORT}",
    "data/processed/discovery_catalog.jsonl.gz",
    "data/processed/discovery_catalog.manifest.json",
    "data/models/lexical_index.mgdx",
    "data/models/embedding_index.mgdx",
    "data/models/category_classifier.mgdx",
    # CC BY-SA 4.0 attribution travels with the derived bytes.
    "data/provenance/flipkart-products/LICENSE_NOTICE.md",
    "data/provenance/flipkart-products/SOURCE.md",
)

#: Things whose presence in a public image would be a mistake of a different
#: kind: secrets, raw upstream data, the training workspace, and the tests.
FORBIDDEN_IN_IMAGE = (
    ".env",
    ".env.local",
    ".git/config",
    "data/import/flipkart-raw.zip",
    "data/eval/retrieval_queries.json",
    "data/eval/category_split.frozen.json",
    "data/eval/category_split.grouped.frozen.json",
    "tests/test_discovery_retrieval.py",
    "tests/__init__.py",
    "docs/DISCOVERY_SCALE.md",
    "benchmark/PROTOCOL.md",
    "schemas/mandate.schema.json",
    "requirements-train.txt",
    "artifacts/engineering/int2/retrieval_summary.json",
    "artifacts/engineering/int3/subset_plan.jsonl",
    "artifacts/engineering/discovery/training_notes.md",
    ".venv/Lib/site-packages/numpy/__init__.py",
    "src/mandateguard/__pycache__/__init__.cpython-312.pyc",
)


def _patterns() -> list[str]:
    return [
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _segment_match(pattern: str, path: str) -> bool:
    """Docker path matching: `**` spans segments, everything else is per-segment."""

    pattern_parts = PurePosixPath(pattern).parts
    path_parts = PurePosixPath(path).parts

    def walk(pattern_index: int, path_index: int) -> bool:
        while pattern_index < len(pattern_parts):
            token = pattern_parts[pattern_index]
            if token == "**":
                if pattern_index + 1 == len(pattern_parts):
                    return True
                for skip in range(path_index, len(path_parts) + 1):
                    if walk(pattern_index + 1, skip):
                        return True
                return False
            if path_index >= len(path_parts):
                return False
            if not fnmatch(path_parts[path_index], token):
                return False
            pattern_index += 1
            path_index += 1
        return path_index == len(path_parts)

    return walk(0, 0)


def _matches(pattern: str, path: str) -> bool:
    """True if the pattern names the path or any directory above it.

    Docker excludes a whole subtree when a pattern names one of its parents,
    which is why re-including a single file under an excluded directory needs
    each parent re-included too.
    """

    if _segment_match(pattern, path):
        return True
    parts = PurePosixPath(path).parts
    for depth in range(1, len(parts)):
        if _segment_match(pattern, "/".join(parts[:depth])):
            return True
    return False


def is_in_build_context(path: str, patterns: list[str] | None = None) -> bool:
    """Last matching pattern wins, `!` re-includes. Docker's documented rule."""

    excluded = False
    for pattern in patterns if patterns is not None else _patterns():
        if pattern.startswith("!"):
            if _matches(pattern[1:], path):
                excluded = False
        elif _matches(pattern, path):
            excluded = True
    return not excluded


# --------------------------------------------------------------------------
# The matcher itself, so a wrong answer below is a real finding
# --------------------------------------------------------------------------


def test_the_build_context_matcher_behaves_like_docker() -> None:
    patterns = ["data/*", "!data/models/", "!data/models/**", "data/models/secret.json"]
    assert is_in_build_context("data/models/index.mgdx", patterns) is True
    assert is_in_build_context("data/models/secret.json", patterns) is False
    assert is_in_build_context("data/import/raw.zip", patterns) is False
    assert is_in_build_context("src/app.py", patterns) is True


# --------------------------------------------------------------------------
# The build context
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", REQUIRED_IN_IMAGE)
def test_a_file_the_running_server_reads_is_in_the_build_context(path: str) -> None:
    assert is_in_build_context(path), (
        f"{path} is excluded by .dockerignore, so the deployed image cannot read "
        "it. The interface would then advertise a measurement it does not have."
    )


@pytest.mark.parametrize("path", FORBIDDEN_IN_IMAGE)
def test_a_file_that_must_not_ship_is_excluded(path: str) -> None:
    assert not is_in_build_context(path), f"{path} would be copied into the public image"


def test_the_required_reports_exist_in_the_repository() -> None:
    """An allowlisted file that does not exist ships nothing."""

    for path in REQUIRED_IN_IMAGE:
        assert (REPOSITORY_ROOT / path).exists(), path


def test_the_dockerfile_copies_the_measured_reports() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "COPY artifacts/engineering/discovery/" in text
    assert "COPY data/models/" in text
    assert "COPY data/processed/" in text
    assert "COPY data/provenance/" in text
    # The raw archive and the training workspace are never copied.
    assert "COPY data/import" not in text
    assert "COPY tests" not in text
    assert "requirements-train" not in text.replace(
        "# scikit-learn and NumPy are needed to *build* the artifacts in data/models/\n"
        "# (see requirements-train.txt) and are deliberately absent here.",
        "",
    )


def test_no_artifact_outside_the_discovery_reports_enters_the_context() -> None:
    """The allowlist is by file, not by directory."""

    for path in (
        "artifacts/engineering/int2/cache_experiment.json",
        "artifacts/engineering/int3/model_feature_manifest.json",
        "artifacts/engineering/semantic_mvp/anything.json",
        "artifacts/engineering/resolve_recovery/anything.json",
        "artifacts/engineering/discovery/some_new_unlisted_file.json",
    ):
        assert not is_in_build_context(path), path


# --------------------------------------------------------------------------
# A container-equivalent runtime, built from only what the context admits
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def container_root(tmp_path_factory) -> Path:
    """A tree containing only the files the build context admits and COPY takes."""

    root = tmp_path_factory.mktemp("container-equivalent")
    for relative in REQUIRED_IN_IMAGE:
        source = REPOSITORY_ROOT / relative
        if not source.exists():
            continue
        assert is_in_build_context(relative), relative
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return root


def test_the_container_equivalent_runtime_loads_system_scale(container_root: Path) -> None:
    scale = system_scale(
        repository_root=container_root, models_dir=container_root / "data" / "models"
    )
    assert scale["available"] is True, scale.get("reason")
    assert scale["kind"] == "SYSTEM_SCALE"
    assert scale["catalog_listings"] and scale["catalog_listings"] > 10_000
    # Both latency families are present and distinct.
    assert scale["retrieval_p50_ms"] is not None
    assert scale["request_p50_ms"] is not None
    assert scale["retrieval_p95_ms"] is not None
    assert scale["request_p95_ms"] is not None
    assert scale["retrieval_p99_ms"] is not None
    assert scale["request_p99_ms"] is not None


def test_the_container_equivalent_runtime_loads_model_quality(
    container_root: Path,
) -> None:
    quality = model_quality(
        repository_root=container_root, models_dir=container_root / "data" / "models"
    )
    assert quality["available"] is True, quality.get("reason")
    assert quality["kind"] == "MODEL_QUALITY"
    assert quality["classifier"]["macro_f1"] is not None
    assert quality["classifier"]["evaluation"] == "grouped_product_family_holdout"
    assert quality["retrieval"]["recall_at_10"] is not None
    assert quality["retrieval"]["distinct_title_at_8"] is not None
    assert quality["negative_results"]


def test_an_empty_runtime_reports_unavailable_rather_than_a_remembered_number(
    tmp_path: Path,
) -> None:
    """The other half of the contract: absent artifacts must say so."""

    scale = system_scale(repository_root=tmp_path, models_dir=tmp_path / "models")
    quality = model_quality(repository_root=tmp_path, models_dir=tmp_path / "models")
    assert scale["available"] is False
    assert quality["available"] is False
    # And the reason names no path.
    for payload in (scale, quality):
        assert str(tmp_path) not in payload["reason"]
