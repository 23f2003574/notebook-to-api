import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from backend.dashboard import app, allowed_origins, DEFAULT_ALLOWED_ORIGINS

PROJECT_ROOT = Path(__file__).resolve().parents[1]

client = TestClient(app)


def test_wildcard_origin_is_not_in_the_default_allowlist():
    """Combining a literal "*" with allow_credentials=True doesn't just
    allow every origin -- Starlette's CORSMiddleware reflects the actual
    requesting Origin back with Access-Control-Allow-Credentials: true,
    letting any website make authenticated cross-origin requests.
    """

    assert "*" not in DEFAULT_ALLOWED_ORIGINS
    assert "*" not in allowed_origins()


def test_arbitrary_origin_is_not_granted_cors_access():
    """Confirmed live before this fix: an arbitrary attacker-controlled
    Origin got reflected back as Access-Control-Allow-Origin with
    Access-Control-Allow-Credentials: true, on every route including
    /api/upload, /api/inspect and /api/compile.
    """

    resp = client.get(
        "/api/health",
        headers={"Origin": "https://evil-attacker-site.example"},
    )

    assert resp.status_code == 200
    assert "access-control-allow-origin" not in resp.headers


def test_known_dev_frontend_origin_still_granted_credentialed_cors_access():

    resp = client.get(
        "/api/health",
        headers={"Origin": "http://localhost:5173"},
    )

    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert resp.headers["access-control-allow-credentials"] == "true"


def test_allowed_origins_env_var_overrides_default_list():
    """Run in a fresh subprocess since the allowlist is baked into the
    CORSMiddleware instance at app-import time.
    """

    script = f"""
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})

from fastapi.testclient import TestClient
from backend.dashboard import app

client = TestClient(app)

allowed = client.get("/api/health", headers={{"Origin": "https://myapp.example.com"}})
assert allowed.headers.get("access-control-allow-origin") == "https://myapp.example.com", allowed.headers

not_allowed = client.get("/api/health", headers={{"Origin": "http://localhost:5173"}})
assert "access-control-allow-origin" not in not_allowed.headers, dict(not_allowed.headers)

print("CORS_ENV_OVERRIDE_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        env={
            **os.environ,
            "NOTEBOOK_API_ALLOWED_ORIGINS": "https://myapp.example.com",
        },
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "CORS_ENV_OVERRIDE_OK" in proc.stdout
