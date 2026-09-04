import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.compiler import NOTEBOOK_TO_API_VERSION
from backend.dashboard import (
    app,
    dashboard_host,
    dashboard_log_level,
    dashboard_port,
    dashboard_rate_limit_per_minute,
    dashboard_reload,
    dashboard_ssl_config,
    DASHBOARD_RATE_LIMIT_WINDOW_SECONDS,
    FRONTEND_DIST_DIR,
    mount_frontend_static_files,
    _DASHBOARD_RATE_LIMIT_WINDOWS,
    _evict_stale_dashboard_rate_limit_windows,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

client = TestClient(app)


def test_dashboard_host_defaults_to_all_interfaces(monkeypatch):

    monkeypatch.delenv("NOTEBOOK_API_DASHBOARD_HOST", raising=False)

    assert dashboard_host() == "0.0.0.0"


def test_dashboard_host_env_var_overrides_the_default(monkeypatch):

    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_HOST", "127.0.0.1")

    assert dashboard_host() == "127.0.0.1"


def test_dashboard_port_defaults_to_8001(monkeypatch):
    """8001, not 8000 -- 8000 is the *generated* app's own default (see
    `serve --port` and the generated Dockerfile's EXPOSE 8000), and the
    bundled frontend already calls the dashboard API on 8001 (see
    frontend/src/components/Dashboard.jsx). Defaulting both services to
    the same port would make running them side by side impossible
    without one of them already overriding it.
    """

    monkeypatch.delenv("NOTEBOOK_API_DASHBOARD_PORT", raising=False)

    assert dashboard_port() == 8001


def test_dashboard_port_env_var_overrides_the_default(monkeypatch):

    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_PORT", "9231")

    assert dashboard_port() == 9231


def test_dashboard_rate_limit_per_minute_defaults_to_disabled(monkeypatch):

    monkeypatch.delenv("NOTEBOOK_API_DASHBOARD_RATE_LIMIT_PER_MINUTE", raising=False)

    assert dashboard_rate_limit_per_minute() == 0


def test_dashboard_rate_limit_per_minute_env_var_overrides_the_default(monkeypatch):

    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_RATE_LIMIT_PER_MINUTE", "45")

    assert dashboard_rate_limit_per_minute() == 45


@pytest.fixture(autouse=False)
def _clear_dashboard_rate_limit_windows():
    _DASHBOARD_RATE_LIMIT_WINDOWS.clear()
    try:
        yield
    finally:
        _DASHBOARD_RATE_LIMIT_WINDOWS.clear()


def test_dashboard_rate_limit_disabled_by_default_allows_unlimited_requests(
    monkeypatch, _clear_dashboard_rate_limit_windows
):

    monkeypatch.delenv("NOTEBOOK_API_DASHBOARD_RATE_LIMIT_PER_MINUTE", raising=False)

    for _ in range(10):
        assert client.get("/api/health").status_code == 200


def test_dashboard_rate_limit_returns_429_once_the_configured_limit_is_exceeded(
    monkeypatch, _clear_dashboard_rate_limit_windows
):

    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_RATE_LIMIT_PER_MINUTE", "3")

    for _ in range(3):
        assert client.get("/api/health").status_code == 200

    resp = client.get("/api/health")

    assert resp.status_code == 429
    assert "Rate limit exceeded" in resp.json()["detail"]


def test_dashboard_rate_limit_429_response_includes_a_positive_retry_after_header(
    monkeypatch, _clear_dashboard_rate_limit_windows
):

    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_RATE_LIMIT_PER_MINUTE", "1")

    assert client.get("/api/health").status_code == 200
    resp = client.get("/api/health")

    assert resp.status_code == 429
    assert int(resp.headers["retry-after"]) > 0


def test_dashboard_rate_limit_429_response_still_gets_cors_headers(
    monkeypatch, _clear_dashboard_rate_limit_windows
):
    """CORSMiddleware is added before this rate-limit middleware (see
    _enforce_dashboard_rate_limit's own docstring), so it wraps it and
    still adds Access-Control-Allow-Origin/-Credentials to a 429 -- without
    that ordering, a legitimate frontend's own JS could never even read
    this response's body to show *why* it was rejected.
    """

    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_RATE_LIMIT_PER_MINUTE", "1")

    headers = {"Origin": "http://localhost:5173"}
    assert client.get("/api/health", headers=headers).status_code == 200
    resp = client.get("/api/health", headers=headers)

    assert resp.status_code == 429
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert resp.headers["access-control-allow-credentials"] == "true"


def test_dashboard_rate_limit_success_responses_include_x_ratelimit_headers(
    monkeypatch, _clear_dashboard_rate_limit_windows
):
    """Confirmed exploitable before this fix: a successful (non-429)
    response carried no rate-limit information at all -- a well-behaved
    caller had no way to see it was about to be throttled (a low or zero
    remaining count) short of actually hitting the 429 first. Mirrors the
    identical X-RateLimit-Limit/-Remaining/-Reset headers every
    *generated* app's own per-API-key limiter now sends (see
    api_generator.py).
    """

    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_RATE_LIMIT_PER_MINUTE", "2")

    first = client.get("/api/health")
    assert first.status_code == 200
    assert first.headers["x-ratelimit-limit"] == "2"
    assert first.headers["x-ratelimit-remaining"] == "1"
    assert int(first.headers["x-ratelimit-reset"]) > 0

    second = client.get("/api/health")
    assert second.status_code == 200
    assert second.headers["x-ratelimit-remaining"] == "0"


def test_dashboard_rate_limit_429_response_includes_x_ratelimit_headers(
    monkeypatch, _clear_dashboard_rate_limit_windows
):

    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_RATE_LIMIT_PER_MINUTE", "1")

    assert client.get("/api/health").status_code == 200
    resp = client.get("/api/health")

    assert resp.status_code == 429
    assert resp.headers["x-ratelimit-limit"] == "1"
    assert resp.headers["x-ratelimit-remaining"] == "0"
    assert int(resp.headers["x-ratelimit-reset"]) > 0


def test_dashboard_rate_limit_disabled_by_default_sends_no_x_ratelimit_headers(
    monkeypatch, _clear_dashboard_rate_limit_windows
):

    monkeypatch.delenv("NOTEBOOK_API_DASHBOARD_RATE_LIMIT_PER_MINUTE", raising=False)

    resp = client.get("/api/health")

    assert resp.status_code == 200
    assert "x-ratelimit-limit" not in resp.headers


def test_dashboard_rate_limit_resets_once_the_window_has_fully_elapsed(
    monkeypatch, _clear_dashboard_rate_limit_windows
):

    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_RATE_LIMIT_PER_MINUTE", "1")

    assert client.get("/api/health").status_code == 200
    assert client.get("/api/health").status_code == 429

    # Simulate the window having fully elapsed, rather than sleeping the
    # test for DASHBOARD_RATE_LIMIT_WINDOW_SECONDS.
    for key, (window_start, count) in list(_DASHBOARD_RATE_LIMIT_WINDOWS.items()):
        _DASHBOARD_RATE_LIMIT_WINDOWS[key] = (
            window_start - DASHBOARD_RATE_LIMIT_WINDOW_SECONDS - 1, count
        )

    assert client.get("/api/health").status_code == 200


def test_evict_stale_dashboard_rate_limit_windows_only_sweeps_past_the_threshold(
    monkeypatch, _clear_dashboard_rate_limit_windows
):

    import backend.dashboard as dashboard_module

    monkeypatch.setattr(dashboard_module, "_DASHBOARD_RATE_LIMIT_SWEEP_THRESHOLD", 3)

    now = 1_000_000.0
    _DASHBOARD_RATE_LIMIT_WINDOWS["stale-a"] = (now - 1000, 1)
    _DASHBOARD_RATE_LIMIT_WINDOWS["stale-b"] = (now - 1000, 1)
    _DASHBOARD_RATE_LIMIT_WINDOWS["fresh"] = (now, 1)

    # At (not past) the threshold: no-op, even though "stale-a"/"stale-b"
    # have already fully elapsed.
    _evict_stale_dashboard_rate_limit_windows(now)
    assert set(_DASHBOARD_RATE_LIMIT_WINDOWS) == {"stale-a", "stale-b", "fresh"}

    _DASHBOARD_RATE_LIMIT_WINDOWS["one-more"] = (now, 1)

    # Past the threshold: sweeps every window that's already fully
    # elapsed, leaving the still-current ones untouched.
    _evict_stale_dashboard_rate_limit_windows(now)
    assert set(_DASHBOARD_RATE_LIMIT_WINDOWS) == {"fresh", "one-more"}


def test_dashboard_stamps_security_headers_on_a_successful_response():
    """Confirmed exploitable before this fix: this dashboard's own API
    set none of X-Content-Type-Options/X-Frame-Options/Referrer-Policy on
    any response -- grepped for across backend/dashboard.py, zero hits.
    """

    resp = client.get("/api/health")

    assert resp.status_code == 200
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "no-referrer"


def test_dashboard_stamps_security_headers_on_a_404_response():
    """Registered last (see _add_security_headers' own docstring) so it
    wraps every other middleware -- must apply to an error response too,
    not just a successful one.
    """

    resp = client.get("/api/this-route-does-not-exist")

    assert resp.status_code == 404
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"


def test_dashboard_stamps_security_headers_on_a_429_rate_limited_response(
    monkeypatch, _clear_dashboard_rate_limit_windows
):
    """Registered outermost, wrapping _enforce_dashboard_rate_limit --
    its own 429 short-circuit must still pass through this middleware's
    call_next return and get the same headers a 200 would.
    """

    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_RATE_LIMIT_PER_MINUTE", "1")

    assert client.get("/api/health").status_code == 200
    resp = client.get("/api/health")

    assert resp.status_code == 429
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "no-referrer"


def test_root_endpoint_reports_service_metadata():
    """GET / had no test coverage at all before this."""

    resp = client.get("/")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["service"] == "notebook-to-api Dashboard API"
    assert body["version"] == NOTEBOOK_TO_API_VERSION
    assert body["docs"] == "/docs"


def test_app_own_openapi_version_matches_notebook_to_api_version():
    """FastAPI(version=...) -- surfaced in the app's own OpenAPI schema
    and /docs -- must report the identical NOTEBOOK_TO_API_VERSION GET /
    and GET /api/health already do, not a second, independently-drifting
    literal.
    """

    assert app.version == NOTEBOOK_TO_API_VERSION


def test_dashboard_module_run_directly_passes_configured_host_and_port_to_uvicorn():
    """The `if __name__ == "__main__":` block (what `python -m
    backend.dashboard` actually executes) had zero test coverage before
    this -- exercised here by running the module as __main__ in a
    subprocess, with uvicorn.run replaced by a stub so nothing actually
    tries to bind a port and block. Run in a subprocess (mirroring
    test_dashboard_cors.py's env-var override test) since dashboard_host/
    dashboard_port must be re-read from the environment at process start,
    not from whatever's already been imported in this test process.
    """

    script = f"""
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})

import uvicorn

captured = {{}}

def fake_run(app_path, **kwargs):
    captured["app_path"] = app_path
    captured.update(kwargs)

uvicorn.run = fake_run

import runpy
runpy.run_module("backend.dashboard", run_name="__main__")

assert captured["app_path"] == "backend.dashboard:app", captured
assert captured["host"] == "127.0.0.1", captured
assert captured["port"] == 9231, captured
assert captured["reload"] is True, captured

print("DASHBOARD_MAIN_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        env={
            **os.environ,
            "NOTEBOOK_API_DASHBOARD_HOST": "127.0.0.1",
            "NOTEBOOK_API_DASHBOARD_PORT": "9231",
        },
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "DASHBOARD_MAIN_OK" in proc.stdout


def test_dashboard_module_run_directly_defaults_to_8001_without_env_override():

    script = f"""
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})

import uvicorn

captured = {{}}

def fake_run(app_path, **kwargs):
    captured.update(kwargs)

uvicorn.run = fake_run

import runpy
runpy.run_module("backend.dashboard", run_name="__main__")

assert captured["host"] == "0.0.0.0", captured
assert captured["port"] == 8001, captured

print("DASHBOARD_MAIN_DEFAULT_OK")
"""

    env = {k: v for k, v in os.environ.items() if not k.startswith("NOTEBOOK_API_DASHBOARD_")}

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "DASHBOARD_MAIN_DEFAULT_OK" in proc.stdout


def test_dashboard_reload_defaults_to_true(monkeypatch):

    monkeypatch.delenv("NOTEBOOK_API_DASHBOARD_RELOAD", raising=False)

    assert dashboard_reload() is True


def test_dashboard_reload_env_var_disables_it(monkeypatch):

    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_RELOAD", "false")

    assert dashboard_reload() is False


def test_dashboard_reload_env_var_is_case_and_value_insensitive(monkeypatch):

    for falsy in ("False", "FALSE", "0", "no", "off"):
        monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_RELOAD", falsy)
        assert dashboard_reload() is False, falsy

    for truthy in ("true", "True", "yes", "1"):
        monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_RELOAD", truthy)
        assert dashboard_reload() is True, truthy


def test_dashboard_ssl_config_defaults_to_none_none(monkeypatch):

    monkeypatch.delenv("NOTEBOOK_API_DASHBOARD_SSL_KEYFILE", raising=False)
    monkeypatch.delenv("NOTEBOOK_API_DASHBOARD_SSL_CERTFILE", raising=False)

    assert dashboard_ssl_config() == (None, None)


def test_dashboard_ssl_config_returns_both_when_both_are_set(monkeypatch):

    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_SSL_KEYFILE", "/certs/key.pem")
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_SSL_CERTFILE", "/certs/cert.pem")

    assert dashboard_ssl_config() == ("/certs/key.pem", "/certs/cert.pem")


def test_dashboard_ssl_config_rejects_a_keyfile_with_no_certfile(monkeypatch):

    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_SSL_KEYFILE", "/certs/key.pem")
    monkeypatch.delenv("NOTEBOOK_API_DASHBOARD_SSL_CERTFILE", raising=False)

    with pytest.raises(ValueError, match="SSL_CERTFILE is not"):
        dashboard_ssl_config()


def test_dashboard_ssl_config_rejects_a_certfile_with_no_keyfile(monkeypatch):

    monkeypatch.delenv("NOTEBOOK_API_DASHBOARD_SSL_KEYFILE", raising=False)
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_SSL_CERTFILE", "/certs/cert.pem")

    with pytest.raises(ValueError, match="SSL_KEYFILE is not"):
        dashboard_ssl_config()


def test_dashboard_log_level_defaults_to_none(monkeypatch):

    monkeypatch.delenv("NOTEBOOK_API_DASHBOARD_LOG_LEVEL", raising=False)

    assert dashboard_log_level() is None


def test_dashboard_log_level_env_var_overrides_the_default(monkeypatch):

    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_LOG_LEVEL", "warning")

    assert dashboard_log_level() == "warning"


def test_dashboard_module_run_directly_passes_reload_ssl_and_log_level_to_uvicorn():
    """Mirrors test_dashboard_module_run_directly_passes_configured_host_and_port_to_uvicorn
    above for the new reload/ssl/log_level knobs -- run in a subprocess
    for the identical reason: they must be re-read from the environment
    at process start, not from whatever's already been imported in this
    test process.
    """

    script = f"""
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})

import uvicorn

captured = {{}}

def fake_run(app_path, **kwargs):
    captured["app_path"] = app_path
    captured.update(kwargs)

uvicorn.run = fake_run

import runpy
runpy.run_module("backend.dashboard", run_name="__main__")

assert captured["reload"] is False, captured
assert captured["ssl_keyfile"] == "/certs/key.pem", captured
assert captured["ssl_certfile"] == "/certs/cert.pem", captured
assert captured["log_level"] == "warning", captured

print("DASHBOARD_MAIN_STARTUP_CONFIG_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        env={
            **os.environ,
            "NOTEBOOK_API_DASHBOARD_RELOAD": "false",
            "NOTEBOOK_API_DASHBOARD_SSL_KEYFILE": "/certs/key.pem",
            "NOTEBOOK_API_DASHBOARD_SSL_CERTFILE": "/certs/cert.pem",
            "NOTEBOOK_API_DASHBOARD_LOG_LEVEL": "warning",
        },
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "DASHBOARD_MAIN_STARTUP_CONFIG_OK" in proc.stdout


def test_dashboard_module_run_directly_fails_fast_on_a_lopsided_ssl_config():
    """A keyfile with no matching certfile must abort *before*
    uvicorn.run is ever reached -- reused as the signal here (fake_run
    would set "called": True) that this failed the way it should: at
    dashboard_ssl_config() itself, not somewhere inside uvicorn.
    """

    script = f"""
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})

import uvicorn

captured = {{"called": False}}

def fake_run(app_path, **kwargs):
    captured["called"] = True

uvicorn.run = fake_run

import runpy

try:
    runpy.run_module("backend.dashboard", run_name="__main__")
except ValueError as e:
    assert "SSL_CERTFILE is not" in str(e), str(e)
    assert captured["called"] is False, captured
    print("DASHBOARD_MAIN_SSL_FAIL_FAST_OK")
else:
    print("DID_NOT_RAISE")
"""

    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith("NOTEBOOK_API_DASHBOARD_SSL_")
    }
    env["NOTEBOOK_API_DASHBOARD_SSL_KEYFILE"] = "/certs/key.pem"

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "DASHBOARD_MAIN_SSL_FAIL_FAST_OK" in proc.stdout


def test_frontend_is_not_mounted_in_this_checked_out_repo():
    """frontend/dist is a `npm run build` artifact, never checked into
    this repo -- confirms the real, importable app reflects that: no
    static frontend mount, and GET / still returns root()'s own JSON.
    """

    assert not FRONTEND_DIST_DIR.is_dir()
    assert not any(getattr(route, "name", None) == "frontend" for route in app.routes)

    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"


def test_mount_frontend_static_files_is_a_no_op_for_a_missing_directory(tmp_path):

    test_app = FastAPI()
    missing_dir = tmp_path / "does_not_exist"

    mount_frontend_static_files(test_app, dist_dir=missing_dir)

    assert not any(getattr(route, "name", None) == "frontend" for route in test_app.routes)


def test_mount_frontend_static_files_serves_the_built_frontend(tmp_path):

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<html><body>SPA root</body></html>", encoding="utf-8")
    (dist_dir / "assets").mkdir()
    (dist_dir / "assets" / "index.js").write_text("console.log('hi')", encoding="utf-8")

    test_app = FastAPI()
    mount_frontend_static_files(test_app, dist_dir=dist_dir)

    test_client = TestClient(test_app)

    index_resp = test_client.get("/")
    assert index_resp.status_code == 200
    assert "SPA root" in index_resp.text

    asset_resp = test_client.get("/assets/index.js")
    assert asset_resp.status_code == 200
    assert "console.log" in asset_resp.text


def test_mount_frontend_static_files_does_not_shadow_an_existing_root_route(tmp_path):
    """Registration order matters: an app.get("/") route already defined
    before the mount must keep winning at "/" -- confirmed against the
    real dashboard app in test_frontend_is_not_mounted_in_this_checked_out_repo
    above (where no frontend is mounted at all); this exercises the case
    where one actually is.
    """

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<html><body>SPA root</body></html>", encoding="utf-8")

    test_app = FastAPI()

    @test_app.get("/")
    async def root():
        return {"status": "running"}

    mount_frontend_static_files(test_app, dist_dir=dist_dir)

    test_client = TestClient(test_app)

    resp = test_client.get("/")
    assert resp.status_code == 200
    assert resp.json() == {"status": "running"}


def test_mount_frontend_static_files_does_not_shadow_existing_api_routes(tmp_path):

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<html></html>", encoding="utf-8")

    test_app = FastAPI()

    @test_app.get("/api/health")
    async def health():
        return {"status": "healthy"}

    mount_frontend_static_files(test_app, dist_dir=dist_dir)

    test_client = TestClient(test_app)

    resp = test_client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}
