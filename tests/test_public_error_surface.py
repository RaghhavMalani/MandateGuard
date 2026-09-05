"""Public errors name what failed, never where it lives.

An HTTP body from this server reaches anyone who can open the demo. A filesystem
path in it is deployment topology - the build machine's drive letter, the
container's layout, the checkout's location - handed to a stranger by an error
message. The operator's log is where that detail belongs.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from mandateguard.discovery.search import (
    DISCOVERY_ARTIFACT_UNAVAILABLE,
    PUBLIC_UNAVAILABLE_REASON,
    try_load,
)
from mandateguard.product.http import create_server
from mandateguard.product.service import CommerceLabService


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

#: Anything that would tell a visitor where this process is running.
PATH_SHAPES = (
    re.compile(r"[A-Za-z]:\\"),        # a Windows drive letter
    re.compile(r"(?<![\w:])/(?:app|tmp|home|usr|var|opt|root)(?:/|\b)"),
    re.compile(r"\\\\"),                # a UNC prefix
)


def assert_no_path_leak(text: str) -> None:
    """No absolute path, in any of the shapes this deployment could produce.

    The repository's *directory name* is deliberately not checked: it happens to
    be the product's name, which belongs in prose. What must never appear is a
    rooted path.
    """

    assert str(REPOSITORY_ROOT) not in text
    assert REPOSITORY_ROOT.as_posix() not in text
    for shape in PATH_SHAPES:
        found = shape.search(text)
        assert found is None, f"{shape.pattern!r} matched {found.group(0)!r}"


@pytest.fixture(scope="module")
def missing_artifact_service(tmp_path_factory):
    """A service whose discovery artifacts are intentionally absent.

    Everything else the server needs is present, so this isolates the one
    failure being tested: the discovery catalog and its indexes did not ship.
    """

    root = tmp_path_factory.mktemp("no-artifacts-here")
    shutil.copytree(REPOSITORY_ROOT / "fixtures", root / "fixtures")
    # data/ is deliberately never created.
    assert not (root / "data").exists()
    service = CommerceLabService(repository_root=root)
    assert service.discovery.available is False
    try:
        yield service
    finally:
        service.close()


@pytest.fixture(scope="module")
def missing_artifact_server(missing_artifact_service):
    server = create_server(host="127.0.0.1", port=0, service=missing_artifact_service)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()


requires_catalog = pytest.mark.skipif(
    not (REPOSITORY_ROOT / "data" / "processed" / "discovery_catalog.jsonl.gz").exists(),
    reason="the discovery catalog is not built in this checkout",
)


@pytest.fixture(scope="module")
def working_server():
    """The ordinary deployment: catalog present, artifacts present."""

    server = create_server(host="127.0.0.1", port=0)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        server.service.close()


def _post(base: str, path: str, body: dict):
    request = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _get(base: str, path: str):
    with urllib.request.urlopen(base + path, timeout=30) as response:
        return json.load(response)


# --------------------------------------------------------------------------
# The loader itself
# --------------------------------------------------------------------------


def test_a_missing_catalog_yields_the_public_reason_and_no_path(tmp_path: Path) -> None:
    engine, reason = try_load(
        processed_dir=tmp_path / "processed", models_dir=tmp_path / "models"
    )
    assert engine is None
    assert reason == PUBLIC_UNAVAILABLE_REASON
    assert_no_path_leak(reason)
    assert str(tmp_path) not in reason


def test_a_missing_index_yields_the_public_reason_and_no_path(tmp_path: Path) -> None:
    """The catalog is present; its indexes are not."""

    processed = REPOSITORY_ROOT / "data" / "processed"
    if not (processed / "discovery_catalog.jsonl.gz").exists():
        pytest.skip("the discovery catalog is not built in this checkout")
    engine, reason = try_load(processed_dir=processed, models_dir=tmp_path / "models")
    assert engine is None
    assert reason == PUBLIC_UNAVAILABLE_REASON
    assert_no_path_leak(reason)


# --------------------------------------------------------------------------
# The HTTP surface
# --------------------------------------------------------------------------


def test_the_config_endpoint_reports_unavailable_without_a_path(
    missing_artifact_server: str,
) -> None:
    config = _get(missing_artifact_server, "/api/config")
    discovery = config["discovery"]
    assert discovery["available"] is False
    assert_no_path_leak(json.dumps(config))


@pytest.mark.parametrize(
    ("endpoint", "body"),
    [
        ("/api/discovery/search", {"intent": "a desk lamp under Rs 2000", "top_k": 3}),
        (
            "/api/discovery/select",
            {"intent": "a desk lamp under Rs 2000", "catalog_product_id": "flipkart.abc"},
        ),
    ],
)
def test_a_discovery_endpoint_returns_a_generic_code_when_artifacts_are_absent(
    missing_artifact_server: str, endpoint: str, body: dict
) -> None:
    with pytest.raises(urllib.error.HTTPError) as raised:
        _post(missing_artifact_server, endpoint, body)
    assert raised.value.code == 503
    payload = json.loads(raised.value.read().decode("utf-8"))
    assert payload["error"]["code"] == DISCOVERY_ARTIFACT_UNAVAILABLE
    assert_no_path_leak(json.dumps(payload))


def test_the_generic_code_is_the_only_discovery_unavailability_code() -> None:
    """One code, so a client cannot distinguish "which file" from the response."""

    text = (REPOSITORY_ROOT / "src" / "mandateguard" / "product" / "http.py").read_text(
        encoding="utf-8"
    )
    assert "DISCOVERY_UNAVAILABLE" not in text.replace(
        DISCOVERY_ARTIFACT_UNAVAILABLE, ""
    )


@requires_catalog
def test_an_invalid_budget_is_a_bad_request_that_leaks_nothing(
    working_server: str,
) -> None:
    """Against a fully working deployment, so the money path is what answers."""

    with pytest.raises(urllib.error.HTTPError) as raised:
        _post(
            working_server,
            "/api/discovery/search",
            {"intent": "Buy the Field Notebook Set under -₹4000.", "top_k": 3},
        )
    assert raised.value.code == 400
    payload = json.loads(raised.value.read().decode("utf-8"))
    assert payload["error"]["code"] == "INVALID_MONETARY_CONSTRAINT"
    assert_no_path_leak(json.dumps(payload))


@requires_catalog
def test_an_invalid_budget_run_is_refused_at_the_http_boundary(
    working_server: str,
) -> None:
    """No run is created, so no capability and no adapter call can follow."""

    with pytest.raises(urllib.error.HTTPError) as raised:
        _post(
            working_server,
            "/api/runs",
            {
                "intent": "Buy the Field Notebook Set under -₹4000. No subscriptions.",
                "mode": "offline",
                "preset_id": None,
                "request_id": "http-negative-budget-regression",
            },
        )
    assert raised.value.code == 400
    payload = json.loads(raised.value.read().decode("utf-8"))
    assert payload["error"]["code"] == "INVALID_MONETARY_CONSTRAINT"
    assert_no_path_leak(json.dumps(payload))


# --------------------------------------------------------------------------
# Committed artifacts must not carry build-machine paths either
# --------------------------------------------------------------------------


def test_the_training_report_records_a_repository_relative_path() -> None:
    """`training_report.json` ships inside the public image."""

    report_path = REPOSITORY_ROOT / "data" / "models" / "training_report.json"
    if not report_path.exists():
        pytest.skip("the models are not built in this checkout")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    recorded = report["category_classifier"]["confusion_matrix_artifact"]
    assert recorded == "data/models/category_confusion.json"
    assert_no_path_leak(report_path.read_text(encoding="utf-8"))


def test_the_public_discovery_reports_carry_no_build_machine_paths() -> None:
    directory = REPOSITORY_ROOT / "artifacts" / "engineering" / "discovery"
    if not directory.exists():
        pytest.skip("the discovery reports are not built in this checkout")
    for path in sorted(directory.glob("*.json")):
        assert_no_path_leak(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# What the server volunteers about itself
#
# None of these is exploitable on its own. Each one narrows the search for
# somebody who is looking: an interpreter build with known advisories, the
# exact names to try in a misconfigured environment, the host a benchmark was
# taken on. They are cheap to withhold and there is no reason to publish them.
# --------------------------------------------------------------------------


def test_the_server_header_names_the_product_and_not_its_interpreter(
    working_server: str,
) -> None:
    """The stdlib default is "BaseHTTP/0.6 Python/3.12.14". This is not that."""

    with urllib.request.urlopen(working_server + "/api/config", timeout=30) as response:
        server_header = response.headers.get("Server", "")
    assert server_header == "MandateGuard"
    assert "Python" not in server_header
    assert "BaseHTTP" not in server_header


def test_an_error_response_carries_the_same_server_header(working_server: str) -> None:
    """A 404 is generated by a different code path, and leaks the same way."""

    try:
        urllib.request.urlopen(working_server + "/api/there-is-no-such-route", timeout=30)
    except urllib.error.HTTPError as error:
        server_header = error.headers.get("Server", "")
    else:  # pragma: no cover - the route genuinely must not exist
        raise AssertionError("the unknown route was served")
    assert server_header == "MandateGuard"
    assert "Python" not in server_header


def test_the_public_configuration_never_names_a_server_environment_variable(
    working_server: str,
) -> None:
    """Whether live mode is configured is public. What it is keyed on is not."""

    body = json.dumps(_get(working_server, "/api/config"))
    for name in (
        "OPENAI_API_KEY",
        "MANDATEGUARD_SEMANTIC_MODEL",
        "RAZORPAY_KEY_ID",
        "RAZORPAY_KEY_SECRET",
        "MANDATEGUARD_EXECUTION_HMAC_KEY",
    ):
        assert name not in body, f"{name} is named in the public configuration"


def test_the_redacted_live_configuration_still_says_whether_it_is_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Redaction must not cost the reader the fact they came for."""

    service = CommerceLabService(state_dir=tmp_path / "state")
    try:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("MANDATEGUARD_SEMANTIC_MODEL", raising=False)
        monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
        monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
        monkeypatch.delenv("MANDATEGUARD_EXECUTION_HMAC_KEY", raising=False)

        public = service.live_configuration(public=True)
        internal = service.live_configuration()

        assert public["available"] is False
        assert public["available"] == internal["available"]
        # The browser only reads the length of this list, so redacting the
        # names costs the interface nothing.
        assert public["missing_configuration"] == ["SERVER_CONFIGURATION"]
        assert len(internal["missing_configuration"]) > 1
        assert "OPENAI_API_KEY" in internal["missing_configuration"]
    finally:
        service.close()


def test_the_scale_surface_does_not_publish_the_machine_it_was_measured_on(
    working_server: str,
) -> None:
    """A benchmark's OS build, CPU model and interpreter version are the host."""

    scale = _get(working_server, "/api/config").get("system_scale") or {}
    assert "environment" not in scale
    body = json.dumps(scale)
    for shape in (
        re.compile(r"Windows-\d"),
        re.compile(r"Linux-\d"),
        re.compile(r"\bIntel64\b"),
        re.compile(r"\bAMD64\b"),
        re.compile(r"\bx86_64\b"),
        re.compile(r"CPython"),
        re.compile(r"\b3\.1[0-9]\.\d+\b"),
    ):
        assert not shape.search(body), f"{shape.pattern} is published on the scale surface"


def test_the_authorization_scale_figure_is_measured_and_qualified(
    working_server: str,
) -> None:
    """The deployed page reported 0. Nothing measured zero; the key was absent.

    A figure this surface publishes has to come from the measured primary rung
    of the frozen benchmark, and has to arrive with the qualification that makes
    it honest - synthetic cases, one process, one machine.
    """

    scale = _get(working_server, "/api/config").get("system_scale") or {}
    authorization = scale.get("authorization_scale")
    assert authorization is not None, "the scale surface carries no authorization figure"
    assert authorization["available"] is True
    assert authorization["cases"] == 25_000
    assert authorization["target_invariant_agreement"] == authorization["cases"]
    assert "one process, one machine" in authorization["scope"]
    assert authorization["source"].startswith("artifacts/")
    assert authorization["freeze_source"].startswith("data/eval/")
    assert_no_path_leak(json.dumps(authorization))
