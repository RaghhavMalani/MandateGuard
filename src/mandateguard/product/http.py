"""Dependency-free HTTP boundary for the MandateGuard commerce lab."""

from __future__ import annotations

from collections import defaultdict, deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
from secrets import token_hex
import sys
from threading import RLock
from time import monotonic
from typing import Any, Mapping
from urllib.parse import urlsplit

from mandateguard.product.service import CommerceLabService


STATIC_ROOT = Path(__file__).resolve().parent / "static"
_RUN_PATH_RE = re.compile(r"^/api/runs/(run_[0-9a-f]{32})$")
_REPLAY_PATH_RE = re.compile(r"^/api/runs/(run_[0-9a-f]{32})/replay$")
_RECOVER_PATH_RE = re.compile(r"^/api/runs/(run_[0-9a-f]{32})/recover$")
_REVOKE_PATH_RE = re.compile(r"^/api/runs/(run_[0-9a-f]{32})/revoke$")
_EXECUTE_PATH_RE = re.compile(r"^/api/runs/(run_[0-9a-f]{32})/execute$")
_MAX_REQUEST_BYTES = 16_384
_DEFAULT_PRODUCT_HOST = "0.0.0.0"
_DEFAULT_PRODUCT_PORT = 8080
_ROUTE_TEMPLATES = (
    (_EXECUTE_PATH_RE, "/api/runs/{run_id}/execute"),
    (_REVOKE_PATH_RE, "/api/runs/{run_id}/revoke"),
    (_RECOVER_PATH_RE, "/api/runs/{run_id}/recover"),
    (_REPLAY_PATH_RE, "/api/runs/{run_id}/replay"),
    (_RUN_PATH_RE, "/api/runs/{run_id}"),
)
_KNOWN_ROUTES = frozenset(
    {
        "/",
        "/index.html",
        "/assets/app.css",
        "/assets/app.js",
        "/api/health",
        "/api/config",
        "/api/runs",
    }
)


class _DuplicateFieldError(ValueError):
    pass


def resolve_bind_address(
    environ: Mapping[str, str] | None = None,
) -> tuple[str, int]:
    """Resolve deployment-safe server defaults without exposing environment data."""

    values = os.environ if environ is None else environ
    host = values.get("MANDATEGUARD_PRODUCT_HOST") or _DEFAULT_PRODUCT_HOST
    raw_port = (
        values.get("PORT")
        or values.get("MANDATEGUARD_PRODUCT_PORT")
        or str(_DEFAULT_PRODUCT_PORT)
    )
    try:
        port = int(raw_port)
    except (TypeError, ValueError) as error:
        raise ValueError("product server port must be an integer") from error
    if not 0 <= port <= 65535:
        raise ValueError("product server port must be between 0 and 65535")
    return host, port


def route_template(path: str) -> str:
    """Reduce a request path to a bounded, non-identifying label for logs."""

    for pattern, template in _ROUTE_TEMPLATES:
        if pattern.fullmatch(path):
            return template
    return path if path in _KNOWN_ROUTES else "/unmatched"


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateFieldError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant: {value}")


class SlidingWindowLimiter:
    """Small in-memory demo limiter for mutating endpoints."""

    def __init__(self, *, limit: int = 8, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = RLock()

    def allow(self, key: str) -> bool:
        now = monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(now)
            return True


class CommerceLabHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        service: CommerceLabService,
    ) -> None:
        super().__init__(server_address, CommerceLabHandler)
        self.service = service
        self.limiter = SlidingWindowLimiter()


class CommerceLabHandler(BaseHTTPRequestHandler):
    server: CommerceLabHTTPServer

    def log_message(self, format: str, *args: object) -> None:
        # Suppress the default access line; it echoes the raw request target.
        return

    def _begin_request(self) -> None:
        self._started_at = monotonic()
        self._trace_id = token_hex(8)

    def _log_access(self, status: HTTPStatus) -> None:
        """Emit one bounded line. Never headers, bodies, query strings, or intent."""

        started = getattr(self, "_started_at", None)
        if started is None:
            return
        self._started_at = None
        print(
            "mandateguard.request "
            f"id={getattr(self, '_trace_id', '-')} "
            f"method={self.command} "
            f"route={route_template(urlsplit(self.path).path)} "
            f"status={status.value} "
            f"duration_ms={(monotonic() - started) * 1000.0:.1f} "
            f"demo_mode={self.server.service.default_mode}",
            file=sys.stderr,
            flush=True,
        )

    def do_GET(self) -> None:
        self._begin_request()
        path = urlsplit(self.path).path
        if path == "/api/health":
            self._send_json(HTTPStatus.OK, self.server.service.health())
            return
        if path == "/api/config":
            self._send_json(HTTPStatus.OK, self.server.service.public_config())
            return
        match = _RUN_PATH_RE.fullmatch(path)
        if match:
            run = self.server.service.get_run(match.group(1))
            if run is None:
                self._send_error(HTTPStatus.NOT_FOUND, "RUN_NOT_FOUND", "Run not found.")
                return
            self._send_json(HTTPStatus.OK, run.snapshot())
            return
        if path in {"/", "/index.html"}:
            self._send_static(STATIC_ROOT / "index.html")
            return
        if path == "/assets/app.css":
            self._send_static(STATIC_ROOT / "app.css")
            return
        if path == "/assets/app.js":
            self._send_static(STATIC_ROOT / "app.js")
            return
        self._send_error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "Resource not found.")

    def do_POST(self) -> None:
        self._begin_request()
        path = urlsplit(self.path).path
        client_key = self.client_address[0] if self.client_address else "local"
        if not self.server.limiter.allow(client_key):
            self._send_error(
                HTTPStatus.TOO_MANY_REQUESTS,
                "RATE_LIMITED",
                "Demo request limit reached. Try again shortly.",
            )
            return
        if path == "/api/runs":
            try:
                payload = self._read_json()
                expected = {"intent", "mode", "preset_id", "request_id"}
                if set(payload) != expected:
                    raise ValueError("request fields do not match the API schema")
                run, deduplicated = self.server.service.start_run(
                    user_intent=payload["intent"],
                    mode=payload["mode"],
                    preset_id=payload["preset_id"],
                    request_id=payload["request_id"],
                )
            except RuntimeError as error:
                self._send_error(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "MODE_UNAVAILABLE",
                    str(error),
                )
                return
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                self._send_error(HTTPStatus.BAD_REQUEST, "INVALID_REQUEST", str(error))
                return
            response = run.snapshot()
            response["deduplicated"] = deduplicated
            status = (
                HTTPStatus.OK
                if response["state"] in {"COMPLETE", "ERROR"}
                else HTTPStatus.ACCEPTED
            )
            self._send_json(status, response)
            return
        replay_match = _REPLAY_PATH_RE.fullmatch(path)
        if replay_match:
            try:
                if self.headers.get("Content-Length") not in {None, "0"}:
                    payload = self._read_json()
                    if payload:
                        raise ValueError("replay request body must be empty")
                response = self.server.service.replay(replay_match.group(1))
            except KeyError:
                self._send_error(HTTPStatus.NOT_FOUND, "RUN_NOT_FOUND", "Run not found.")
                return
            except (TypeError, ValueError) as error:
                self._send_error(HTTPStatus.BAD_REQUEST, "INVALID_REQUEST", str(error))
                return
            except RuntimeError as error:
                self._send_error(HTTPStatus.CONFLICT, "REPLAY_UNAVAILABLE", str(error))
                return
            self._send_json(HTTPStatus.OK, response)
            return
        recover_match = _RECOVER_PATH_RE.fullmatch(path)
        if recover_match:
            try:
                if self.headers.get("Content-Length") not in {None, "0"}:
                    payload = self._read_json()
                    if payload:
                        raise ValueError(
                            "recovery request body must be empty; sources are server-selected"
                        )
                response = self.server.service.recover(recover_match.group(1))
            except KeyError:
                self._send_error(HTTPStatus.NOT_FOUND, "RUN_NOT_FOUND", "Run not found.")
                return
            except (TypeError, ValueError) as error:
                self._send_error(HTTPStatus.BAD_REQUEST, "INVALID_REQUEST", str(error))
                return
            except RuntimeError as error:
                self._send_error(
                    HTTPStatus.CONFLICT,
                    "RECOVERY_UNAVAILABLE",
                    str(error),
                )
                return
            self._send_json(HTTPStatus.OK, response)
            return
        revoke_match = _REVOKE_PATH_RE.fullmatch(path)
        if revoke_match:
            try:
                if self.headers.get("Content-Length") not in {None, "0"}:
                    payload = self._read_json()
                    if payload:
                        raise ValueError("revocation request body must be empty")
                response = self.server.service.revoke_mandate(revoke_match.group(1))
            except KeyError:
                self._send_error(HTTPStatus.NOT_FOUND, "RUN_NOT_FOUND", "Run not found.")
                return
            except (TypeError, ValueError) as error:
                self._send_error(HTTPStatus.BAD_REQUEST, "INVALID_REQUEST", str(error))
                return
            except RuntimeError as error:
                self._send_error(
                    HTTPStatus.CONFLICT,
                    "REVOCATION_UNAVAILABLE",
                    str(error),
                )
                return
            self._send_json(HTTPStatus.OK, response)
            return
        execute_match = _EXECUTE_PATH_RE.fullmatch(path)
        if execute_match:
            try:
                if self.headers.get("Content-Length") not in {None, "0"}:
                    payload = self._read_json()
                    if payload:
                        raise ValueError("execution request body must be empty")
                response = self.server.service.attempt_execution(
                    execute_match.group(1)
                )
            except KeyError:
                self._send_error(HTTPStatus.NOT_FOUND, "RUN_NOT_FOUND", "Run not found.")
                return
            except (TypeError, ValueError) as error:
                self._send_error(HTTPStatus.BAD_REQUEST, "INVALID_REQUEST", str(error))
                return
            except RuntimeError as error:
                self._send_error(
                    HTTPStatus.CONFLICT,
                    "EXECUTION_UNAVAILABLE",
                    str(error),
                )
                return
            self._send_json(HTTPStatus.OK, response)
            return
        self._send_error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "Resource not found.")

    def _read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("Content-Length is required")
        try:
            length = int(raw_length)
        except ValueError as error:
            raise ValueError("Content-Length is invalid") from error
        if length < 0 or length > _MAX_REQUEST_BYTES:
            raise ValueError("request body is too large")
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Content-Type must be application/json")
        raw = self.rfile.read(length)
        try:
            decoded = json.loads(
                raw,
                object_pairs_hook=_object_without_duplicates,
                parse_constant=_reject_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise ValueError("request body is not valid JSON") from error
        if not isinstance(decoded, dict):
            raise ValueError("request body must be a JSON object")
        return decoded

    def _send_json(self, status: HTTPStatus, payload: object) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        self.send_response(status.value)
        self._common_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self._log_access(status)

    def _send_error(self, status: HTTPStatus, code: str, message: str) -> None:
        self._send_json(status, {"error": {"code": code, "message": message}})

    def _send_static(self, path: Path) -> None:
        try:
            body = path.read_bytes()
        except OSError:
            self._send_error(
                HTTPStatus.NOT_FOUND, "ASSET_NOT_FOUND", "Asset not found."
            )
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix == ".js":
            content_type = "text/javascript"
        self.send_response(HTTPStatus.OK.value)
        self._common_headers()
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self._log_access(HTTPStatus.OK)

    def _common_headers(self) -> None:
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")


def create_server(
    *,
    host: str,
    port: int,
    service: CommerceLabService | None = None,
) -> CommerceLabHTTPServer:
    if not isinstance(host, str) or not host:
        raise ValueError("host must be non-empty")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535")
    return CommerceLabHTTPServer((host, port), service or CommerceLabService())
