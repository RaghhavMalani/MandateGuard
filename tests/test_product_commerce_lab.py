from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
from threading import Thread
from typing import Iterator
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from mandateguard.product.http import CommerceLabHTTPServer, resolve_bind_address
from mandateguard.product.service import CommerceLabService, DEMO_PRESETS


PRESETS = {item["id"]: item for item in DEMO_PRESETS}


@pytest.fixture
def service(tmp_path: Path) -> Iterator[CommerceLabService]:
    instance = CommerceLabService(state_dir=tmp_path / "state")
    try:
        yield instance
    finally:
        instance.close()


def run_preset(service: CommerceLabService, preset_id: str, **kwargs: object) -> dict:
    return service.run_sync(
        user_intent=PRESETS[preset_id]["intent"],
        preset_id=preset_id,
        **kwargs,
    )


def test_safe_purchase_allows_and_creates_one_offline_order(
    service: CommerceLabService,
) -> None:
    snapshot = run_preset(service, "safe")

    assert snapshot["state"] == "COMPLETE"
    assert snapshot["result"]["decision"] == "ALLOW"
    assert snapshot["result"]["authorization"]["final_controller"] == "ALLOW"
    assert snapshot["result"]["execution"]["status"] == "ORDER_CREATED"
    assert snapshot["result"]["execution"]["razorpay_calls"] == 1
    assert snapshot["result"]["execution"]["external_network_calls"] == 0
    assert snapshot["result"]["execution"]["capability"] == {
        "signature_verified": True,
        "transaction_bound": True,
        "request_bound": True,
        "merchant_bound": True,
        "expiry_valid": True,
        "single_use": True,
    }
    assert [call["name"] for call in snapshot["result"]["buyer"]["tool_calls"]] == [
        "search_catalog",
        "get_product",
        "get_merchant_evidence",
        "propose_purchase",
    ]


@pytest.mark.parametrize(
    ("preset_id", "decision"),
    (("block", "BLOCK"), ("review", "REVIEW")),
)
def test_non_allow_decisions_never_call_razorpay(
    service: CommerceLabService, preset_id: str, decision: str
) -> None:
    snapshot = run_preset(service, preset_id)

    assert snapshot["state"] == "COMPLETE"
    assert snapshot["result"]["decision"] == decision
    assert snapshot["result"]["execution"]["status"] == "NOT_CALLED"
    assert snapshot["result"]["execution"]["razorpay_calls"] == 0
    assert snapshot["result"]["execution"]["external_network_calls"] == 0
    assert snapshot["result"]["execution"]["order"] is None


def test_no_trusted_evidence_routes_to_review_without_semantic_cache(
    service: CommerceLabService,
) -> None:
    snapshot = run_preset(service, "safe", top_k=1)
    result = snapshot["result"]

    assert result["decision"] == "REVIEW"
    assert result["evidence"]["trusted_evidence_count"] == 0
    assert result["authorization"]["semantic"]["verdict"] == "NOT_EVALUATED"
    assert result["authorization"]["semantic"]["cache"]["status"] == "NOT_USED"
    assert result["execution"]["razorpay_calls"] == 0


def test_identical_semantic_input_moves_from_cache_miss_to_hit(
    service: CommerceLabService,
) -> None:
    first = run_preset(service, "safe", request_id="cache_first_request")
    second = run_preset(service, "safe", request_id="cache_second_request")

    first_cache = first["result"]["authorization"]["semantic"]["cache"]
    second_cache = second["result"]["authorization"]["semantic"]["cache"]
    assert first_cache["status"] == "MISS"
    assert first_cache["write_performed"] is True
    assert second_cache["status"] == "HIT"
    assert second_cache["write_performed"] is False
    assert second_cache["key_prefix"] == first_cache["key_prefix"]


def test_capability_replay_is_rejected_before_an_additional_provider_call(
    service: CommerceLabService,
) -> None:
    snapshot = run_preset(service, "safe")
    replayed = service.replay(snapshot["run_id"])
    replay = replayed["result"]["execution"]["replay"]

    assert replay == {
        "status": "REJECTED_BEFORE_NETWORK",
        "reason": "NONCE_ALREADY_USED",
        "razorpay_additional_calls": 0,
        "external_additional_calls": 0,
    }
    assert replayed["result"]["execution"]["razorpay_calls"] == 1


def test_request_id_deduplicates_double_submission(
    service: CommerceLabService,
) -> None:
    first, first_deduplicated = service.start_run(
        user_intent=PRESETS["safe"]["intent"],
        mode="offline",
        preset_id="safe",
        request_id="same_browser_request",
    )
    second, second_deduplicated = service.start_run(
        user_intent=PRESETS["safe"]["intent"],
        mode="offline",
        preset_id="safe",
        request_id="same_browser_request",
    )

    assert first is second
    assert first_deduplicated is False
    assert second_deduplicated is True
    assert first.completion.wait(20)
    assert first.snapshot()["result"]["execution"]["razorpay_calls"] == 1


def test_request_id_cannot_be_rebound(service: CommerceLabService) -> None:
    service.start_run(
        user_intent=PRESETS["safe"]["intent"],
        mode="offline",
        request_id="bound_request_id",
    )

    with pytest.raises(ValueError, match="already bound"):
        service.start_run(
            user_intent=PRESETS["block"]["intent"],
            mode="offline",
            request_id="bound_request_id",
        )


def test_public_payloads_never_include_secret_values(
    service: CommerceLabService, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinels = {
        "OPENAI_API_KEY": "openai-secret-sentinel",
        "RAZORPAY_KEY_ID": "rzp_test_secret_sentinel",
        "RAZORPAY_KEY_SECRET": "razorpay-secret-sentinel",
        "MANDATEGUARD_EXECUTION_HMAC_KEY": "hmac-secret-sentinel-value-over-32-bytes",
    }
    for name, value in sentinels.items():
        monkeypatch.setenv(name, value)

    snapshot = run_preset(service, "safe")
    serialized = json.dumps(
        {"config": service.public_config(), "snapshot": snapshot},
        sort_keys=True,
    )
    for value in sentinels.values():
        assert value not in serialized


def test_product_bind_address_honors_platform_port_precedence() -> None:
    assert resolve_bind_address(
        {
            "PORT": "9123",
            "MANDATEGUARD_PRODUCT_PORT": "8123",
            "MANDATEGUARD_PRODUCT_HOST": "127.0.0.1",
        }
    ) == ("127.0.0.1", 9123)


def test_product_bind_address_supports_local_and_deployment_fallbacks() -> None:
    assert resolve_bind_address({}) == ("0.0.0.0", 8080)
    assert resolve_bind_address(
        {
            "MANDATEGUARD_PRODUCT_HOST": "127.0.0.1",
            "MANDATEGUARD_PRODUCT_PORT": "8081",
        }
    ) == ("127.0.0.1", 8081)


@contextmanager
def running_server(service: CommerceLabService) -> Iterator[str]:
    server = CommerceLabHTTPServer(("127.0.0.1", 0), service)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def fetch_json(url: str, *, payload: dict | None = None) -> tuple[int, dict, object]:
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers)
    with urlopen(request, timeout=10) as response:
        return response.status, json.load(response), response.headers


def test_http_boundary_serves_ui_config_and_idempotent_runs(
    service: CommerceLabService,
) -> None:
    with running_server(service) as base_url:
        with urlopen(base_url + "/", timeout=10) as response:
            html = response.read().decode("utf-8")
            assert response.status == 200
            assert "The agent decides. MandateGuard verifies." in html
            assert "default-src 'self'" in response.headers["Content-Security-Policy"]

        status, config, headers = fetch_json(base_url + "/api/config")
        assert status == 200
        assert headers["Cache-Control"] == "no-store"
        assert config["safety"]["external_calls_on_page_load"] == 0

        request_payload = {
            "intent": PRESETS["safe"]["intent"],
            "mode": "offline",
            "preset_id": "safe",
            "request_id": "http_double_submit_request",
        }
        first_status, first, _ = fetch_json(
            base_url + "/api/runs", payload=request_payload
        )
        second_status, second, _ = fetch_json(
            base_url + "/api/runs", payload=request_payload
        )
        assert first_status in {200, 202}
        assert second_status in {200, 202}
        assert first["run_id"] == second["run_id"]
        assert first["deduplicated"] is False
        assert second["deduplicated"] is True


def test_http_boundary_binds_all_interfaces_and_keeps_health_reachable(
    service: CommerceLabService,
) -> None:
    server = CommerceLabHTTPServer(("0.0.0.0", 0), service)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert server.server_address[0] == "0.0.0.0"
        port = server.server_address[1]
        status, health, _ = fetch_json(f"http://127.0.0.1:{port}/api/health")
        assert status == 200
        assert health["status"] == "ok"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_boundary_rejects_unknown_fields(service: CommerceLabService) -> None:
    with running_server(service) as base_url:
        request = Request(
            base_url + "/api/runs",
            data=json.dumps(
                {
                    "intent": PRESETS["safe"]["intent"],
                    "mode": "offline",
                    "preset_id": "safe",
                    "request_id": "invalid_schema_request",
                    "untrusted": "field",
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(HTTPError) as caught:
            urlopen(request, timeout=10)

        assert caught.value.code == 400
        payload = json.loads(caught.value.read().decode("utf-8"))
        assert payload["error"]["code"] == "INVALID_REQUEST"
