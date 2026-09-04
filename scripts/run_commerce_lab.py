"""Run the local judge-facing MandateGuard commerce lab."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from threading import Thread


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from mandateguard.product.http import create_server, resolve_bind_address  # noqa: E402


def _warm_sandbox(server: object) -> None:
    """Build the sandbox world in the background, after the port is bound.

    Generation takes about a second. Doing it at import time would delay the
    health check a free-tier host uses to decide the container is up; doing it
    lazily on the first Playground request would put that second in front of the
    first person to open the page. So it happens on a daemon thread once the
    socket is listening, and the lazy path stays as the fallback: a failure here
    costs nothing but a slower first request.
    """

    try:
        server.service.playground_config()  # type: ignore[attr-defined]
    except Exception as error:  # noqa: BLE001 - warming must never take the server down
        print(f"sandbox warm-up deferred to first request: {error!r}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    default_host, default_port = resolve_bind_address()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host",
        default=default_host,
    )
    parser.add_argument(
        "--port",
        type=int,
        default=default_port,
    )
    args = parser.parse_args(argv)
    server = create_server(host=args.host, port=args.port)
    host, port = server.server_address[:2]
    display_host = "127.0.0.1" if host == "0.0.0.0" else host
    print(f"MandateGuard Commerce Lab: http://{display_host}:{port} (bound to {host})")
    print("Default mode: OFFLINE DEMO MODE (zero external calls)")
    Thread(target=_warm_sandbox, args=(server,), daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        return 0
    finally:
        server.shutdown()
        server.server_close()
        server.service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
