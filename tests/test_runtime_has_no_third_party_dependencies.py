"""The served runtime must import nothing outside the standard library.

This is what lets the public image install no packages, start in under a second,
and make zero external calls. It is easy to break by accident - one `import
numpy` in a module the server happens to touch - so it is checked rather than
documented.

The training and evaluation packages under `mandateguard.ml` are exempt by
design; the test asserts that nothing served ever imports them.
"""

from __future__ import annotations

import ast
from pathlib import Path
import sys

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src" / "mandateguard"

#: Packages the running server is composed of. `mandateguard.ml` is absent on
#: purpose: it is the offline trainer.
RUNTIME_PACKAGES = (
    "discovery",
    "product",
    "policy",
    "execution",
    "semantic",
    "intelligence",
    "recovery",
    "replay",
    "core",
    "models",
    "audit",
    "evidence",
)

TRAINING_ONLY = {"numpy", "scipy", "sklearn", "pandas", "torch", "joblib"}


def _runtime_modules() -> list[Path]:
    paths: list[Path] = []
    for package in RUNTIME_PACKAGES:
        paths.extend(sorted((SOURCE_ROOT / package).rglob("*.py")))
    return paths


def _top_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


def test_the_runtime_packages_exist() -> None:
    """A renamed package would make the sweep below silently vacuous."""

    for package in RUNTIME_PACKAGES:
        assert (SOURCE_ROOT / package).is_dir(), f"{package} is missing"
    assert len(_runtime_modules()) > 50


def test_no_served_module_imports_a_training_dependency() -> None:
    offenders: list[str] = []
    for path in _runtime_modules():
        for name in _top_level_imports(path):
            if name in TRAINING_ONLY:
                offenders.append(f"{path.relative_to(REPOSITORY_ROOT)} imports {name}")
    assert offenders == [], (
        "the served runtime must stay standard library only; "
        f"these modules would pull a training dependency into the image: {offenders}"
    )


def test_no_served_module_imports_the_offline_trainer() -> None:
    offenders: list[str] = []
    for path in _runtime_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.ImportFrom) and node.module:
                module = node.module
            elif isinstance(node, ast.Import):
                module = next(
                    (
                        alias.name
                        for alias in node.names
                        if alias.name.startswith("mandateguard.ml")
                    ),
                    None,
                )
            if module and module.startswith("mandateguard.ml"):
                offenders.append(str(path.relative_to(REPOSITORY_ROOT)))
    assert offenders == [], f"served modules importing the trainer: {offenders}"


def test_every_third_party_import_in_the_runtime_is_a_declared_optional_one() -> None:
    """`openai` is the one exception, and it is imported lazily inside a branch."""

    allowed = {"mandateguard", "openai"}
    unexpected: dict[str, set[str]] = {}
    for path in _runtime_modules():
        for name in _top_level_imports(path):
            if name in allowed or name in sys.stdlib_module_names:
                continue
            unexpected.setdefault(str(path.relative_to(REPOSITORY_ROOT)), set()).add(name)
    assert unexpected == {}, f"unexpected runtime imports: {unexpected}"


def test_openai_is_only_imported_inside_a_function_body() -> None:
    """A module-level import would make the package mandatory to start."""

    for path in _runtime_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                assert not any(
                    name.startswith("openai") for name in names
                ), f"{path} imports openai at module scope"


@pytest.mark.parametrize(
    "module",
    [
        "mandateguard.discovery.search",
        "mandateguard.discovery.index.hybrid",
        "mandateguard.discovery.classifier",
        "mandateguard.product.service",
        "mandateguard.product.discovery_service",
    ],
)
def test_importing_a_served_module_does_not_pull_in_a_training_dependency(
    module: str,
) -> None:
    """Import it in a subprocess so an already-loaded module cannot hide this."""

    import subprocess

    script = (
        "import sys; "
        f"import {module}; "
        "loaded = {name for name in sys.modules} & "
        f"{TRAINING_ONLY!r}; "
        "print(sorted(loaded))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=REPOSITORY_ROOT,
        env={**dict(__import__("os").environ), "PYTHONPATH": str(REPOSITORY_ROOT / "src")},
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "[]", (
        f"importing {module} loaded training dependencies: {completed.stdout}"
    )
