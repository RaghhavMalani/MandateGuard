"""Run the local judge-facing MandateGuard commerce lab."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from mandateguard.product.http import create_server  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host",
        default=os.environ.get("MANDATEGUARD_PRODUCT_HOST", "127.0.0.1"),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MANDATEGUARD_PRODUCT_PORT", "8080")),
    )
    args = parser.parse_args(argv)
    server = create_server(host=args.host, port=args.port)
    host, port = server.server_address[:2]
    print(f"MandateGuard Commerce Lab: http://{host}:{port}")
    print("Default mode: OFFLINE DEMO MODE (zero external calls)")
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
