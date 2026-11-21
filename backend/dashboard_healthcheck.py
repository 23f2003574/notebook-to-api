"""Container health check for the dashboard: `python -m backend.dashboard_healthcheck`.

Exit 0 when GET /api/health answers 200. The request carries the dashboard key when the container
has one in $NOTEBOOK_API_DASHBOARD_KEY (or the first line of $NOTEBOOK_API_DASHBOARD_KEY_FILE). A
container configured only with digest-style named keys (NOTEBOOK_API_DASHBOARD_KEYS) has no
plaintext key to send: there, a 401 still proves the server is up and enforcing authentication, so
it counts as healthy. Kept free of heavy imports so it starts instantly."""
from __future__ import annotations

import os
import ssl
import sys
import urllib.error
import urllib.request


def _key() -> str:
    path = os.environ.get("NOTEBOOK_API_DASHBOARD_KEY_FILE", "").strip()
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                return f.readline().strip()
        except OSError:
            return ""
    return os.environ.get("NOTEBOOK_API_DASHBOARD_KEY", "").strip()


def check(timeout: float = 4.0) -> bool:
    port = os.environ.get("NOTEBOOK_API_DASHBOARD_PORT", "8001").strip() or "8001"
    tls = bool(os.environ.get("NOTEBOOK_API_DASHBOARD_SSL_CERTFILE", "").strip())
    url = f"{'https' if tls else 'http'}://127.0.0.1:{port}/api/health"
    key = _key()
    request = urllib.request.Request(url, headers={"X-API-Key": key} if key else {})
    context = ssl._create_unverified_context() if tls else None  # loopback only; the cert names a host
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            return response.status == 200
    except urllib.error.HTTPError as exc:
        return exc.code in (401, 403) and not key
    except (OSError, ValueError):
        return False


if __name__ == "__main__":
    sys.exit(0 if check() else 1)
