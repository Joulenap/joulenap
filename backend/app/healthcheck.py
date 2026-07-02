"""Container HEALTHCHECK probe: hit the local ``/api/health`` on the configured port.

Run by the Docker ``HEALTHCHECK`` as ``python -m app.healthcheck``. Exits 0 when the API
answers 200, 1 otherwise. Reads ``app.port`` from config so it tracks a changed port
without the Dockerfile having to know it; falls back to 8080 if the config can't be read.
"""

from __future__ import annotations

import sys
import urllib.request

from . import paths
from .config import load_config

_DEFAULT_PORT = 8080


def _port() -> int:
    try:
        return load_config(paths.config_path()).app.port
    except Exception:  # noqa: BLE001 — any config problem: fall back to the default port
        return _DEFAULT_PORT


def main() -> int:
    url = f"http://127.0.0.1:{_port()}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=4) as resp:  # noqa: S310 — fixed localhost URL
            return 0 if resp.status == 200 else 1
    except Exception:  # noqa: BLE001 — unreachable/timeout/non-200 all mean "unhealthy"
        return 1


if __name__ == "__main__":
    sys.exit(main())
