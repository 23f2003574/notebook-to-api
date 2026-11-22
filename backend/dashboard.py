"""
Dashboard API Server
Serves the React dashboard frontend and provides API endpoints for compilation
"""

import hmac
import json
import contextlib
import hashlib
import re
import os
import logging
import sys
import threading
import time
import uuid
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from backend.compiler import NOTEBOOK_TO_API_VERSION
from backend.routes.upload import router as upload_router
from backend import auth_metrics
from backend.observability.deployment_governance_api import (
    register_governance_metrics_middleware,
    router as governance_metrics_router,
    health_router as governance_health_router,
)
from backend.security.authentication import router as security_authentication_router
from backend.security.api_keys import router as security_api_keys_router
from backend.security.jwt_service import router as security_jwt_router
from backend.security.rbac import router as security_rbac_router
from backend.security.permissions import router as security_permissions_router
from backend.security.session_manager import router as security_session_router
from backend.security.audit_logs import router as security_audit_router
from backend.security.security_policy import router as security_policy_router
from backend.security.secrets import router as security_secrets_router
from backend.security.security_analytics import router as security_analytics_router
from backend.security.dashboard import router as security_dashboard_router
from backend.security.export_service import router as security_export_router
from backend.security.bootstrap import bootstrap_security_subsystem
from backend.plugins.plugin_loader import router as plugin_loader_router
from backend.plugins.plugin_lifecycle import router as plugin_lifecycle_router
from backend.plugins.extension_api import router as extension_api_router
from backend.plugins.event_system import router as event_system_router
from backend.plugins.plugin_dependencies import router as plugin_dependencies_router
from backend.plugins.plugin_config import router as plugin_config_router
from backend.plugins.plugin_sandbox import router as plugin_sandbox_router
from backend.plugins.plugin_packaging import router as plugin_packaging_router
from backend.plugins.plugin_marketplace import router as plugin_marketplace_router
from backend.plugins.plugin_analytics import router as plugin_analytics_router
from backend.plugins.dashboard import router as plugin_dashboard_router
from backend.plugins.plugin_registry import router as plugin_registry_router
from backend.plugins.bootstrap import bootstrap_plugin_framework
from backend.performance.cache_manager import (
    router as performance_cache_router,
    profile_router as performance_profile_router,
)
from backend.performance.profiler import (
    pool_router as performance_pool_router,
    dashboard_router as performance_dashboard_router,
)
from backend.performance.dashboard import export_router as performance_export_router
from backend.performance.bootstrap import bootstrap_performance_subsystem
from backend.pipeline.data_sources import router as pipeline_data_sources_router
from backend.pipeline.transformation_engine import router as pipeline_transformation_router
from backend.pipeline.data_validation import router as pipeline_validation_router
from backend.pipeline.etl_engine import router as pipeline_etl_router
from backend.pipeline.schema_registry import router as pipeline_schema_registry_router
from backend.pipeline.pipeline_scheduler import router as pipeline_scheduler_router
from backend.pipeline.pipeline_executor import router as pipeline_executor_router
from backend.pipeline.checkpoint_manager import router as pipeline_checkpoint_router
from backend.pipeline.pipeline_analytics import router as pipeline_analytics_router
from backend.pipeline.dashboard import (
    router as pipeline_dashboard_router,
    export_router as pipeline_export_router,
)
from backend.pipeline.pipeline_registry import router as pipeline_registry_router
from backend.pipeline.bootstrap import bootstrap_pipeline_subsystem
from backend.ai.model_loader import router as ai_model_loader_router
from backend.ai.inference_engine import router as ai_inference_router
from backend.ai.model_versioning import router as ai_model_versioning_router
from backend.ai.prompt_templates import router as ai_prompt_templates_router
from backend.ai.batch_inference import router as ai_batch_inference_router
from backend.ai.model_routing import router as ai_model_routing_router
from backend.ai.model_benchmark import router as ai_model_benchmark_router
from backend.ai.model_deployment import router as ai_model_deployment_router
from backend.ai.inference_analytics import router as ai_inference_analytics_router
from backend.ai.dashboard import (
    router as ai_dashboard_router,
    export_router as ai_export_router,
)
from backend.ai.model_registry import router as ai_model_registry_router
from backend.ai.bootstrap import bootstrap_ai_subsystem
from backend.cluster.worker_registry import router as cluster_worker_registry_router
from backend.cluster.worker_discovery import router as cluster_worker_discovery_router
from backend.cluster.job_dispatcher import router as cluster_job_dispatcher_router
from backend.cluster.task_serializer import router as cluster_task_serializer_router
from backend.cluster.execution_coordinator import router as cluster_execution_coordinator_router
from backend.cluster.worker_health import router as cluster_worker_health_router
from backend.cluster.distributed_scheduler import router as cluster_distributed_scheduler_router
from backend.cluster.auto_scaling import router as cluster_auto_scaling_router
from backend.cluster.fault_tolerance import router as cluster_fault_tolerance_router
from backend.cluster.cluster_analytics import router as cluster_analytics_router
from backend.cluster.dashboard import router as cluster_dashboard_router
from backend.cluster.export_service import router as cluster_export_router
from backend.cluster.bootstrap import bootstrap_cluster_subsystem

@contextlib.asynccontextmanager
async def _dashboard_lifespan(_app):
    """Validate audit storage whichever way the app is launched (`python -m backend.dashboard`,
    `uvicorn backend.dashboard:app`, a container CMD, a process manager): production storage that
    would lose audit records stops the application from starting. Clients that never run the
    lifespan (TestClient without a `with` block) are unaffected."""
    from backend.observability.deployment_governance_audit_store import (
        GovernanceAuditStorageError,
        validate_audit_storage_for_startup,
    )

    try:
        validate_dashboard_auth_config(dashboard_host())
    except ValueError as exc:
        logging.getLogger("uvicorn.error").error("%s", exc)
        raise

    try:
        status = validate_audit_storage_for_startup()
    except GovernanceAuditStorageError as exc:
        logging.getLogger("uvicorn.error").error("governance audit storage: %s", exc)
        raise
    for issue in status["issues"]:
        logging.getLogger("uvicorn.error").warning("governance audit storage: %s", issue)

    from backend.routes.upload import _notebook_storage_status

    notebook_storage = _notebook_storage_status()
    for issue in notebook_storage["issues"]:
        logging.getLogger("uvicorn.error").warning("notebook storage: %s", issue)
    yield


app = FastAPI(
    lifespan=_dashboard_lifespan,
    title="notebook-to-api Dashboard",
    description="Transform Jupyter notebooks into production APIs",
    version=NOTEBOOK_TO_API_VERSION
)

DEFAULT_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:3000",
    "http://localhost:5174",
]


def allowed_origins():
    """Origins allowed to make credentialed cross-origin requests.

    Combining a literal "*" with allow_credentials=True (the previous
    config) doesn't just allow every origin: Starlette's CORSMiddleware
    reflects the actual requesting Origin header back with
    Access-Control-Allow-Credentials: true whenever allow_credentials is
    set and "*" is in allow_origins (confirmed against the installed
    starlette.middleware.cors source, and live against this app -- an
    arbitrary Origin got reflected with credentials enabled). That lets
    any website make authenticated cross-origin requests to this
    dashboard, including /api/upload, /api/inspect and /api/compile.

    Defaults to the known local frontend dev-server ports; set
    NOTEBOOK_API_ALLOWED_ORIGINS (comma-separated) to configure this for
    a real deployment instead of hardcoding one fixed list.

    NOTEBOOK_API_ALLOWED_ORIGINS is rejected outright if it contains "*":
    confirmed exploitable -- setting it to that reintroduces the exact
    vulnerability this function's own docstring above says was already
    fixed, live: an arbitrary Origin got reflected back as
    Access-Control-Allow-Origin with Access-Control-Allow-Credentials:
    true, the same way the previous hardcoded "*" config did, since
    allow_credentials=True (below) is unconditional regardless of where
    allow_origins came from. "*" is exactly the value an operator reaches
    for first when trying to "just allow everything" for a quick
    deployment, so this was a real, easy-to-hit misconfiguration this
    tool's own documented escape hatch offered no protection against.
    Failing fast here, at startup, beats silently running with that hole
    open.
    """
    raw = os.getenv("NOTEBOOK_API_ALLOWED_ORIGINS")

    if raw:
        origins = [origin.strip() for origin in raw.split(",") if origin.strip()]

        if origins:

            if "*" in origins:
                raise ValueError(
                    "NOTEBOOK_API_ALLOWED_ORIGINS must not contain '*' -- "
                    "combining a wildcard origin with this dashboard's "
                    "credentialed CORS requests (allow_credentials=True) "
                    "lets any website make authenticated cross-origin "
                    "requests to it, including /api/upload, /api/inspect "
                    "and /api/compile. List the specific origin(s) that "
                    "should be allowed instead."
                )

            return origins

    return DEFAULT_ALLOWED_ORIGINS


def dashboard_rate_limit_per_minute():
    """Requests a single client may make to this dashboard per rolling
    minute before getting a 429, via NOTEBOOK_API_DASHBOARD_RATE_LIMIT_PER_MINUTE
    -- 0 (the default) disables rate limiting entirely, the same "0 means
    off" convention NOTEBOOK_API_MAX_NOTEBOOKS and the *generated* app's
    own NOTEBOOK_API_RATE_LIMIT_PER_MINUTE (backend/generator/api_generator.py)
    already establish, so this changes nothing for an existing deployment
    until an operator opts in.

    Every *generated* app this dashboard produces already gets its own
    per-API-key rate limiter for free (see _enforce_rate_limit in
    generate_fastapi_code) -- but this dashboard's own management API
    (POST /api/upload, /api/compile, /api/deploy, /api/notebooks/import-url,
    ...) had no equivalent at all: nothing stood between an open (or
    merely misconfigured-CORS) dashboard and a caller hammering it with
    unbounded upload/compile/deploy requests, each with a real cost this
    process actually pays (disk writes, a subprocess `docker build`, an
    outbound fetch to a caller-supplied URL). Read fresh on every request
    (like dashboard_host()/dashboard_port()/dashboard_reload() above)
    rather than cached at import time, so tests can toggle it via
    monkeypatch without reloading this module.
    """
    return int(os.getenv("NOTEBOOK_API_DASHBOARD_RATE_LIMIT_PER_MINUTE", "0"))


def dashboard_json_logs_enabled():
    """Whether this dashboard process prints one structured JSON line per
    request to stdout, via NOTEBOOK_API_DASHBOARD_JSON_LOGS -- off
    (the default) reproduces this dashboard's previous stdout output
    exactly, purely additive alongside uvicorn's own default access log,
    never a disable or replacement of it.

    Every *generated* app this dashboard produces already gets this for
    free (NOTEBOOK_API_JSON_LOGS, see _log_request_json in
    generate_fastapi_code) -- added specifically because the only
    per-request record such an app ever produced otherwise was uvicorn's
    own free-text access line, with no request_id of its own and no
    duration to grep `docker logs` for the exact X-Request-ID a caller
    already got back in a response header. This dashboard's own
    management API (POST /api/upload, /api/compile, /api/deploy, ...)
    already stamps that identical X-Request-ID/X-Process-Time-Ms pair on
    every response it sends (_add_request_id_header/
    _add_process_time_header below) for the exact same correlation
    reason, but had no equivalent way to actually log either one
    anywhere -- an operator grepping this dashboard's own `docker logs`
    for one specific X-Request-ID a caller already has had nothing to
    find, the identical gap NOTEBOOK_API_JSON_LOGS already closed for
    every *generated* app, just never itself closed for the dashboard
    process producing those compiles in the first place.

    Read fresh on every request (like dashboard_rate_limit_per_minute()
    above) rather than cached at import time, so tests can toggle it via
    monkeypatch without reloading this module. Same truthy-spelling
    convention JSON_REQUEST_LOGS's own generated os.getenv(...).strip().
    lower() check already uses ("true"/"1"/"yes"/"on", case-insensitive),
    so an operator setting this dashboard's own env var doesn't have to
    remember a different accepted spelling than the one already
    documented for every compiled app it produces.
    """
    return os.getenv(
        "NOTEBOOK_API_DASHBOARD_JSON_LOGS", "false"
    ).strip().lower() in ("true", "1", "yes", "on")


DASHBOARD_RATE_LIMIT_WINDOW_SECONDS = 60

# Fixed window per client key -- the same {key: (window_start, count)}
# shape and "reset once the window's fully elapsed rather than decay
# gradually" behavior _RATE_LIMIT_WINDOWS already uses in every generated
# app, just keyed by client IP here since this dashboard (unlike a
# generated app) has no built-in notion of an API key every caller
# already presents. A reverse proxy in front of this dashboard that
# doesn't forward the real client IP would make every request appear to
# come from the same key -- the identical caveat the generated app's own
# per-API-key limiter already documents for the opposite case (an IP
# unreliable behind a proxy), just unavoidable here since this dashboard
# has nothing sturdier than the IP to key on.
_DASHBOARD_RATE_LIMIT_WINDOWS = {}

# Unlike the generated app's own API_KEYS (a small, fixed set known at
# startup), a client key here is an arbitrary IP address -- an unbounded
# set under sustained traffic from many distinct clients (or a spoofed-IP
# flood). Swept opportunistically, only once the tracked-client count
# actually crosses this threshold, so normal traffic (well under it)
# never pays any sweep cost at all.
_DASHBOARD_RATE_LIMIT_SWEEP_THRESHOLD = 10_000


def _evict_stale_dashboard_rate_limit_windows(now):
    """Drop every tracked client whose own window has already fully
    elapsed -- called opportunistically (see
    _DASHBOARD_RATE_LIMIT_SWEEP_THRESHOLD above), the same lazy,
    no-background-thread eviction style _evict_expired_tasks already
    uses in every generated app for its own TASKS dict.
    """
    if len(_DASHBOARD_RATE_LIMIT_WINDOWS) <= _DASHBOARD_RATE_LIMIT_SWEEP_THRESHOLD:
        return

    stale_keys = [
        key for key, (window_start, _count) in _DASHBOARD_RATE_LIMIT_WINDOWS.items()
        if now - window_start >= DASHBOARD_RATE_LIMIT_WINDOW_SECONDS
    ]

    for key in stale_keys:
        _DASHBOARD_RATE_LIMIT_WINDOWS.pop(key, None)


@app.middleware("http")
async def _enforce_dashboard_rate_limit(request: Request, call_next):
    """Reject a request with 429 once its own client key has made more
    than dashboard_rate_limit_per_minute() requests inside the current
    rolling window -- see dashboard_rate_limit_per_minute()'s own
    docstring above for why this dashboard needs this at all.

    Registered (via the @app.middleware("http") decorator, itself
    Starlette's own sugar for add_middleware(BaseHTTPMiddleware,
    dispatch=...)) *before* app.add_middleware(CORSMiddleware, ...)
    below -- Starlette's own add_middleware inserts each new middleware
    at the *front* of its internal list, and then builds the actual
    request-handling stack by wrapping outward-in over that list in
    reverse, so the middleware added *last* ends up outermost. Adding
    CORSMiddleware after this one is what makes it the outermost layer,
    not the other way around: confirmed live -- with CORSMiddleware
    added first instead (this dashboard's own original ordering, before
    this comment), a 429 from here never got the CORS headers this
    dashboard's own tests below already caught. With CORSMiddleware
    outermost, a 429 response from here still passes back out through it
    on its way to the caller, so it still gets the same
    Access-Control-Allow-Origin/-Credentials headers any other response
    already would -- without that ordering, a legitimate frontend's own
    JS could never even read this response's body to show *why* it was
    rejected.
    """
    limit = dashboard_rate_limit_per_minute()

    if limit > 0:

        key = request.client.host if request.client else "unknown"
        now = time.time()

        _evict_stale_dashboard_rate_limit_windows(now)

        window_start, count = _DASHBOARD_RATE_LIMIT_WINDOWS.get(key, (now, 0))

        if now - window_start >= DASHBOARD_RATE_LIMIT_WINDOW_SECONDS:
            window_start, count = now, 0

        count += 1
        _DASHBOARD_RATE_LIMIT_WINDOWS[key] = (window_start, count)

        # Set on every rate-limited request, not just a 429 -- the same
        # X-RateLimit-Limit/-Remaining/-Reset contract (GitHub/Stripe/...)
        # every *generated* app's own per-API-key limiter already sends
        # (see _enforce_rate_limit in api_generator.py), so a well-behaved
        # caller of this dashboard's own API can see it's about to be
        # throttled (a low/zero Remaining) and back off on its own, rather
        # than the only previous signal being a 429 it's already received.
        reset_at = int(window_start + DASHBOARD_RATE_LIMIT_WINDOW_SECONDS)
        remaining = max(0, limit - count)

        if count > limit:

            retry_after = max(
                1, int(DASHBOARD_RATE_LIMIT_WINDOW_SECONDS - (now - window_start))
            )

            return JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        f"Rate limit exceeded: {limit} requests per "
                        f"{DASHBOARD_RATE_LIMIT_WINDOW_SECONDS}s per client"
                    )
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_at),
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset_at)
        return response

    return await call_next(request)


def dashboard_max_request_body_bytes():
    """Bytes a single non-multipart request body to this dashboard may
    declare via its own Content-Length header before being rejected with
    413, via NOTEBOOK_API_DASHBOARD_MAX_REQUEST_BYTES -- defaults to the
    same 10MB MAX_UPLOAD_BYTES (backend/routes/upload.py) already
    defaults to, so an operator who never touches either env var gets the
    identical ceiling on both.

    Every *generated* app this dashboard produces already gets its own
    MaxRequestBodySizeMiddleware for free (generate_fastapi_code,
    backend/generator/api_generator.py) -- but this dashboard's own
    management API had no equivalent at all for any of its many JSON-body
    endpoints (PUT /api/notebooks/{filename}/tags, .../description, POST
    /api/notebooks/duplicates/resolve, every *-batch endpoint, ...):
    FastAPI/Starlette fully buffers a request body into memory to parse
    it as JSON *before* any of this dashboard's own per-field/per-count
    validations (_validate_batch_entry_count, a tag/description length
    cap, ...) ever get a chance to reject it for being malformed. An
    arbitrarily large (multi-GB) JSON body sent to any of these forces
    this dashboard process to buffer the whole thing in memory first --
    the exact unbounded-memory-consumption class MaxRequestBodySizeMiddleware
    already exists to prevent for a *generated* app, never applied to the
    dashboard serving it. Read fresh on every request (like
    dashboard_rate_limit_per_minute() above) rather than cached at import
    time, so tests can toggle it via monkeypatch without reloading this
    module.
    """
    return int(
        os.getenv("NOTEBOOK_API_DASHBOARD_MAX_REQUEST_BYTES", str(10 * 1024 * 1024))
    )


@app.middleware("http")
async def _enforce_dashboard_max_request_body_bytes(request: Request, call_next):
    """Reject a request with 413 before its body is ever read, once its
    own declared Content-Length exceeds dashboard_max_request_body_bytes()
    -- see that function's own docstring for the gap this closes.

    Deliberately skipped for multipart/form-data entirely: POST
    /api/upload, /api/upload/batch, /api/notebooks/import, and
    /api/notebooks/import-url already enforce their own, more precise
    size limits (MAX_UPLOAD_BYTES per file, checked incrementally while
    streaming each chunk to disk -- see _save_uploaded_notebook,
    backend/routes/upload.py -- not declared upfront the way this check
    works), and a batch upload's own aggregate Content-Length (several
    files in one multipart body) can legitimately exceed a single file's
    own cap -- this check would otherwise reject a perfectly ordinary
    multi-file batch upload that every one of those files, individually,
    already passes.

    Registered *after* _enforce_dashboard_rate_limit above (so this ends
    up wrapping it, running first on the way in): an oversized request
    should never consume a caller's own rate-limit quota just to be
    rejected for its size instead. Also registered before
    app.add_middleware(CORSMiddleware, ...) below, so CORSMiddleware
    still ends up the outermost middleware of all three -- the identical
    ordering reasoning _enforce_dashboard_rate_limit's own docstring
    already gives, just for a different response code.
    """
    content_type = request.headers.get("content-type", "")

    if not content_type.startswith("multipart/form-data"):

        content_length = request.headers.get("content-length")

        if content_length is not None:

            try:
                too_large = int(content_length) > dashboard_max_request_body_bytes()
            except ValueError:
                too_large = False

            if too_large:

                return JSONResponse(
                    status_code=413,
                    content={
                        "detail": (
                            "Request body exceeds the maximum allowed size "
                            f"of {dashboard_max_request_body_bytes()} bytes"
                        )
                    },
                )

    return await call_next(request)


def dashboard_api_key():
    """The legacy single key, from NOTEBOOK_API_DASHBOARD_KEY (unset or empty: not configured).
    It stays valid, under the name "default", next to any NOTEBOOK_API_DASHBOARD_KEYS."""

    return os.getenv("NOTEBOOK_API_DASHBOARD_KEY", "")


_KEY_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_MIN_PRODUCTION_KEY_LENGTH = 16


def _key_digest(value):
    return hashlib.sha256(value.encode("utf-8")).digest()


KEY_SCOPES = ("read", "compile", "admin")


def _parse_named_keys(text, source, scopes=None):
    """Parse `name:key[:scope]` entries separated by commas or newlines (`#` starts a comment line).
    `key` is either the secret itself or `sha256:<64 hex digits>`, so a deployment can keep only
    digests on disk. Returns [(name, digest, plaintext_length_or_None)]. Messages never contain a
    key."""
    entries = []
    for raw in re.split(r"[,\n]", text):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, secret = line.partition(":")
        name, secret = name.strip(), secret.strip()
        if not separator or not _KEY_NAME.match(name) or not secret:
            raise ValueError(f"{source} entries must look like name:key[:scope] (name: letters, digits, . _ -)")
        scope = "admin"
        head, colon, tail = secret.rpartition(":")
        if colon and head and re.fullmatch(r"[A-Za-z]+", tail) and head.strip().lower() != "sha256":
            # A trailing alphabetic segment after the key is its scope; unknown scopes are refused
            # (a typo must never silently widen or narrow a credential).
            if tail.lower() not in KEY_SCOPES:
                raise ValueError(f"{source}: key {name!r} has an unknown scope (use one of: {', '.join(KEY_SCOPES)})")
            scope, secret = tail.lower(), head.strip()
        if scopes is not None:
            scopes[name] = scope
        if any(existing[0] == name for existing in entries):
            raise ValueError(f"{source} names the key {name!r} twice")
        if secret.lower().startswith("sha256:"):
            digest_hex = secret[7:].strip().lower()
            if not re.fullmatch(r"[0-9a-f]{64}", digest_hex):
                raise ValueError(f"{source}: the sha256 digest of key {name!r} must be 64 hexadecimal digits")
            entries.append((name, bytes.fromhex(digest_hex), None))
        else:
            entries.append((name, _key_digest(secret), len(secret)))
    return entries


def dashboard_api_keys():
    """Every configured dashboard credential as [(name, sha256 digest, plaintext length or None)]:
    NOTEBOOK_API_DASHBOARD_KEYS and/or the file NOTEBOOK_API_DASHBOARD_KEYS_FILE, plus the legacy
    NOTEBOOK_API_DASHBOARD_KEY as "default". Raises ValueError for a malformed configuration."""
    return dashboard_api_keys_and_scopes()[0]


def dashboard_api_keys_and_scopes():
    """(entries, {name: scope}). A key's scope is "read", "compile" or "admin" (`name:key:scope`);
    a key with none, and the legacy NOTEBOOK_API_DASHBOARD_KEY, is "admin", as before."""
    scopes = {}
    entries = []
    inline = os.getenv("NOTEBOOK_API_DASHBOARD_KEYS", "")
    if inline.strip():
        entries += _parse_named_keys(inline, "NOTEBOOK_API_DASHBOARD_KEYS", scopes)
    path = os.getenv("NOTEBOOK_API_DASHBOARD_KEYS_FILE", "").strip()
    if path:
        try:
            text = Path(path).read_text()
        except OSError:
            raise ValueError("NOTEBOOK_API_DASHBOARD_KEYS_FILE could not be read") from None
        entries += _parse_named_keys(text, "NOTEBOOK_API_DASHBOARD_KEYS_FILE", scopes)
    legacy = dashboard_api_key()
    if legacy:
        entries.append(("default", _key_digest(legacy), len(legacy)))
        scopes["default"] = "admin"
    names = [entry[0] for entry in entries]
    if len(names) != len(set(names)):
        raise ValueError("dashboard key names must be unique (\"default\" is the legacy NOTEBOOK_API_DASHBOARD_KEY)")
    return entries, scopes


# Scope authorization. Anything not listed below needs "admin", so a new mutating route is
# admin-only until it is deliberately classified.
_SCOPE_RANK = {"read": 0, "compile": 1, "admin": 2}
_COMPILE_POSTS = frozenset({
    "/api/upload", "/api/upload/batch", "/api/notebooks/import", "/api/compile", "/api/inspect",
    "/api/validate", "/api/export-openapi", "/api/export-sdk",
})


def required_scope(method, path):
    """The least scope that may call `method path`: read for safe methods; compile for uploading and
    compiling a notebook and the non-mutating previews/exports; admin for everything else
    (delete, rename, restore, tags, deploy, history, governance and experimental routes)."""
    method = method.upper()
    if method in ("GET", "HEAD", "OPTIONS"):
        return "read"
    path = path.rstrip("/") or "/"
    if method == "POST" and (path in _COMPILE_POSTS or (path.startswith("/api/") and path.endswith("-preview"))):
        return "compile"
    return "admin"


def scope_allows(scope, method, path):
    return _SCOPE_RANK.get(scope, 2) >= _SCOPE_RANK[required_scope(method, path)]


def authenticate_dashboard_key(supplied, entries):
    """The name of the key `supplied` matches, or None. Every entry is compared, in constant time,
    so neither the response time nor an early exit reveals which keys exist."""
    digest = _key_digest(supplied)
    matched = None
    for name, expected, _length in entries:
        if hmac.compare_digest(digest, expected):
            matched = name
    return matched


def validate_dashboard_auth_config(host, environ=None):
    """Startup check: the key configuration parses, and a production deployment reachable from the
    network uses only strong keys (at least 16 characters; digest entries are trusted). Raises
    ValueError; the message never contains a key."""
    entries = dashboard_api_keys()
    environ = os.environ if environ is None else environ
    production = environ.get("NOTEBOOK2API_ENVIRONMENT", "").strip().lower() in {"production", "prod"}
    if production and not host_is_loopback(host):
        weak = [name for name, _d, length in entries if length is not None and length < _MIN_PRODUCTION_KEY_LENGTH]
        if weak:
            raise ValueError(
                f"Refusing to start: dashboard key(s) {', '.join(sorted(weak))} are shorter than "
                f"{_MIN_PRODUCTION_KEY_LENGTH} characters, which is too weak for a production deployment "
                "reachable from the network"
            )
    return entries


# Starlette's TestClient reports this literal as the peer; a real socket peer is an IP
# address (or absent, for a unix socket), so it never matches a network client.
_IN_PROCESS_TEST_PEER = "testclient"


def dashboard_auth_failure_limit_per_minute():
    """Failed-authentication attempts one client (its peer address) may make
    per minute before further requests from it are answered 429 -- until the
    minute ends and *before* the key is checked, so the right key from that
    address is refused meanwhile. Default 30; 0 disables. Behind a reverse
    proxy every caller shares the proxy's address, so size it for that.
    NOTEBOOK_API_DASHBOARD_AUTH_FAILURE_LIMIT_PER_MINUTE."""
    raw = os.getenv("NOTEBOOK_API_DASHBOARD_AUTH_FAILURE_LIMIT_PER_MINUTE", "30").strip() or "30"
    try:
        return max(0, int(raw))
    except ValueError:
        raise RuntimeError(
            f"NOTEBOOK_API_DASHBOARD_AUTH_FAILURE_LIMIT_PER_MINUTE must be a whole number, got {raw!r}"
        ) from None


_AUTH_FAILURE_WINDOW_SECONDS = 60
_AUTH_FAILURE_MAX_CLIENTS = 10000
_AUTH_FAILURES = {}
_AUTH_FAILURES_LOCK = threading.Lock()


def dashboard_trusted_proxies():
    """Networks (NOTEBOOK_API_DASHBOARD_TRUSTED_PROXIES: comma-separated IPs or
    CIDR ranges) whose X-Forwarded-For may identify the real client for the
    failed-authentication throttle. Empty (default): no proxy is trusted."""
    import ipaddress

    networks = []
    for part in os.getenv("NOTEBOOK_API_DASHBOARD_TRUSTED_PROXIES", "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            networks.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            raise RuntimeError(
                f"NOTEBOOK_API_DASHBOARD_TRUSTED_PROXIES must list IP addresses or CIDR ranges, got {part!r}"
            ) from None
    return tuple(networks)


def _auth_client(request):
    """The throttle key: the peer address, or -- only when the peer is a trusted
    proxy -- the first X-Forwarded-For hop from the right that is not itself
    a trusted proxy."""
    import ipaddress

    peer = request.client.host if request.client else "unknown"
    networks = dashboard_trusted_proxies()

    def trusted(address):
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        return any(ip in n for n in networks if ip.version == n.version)

    if not networks or not trusted(peer):
        return peer

    for hop in reversed([h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]):
        try:
            ipaddress.ip_address(hop)
        except ValueError:
            return peer
        if not trusted(hop):
            return hop
    return peer


def _auth_retry_after_if_blocked(client):
    """Seconds until `client` may try again, or None when it is not blocked."""
    limit = dashboard_auth_failure_limit_per_minute()
    if limit <= 0:
        return None
    now = time.time()
    with _AUTH_FAILURES_LOCK:
        started, count = _AUTH_FAILURES.get(client, (now, 0))
        if now - started >= _AUTH_FAILURE_WINDOW_SECONDS:
            _AUTH_FAILURES.pop(client, None)
            return None
        if count >= limit:
            return max(1, int(_AUTH_FAILURE_WINDOW_SECONDS - (now - started)))
    return None


def _auth_record_failure(client):
    """Count one failed attempt by `client`; returns how many it has made in the current window
    (0 when the throttle is off)."""
    if dashboard_auth_failure_limit_per_minute() <= 0:
        return 0
    now = time.time()
    with _AUTH_FAILURES_LOCK:
        started, count = _AUTH_FAILURES.get(client, (now, 0))
        if now - started >= _AUTH_FAILURE_WINDOW_SECONDS:
            started, count = now, 0
        if client not in _AUTH_FAILURES and len(_AUTH_FAILURES) >= _AUTH_FAILURE_MAX_CLIENTS:
            for key in [k for k, (t, _) in _AUTH_FAILURES.items() if now - t >= _AUTH_FAILURE_WINDOW_SECONDS]:
                del _AUTH_FAILURES[key]
            while len(_AUTH_FAILURES) >= _AUTH_FAILURE_MAX_CLIENTS:
                _AUTH_FAILURES.pop(next(iter(_AUTH_FAILURES)))
        _AUTH_FAILURES[client] = (started, count + 1)
        return count + 1


def _peer_is_local(request):
    client = request.client
    return client is None or client.host == _IN_PROCESS_TEST_PEER or host_is_loopback(client.host)


def _is_frontend_shell_file(request):
    """Whether this GET/HEAD names a file of the built frontend (HTML, JS, CSS).

    A browser cannot attach X-API-Key to a page load or a <script> request, so
    with the key required on these too the bundled dashboard could never open.
    The files hold no data and no secret; every /api and other route still
    needs the key.
    """

    dist_dir = getattr(request.app.state, "frontend_dist", None)

    if dist_dir is None or request.method not in ("GET", "HEAD"):
        return False

    relative = request.url.path.lstrip("/") or "index.html"

    if "\\" in relative or "\x00" in relative:
        return False

    root = Path(dist_dir).resolve()
    candidate = (root / relative).resolve()

    return candidate.is_file() and root in candidate.parents


def _record_denied_scope(key_name, key_scope, needed, request):
    """One governance audit record per authorization denial (the key's name, never its value)."""
    try:
        from backend.routes.upload import _record_governance

        _record_governance(
            "dashboard.authorization_denied",
            {"notebook": None},
            "failure",
            error=f"{request.method} {request.url.path[:120]}: scope {key_scope} < required {needed}",
            actor=key_name,
        )
    except Exception:  # noqa: BLE001 - auditing must not turn a 403 into a 500
        logging.getLogger("uvicorn.error").warning("could not audit a denied dashboard request")


@app.middleware("http")
async def _require_dashboard_api_key(request: Request, call_next):
    """Answer 401 unless the X-API-Key header matches
    NOTEBOOK_API_DASHBOARD_KEY (when it is set). CORS preflights are
    exempt: browsers send them without credentials. Registered before
    CORSMiddleware, like the middlewares above, so a 401 still carries
    CORS headers. The key is never logged or echoed.
    """

    try:
        entries, key_scopes = dashboard_api_keys_and_scopes()
    except ValueError:
        # Fail closed without saying what is wrong (startup validation reports the details).
        return JSONResponse(status_code=503, content={"detail": "Dashboard authentication is misconfigured"})

    if not entries and request.method != "OPTIONS" and not _peer_is_local(request):
        # No key configured: only this machine may use the dashboard. This holds for
        # every launch path (`uvicorn backend.dashboard:app --host 0.0.0.0`,
        # backend.app:app, a container CMD), not just `python -m backend.dashboard`,
        # whose startup check cannot see how another launcher binds the socket.
        return JSONResponse(status_code=401, content={"detail": (
            "Remote access is refused until NOTEBOOK_API_DASHBOARD_KEY is set"
        )})

    if entries and request.method != "OPTIONS" and not _is_frontend_shell_file(request):
        supplied = request.headers.get("X-API-Key", "")
        client = _auth_client(request)

        retry_after = _auth_retry_after_if_blocked(client)

        if retry_after is not None:
            auth_metrics.record_throttled()
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many failed authentication attempts; try again later"},
                headers={"Retry-After": str(retry_after)},
            )

        key_name = authenticate_dashboard_key(supplied, entries)

        if key_name is not None:
            request.state.dashboard_actor = key_name
            key_scope = key_scopes.get(key_name, "admin")
            if not scope_allows(key_scope, request.method, request.url.path):
                needed = required_scope(request.method, request.url.path)
                auth_metrics.record_denied(client, key_name, key_scope, needed)
                _record_denied_scope(key_name, key_scope, needed, request)
                return JSONResponse(status_code=403, content={
                    "detail": f"This key's scope ({key_scope}) does not allow {request.method} requests here; the {needed} scope is required"
                })

        if key_name is None:
            window_count = _auth_record_failure(client)
            auth_metrics.record_failure(
                client, "missing_key" if not supplied else "invalid_key",
                window_count, dashboard_auth_failure_limit_per_minute(),
            )
            return JSONResponse(status_code=401, content={"detail": "Invalid API key"})

    return await call_next(request)


# Enable CORS for the frontend, credentialed requests restricted to a
# known allowlist -- see allowed_origins() docstring. Added *after*
# _enforce_dashboard_rate_limit/_enforce_dashboard_max_request_body_bytes
# above (see either middleware's own docstring for why the ordering here
# matters) so this ends up the outermost middleware, wrapping both --
# every response,
# including a 429 from it, still gets CORS headers applied.
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Browsers only ever expose a small built-in safelist of *response*
    # headers to cross-origin JS (Cache-Control, Content-Language,
    # Content-Length, Content-Type, Expires, Last-Modified, Pragma) --
    # everything else this dashboard's own endpoints already send (ETag/
    # X-Content-SHA256/X-Bundle-SHA256 for conditional-GET, Content-
    # Disposition on GET /api/download's own zip filename,
    # X-Notebook-Changed-Since-Compile, X-RateLimit-*/Retry-After from
    # _enforce_dashboard_rate_limit above) is invisible to
    # `fetch(...).headers.get(...)` cross-origin no matter what
    # allow_origins/allow_headers above are set to, unless explicitly
    # listed here. The bundled frontend (same-origin, or a dev server
    # proxying this API) never hit this, but any other browser-based
    # client calling this dashboard cross-origin could never read a
    # single one of these -- even though the server sent them every time.
    expose_headers=[
        "ETag",
        "X-Content-SHA256",
        "X-Bundle-SHA256",
        "X-Notebook-Changed-Since-Compile",
        "Content-Disposition",
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
        "X-RateLimit-Reset",
        "Retry-After",
    ],
)

# Collect request metrics for the governance API endpoints
register_governance_metrics_middleware(app)


@app.middleware("http")
async def _add_security_headers(request: Request, call_next):
    """Stamp baseline OWASP-recommended hardening headers on every
    response this dashboard sends -- registered last (see
    _enforce_dashboard_rate_limit's own docstring above for why ordering
    matters here: Starlette's add_middleware inserts each new middleware
    at the *front* of its internal list, then wraps outward-in over that
    list in reverse, so whichever is added last ends up outermost), so
    these headers land on literally every response -- a 429 from the rate
    limiter above, a CORS preflight, an upload/compile 4xx/5xx, all of
    it -- not just the successful ones a handler-level fix would only
    ever reach.

    Unlike CORSMiddleware's own allow_origins (restricted to an explicit
    allowlist) or dashboard_rate_limit_per_minute() (opt-in, 0 by
    default), these are unconditional: nothing here can reject a
    legitimate request, so there's no reason to gate baseline hardening
    behind an env var an operator has to remember to set, the same
    "closes an actual gap with no functional downside" reasoning
    NOTEBOOK_API_RATE_LIMIT_PER_MINUTE's own new X-RateLimit-* headers
    already applied. X-Content-Type-Options blocks a browser from ever
    guessing a response is something other than what Content-Type
    already says it is; X-Frame-Options blocks embedding any response
    (including this dashboard's own /docs) in a third-party <iframe>;
    Referrer-Policy stops this dashboard's own URL -- which can itself
    carry sensitive path segments, e.g. an uploaded notebook's filename
    in an error message -- from leaking into the Referer header of a
    request /docs' own "Try it out", or a link in some response body,
    makes to a different origin.
    """

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


# Registered last -- see _add_security_headers' own docstring above for
# why that makes this the outermost middleware -- so it compresses the
# truly final response body, after every other layer has already
# finished with it. Only kicks in when a caller's own Accept-Encoding
# actually says it can decode gzip, so this changes nothing for one that
# doesn't. Several of this dashboard's own responses are large by design
# -- an unpaginated GET /api/notebooks catalog, a CSV export, an exported
# OpenAPI schema, GET /api/notebooks/{filename}/versions history -- and
# previously always went out uncompressed, a real bandwidth/latency cost
# for any caller reached over a slow or metered link with no way to avoid
# it short of a reverse proxy in front of this dashboard doing the
# compression itself. Starlette's own default minimum_size (500 bytes) is
# left as-is -- below that, gzip's own framing overhead can make a
# compressed response larger than the original.
app.add_middleware(GZipMiddleware)


@app.middleware("http")
async def _add_process_time_header(request: Request, call_next):
    """Stamp X-Process-Time-Ms (wall-clock request handling time, in
    milliseconds) on every response -- registered last, after GZip
    itself (see that middleware's own comment on why registration order
    determines outermost-ness), so the timer spans every other layer too
    (rate limiting, compression, the endpoint itself), reporting what a
    real client actually experienced rather than just handler time.
    Before this, an operator had no way to see per-request latency for
    this dashboard's own API short of instrumenting it externally (a
    reverse proxy's own access log, an APM agent) -- every compiled
    app's own equivalent latency signal (per-request X-RateLimit-Reset
    aside) had the same gap until now.
    """

    start_time = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Process-Time-Ms"] = (
        f"{(time.perf_counter() - start_time) * 1000:.2f}"
    )
    return response


@app.middleware("http")
async def _add_request_id_header(request: Request, call_next):
    """Stamp X-Request-ID on every response -- registered last, outermost
    (wrapping X-Process-Time-Ms above, same reasoning as that
    middleware's own comment), so this request's own id is present even
    on a response an earlier layer short-circuits (a 429 from the rate
    limiter). Honors a caller-supplied X-Request-ID instead of always
    minting a fresh one -- e.g. from an upstream gateway that already
    assigns one to correlate a single logical request across several
    downstream services -- so a trace started upstream doesn't fork into
    two disconnected ids the moment it reaches this dashboard; only
    generates a new uuid4 when the caller didn't send one at all. Mirrors
    the identical addition to every compiled app's own generated
    middleware stack (api_generator.py).
    """

    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.middleware("http")
async def _log_dashboard_request_json(request: Request, call_next):
    """When dashboard_json_logs_enabled() is set, print one
    {"timestamp", "request_id", "method", "path", "status_code",
    "duration_ms"} JSON line per request to stdout -- see that
    function's own docstring above for the gap this closes.

    Registered last -- outermost, wrapping every other middleware above
    (see _add_request_id_header's own docstring for why "registered
    last" means outermost) -- so this reads back the *final*
    X-Request-ID/X-Process-Time-Ms headers _add_request_id_header/
    _add_process_time_header already set on every response, the
    identical "can never drift from what a caller correlating its own
    logs against those exact same headers already sees" guarantee
    _log_request_json already gives every generated app (see
    api_generator.py), rather than this middleware re-deriving either
    value itself.

    print(..., flush=True) rather than the stdlib logging module -- this
    dashboard configures no logging of its own anywhere else (uvicorn
    configures its own independently), the identical choice
    _log_request_json already makes and for the same reason: unbuffered
    stdout is what `docker logs`/a log-aggregation pipeline actually
    tails in real time.

    Off by default, purely additive alongside uvicorn's own access log --
    an existing deployment that hasn't opted in via
    NOTEBOOK_API_DASHBOARD_JSON_LOGS sees byte-for-byte the same stdout
    output as before this existed.
    """

    response = await call_next(request)

    if dashboard_json_logs_enabled():

        print(json.dumps({
            "timestamp": time.time(),
            "request_id": response.headers.get("X-Request-ID"),
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": float(response.headers.get("X-Process-Time-Ms", "0")),
        }), flush=True)

    return response


# Include API routes
app.include_router(upload_router)
app.include_router(governance_metrics_router)
app.include_router(governance_health_router)

def experimental_routers_enabled(environ=None):
    """The cluster, plugins, AI, security (v1/v2), pipelines and performance routers are unused by the
    dashboard UI and include operations that read server directories and import modules, so they are
    mounted only on explicit opt-in (NOTEBOOK2API_EXPERIMENTAL_ROUTERS=1)."""
    environ = os.environ if environ is None else environ
    return str(environ.get("NOTEBOOK2API_EXPERIMENTAL_ROUTERS", "")).strip().lower() in {"1", "true", "yes"}


def _mount_experimental_routers():
    # Security, Authentication & Access Control subsystem
    app.include_router(security_authentication_router)
    app.include_router(security_api_keys_router)
    app.include_router(security_jwt_router)
    app.include_router(security_rbac_router)
    app.include_router(security_permissions_router)
    app.include_router(security_session_router)
    app.include_router(security_audit_router)
    app.include_router(security_policy_router)
    app.include_router(security_secrets_router)
    app.include_router(security_analytics_router)
    app.include_router(security_dashboard_router)
    app.include_router(security_export_router)
    bootstrap_security_subsystem()

    # Plugin subsystem
    # Route ordering matters here: plugin_registry_router's catch-all
    # "/plugins/{name}" route must be included LAST, since any other router
    # adding a static second-segment path under "/plugins/" (e.g. "/loaded",
    # "/extensions") would otherwise be shadowed by it.
    #   - loader's static "/plugins/loaded" would otherwise be shadowed by the
    #     registry's "/plugins/{name}" route.
    #   - lifecycle's "DELETE /plugins/{plugin}" (uninstall) intentionally takes
    #     over that path from the registry's plain "DELETE /plugins/{name}"
    #     (unregister), since uninstall unloads the plugin and unregisters it
    #     from the catalog as part of the same transition.
    #   - extension_api's static "/plugins/extensions" would otherwise be
    #     shadowed by the registry's "/plugins/{name}" route.
    #   - event_system's static "/plugins/events" would otherwise be shadowed
    #     by the registry's "/plugins/{name}" route (its "/plugins/hooks" and
    #     "/plugins/hooks/{hook}" paths don't collide with anything).
    #   - plugin_dependencies's static "/plugins/load-order" would otherwise be
    #     shadowed by the registry's "/plugins/{name}" route (its
    #     "/plugins/dependencies" and "/plugins/dependencies/{plugin}" paths
    #     don't collide with anything).
    app.include_router(plugin_loader_router)
    app.include_router(plugin_lifecycle_router)
    app.include_router(extension_api_router)
    app.include_router(event_system_router)
    app.include_router(plugin_dependencies_router)
    # plugin_config's routes are all 3+ segments ("/plugins/{plugin}/config",
    # "/plugins/{plugin}/config/validate"), so unlike the routers above it
    # doesn't collide with the registry's "/plugins/{name}" and can be included
    # in any order relative to it.
    app.include_router(plugin_config_router)
    # plugin_sandbox's routes are all under "/plugins/sandbox" (2+ segments,
    # with "sandbox" as a static literal), and the registry has no POST route
    # at "/plugins/{name}" for it to collide with, so this is order-independent
    # relative to plugin_registry_router too.
    app.include_router(plugin_sandbox_router)
    # plugin_packaging's routes are "POST /plugins/package", "GET
    # /plugins/package/{plugin}", and "POST /plugins/import" - the registry has
    # no POST route at "/plugins/{name}" and its GET route is 2 segments (not 3),
    # so this is order-independent relative to plugin_registry_router too.
    app.include_router(plugin_packaging_router)
    # plugin_marketplace's bare "GET /plugins/marketplace" would otherwise be
    # shadowed by the registry's "/plugins/{name}" route (its "/search",
    # "/install", and "/update/{plugin}" sub-paths don't collide with anything).
    app.include_router(plugin_marketplace_router)
    # plugin_analytics's bare "GET /plugins/analytics" would otherwise be
    # shadowed by the registry's "/plugins/{name}" route (its "/summary" and
    # "/trends" sub-paths don't collide with anything).
    app.include_router(plugin_analytics_router)
    # plugin_dashboard's bare "GET /plugins/dashboard" would otherwise be
    # shadowed by the registry's "/plugins/{name}" route (its "/registry",
    # "/runtime", "/marketplace", and "/analytics" sub-paths don't collide with
    # anything).
    app.include_router(plugin_dashboard_router)
    app.include_router(plugin_registry_router)
    bootstrap_plugin_framework()

    # Caching & Performance Optimization subsystem
    app.include_router(performance_cache_router)
    app.include_router(performance_profile_router)
    app.include_router(performance_pool_router)
    app.include_router(performance_dashboard_router)
    app.include_router(performance_export_router)
    bootstrap_performance_subsystem()

    # Data Pipeline & ETL Framework subsystem
    # Route ordering matters here for the same reason as the plugin subsystem
    # above: pipeline_registry_router's catch-all "/pipelines/{name}" route must
    # be included LAST, since any other router adding a static second-segment
    # path under "/pipelines/" (e.g. "/sources", "/schemas", "/schedules")
    # would otherwise be shadowed by it.
    #   - data_sources's bare "GET /pipelines/sources" would otherwise be
    #     shadowed by the registry's "/pipelines/{name}" route.
    #   - schema_registry's bare "GET /pipelines/schemas" would otherwise be
    #     shadowed by the registry's "/pipelines/{name}" route.
    #   - pipeline_scheduler's bare "GET /pipelines/schedules" would otherwise
    #     be shadowed by the registry's "/pipelines/{name}" route.
    #   - pipeline_executor's "GET /pipelines/runs" would otherwise be shadowed
    #     by the registry's "/pipelines/{name}" route (its "POST /execute" and
    #     "/runs/{run}" sub-paths don't collide with anything).
    #   - checkpoint_manager's bare "GET /pipelines/checkpoints" would otherwise
    #     be shadowed by the registry's "/pipelines/{name}" route.
    #   - pipeline_analytics's bare "GET /pipelines/analytics" would otherwise
    #     be shadowed by the registry's "/pipelines/{name}" route.
    #   - dashboard's bare "GET /pipelines/dashboard" would otherwise be
    #     shadowed by the registry's "/pipelines/{name}" route.
    # transformation_engine, data_validation, etl_engine, and export are
    # order-independent relative to the registry: none of them define a bare
    # 2-segment GET under "/pipelines/", so they never collide with "/{name}".
    app.include_router(pipeline_data_sources_router)
    app.include_router(pipeline_transformation_router)
    app.include_router(pipeline_validation_router)
    app.include_router(pipeline_etl_router)
    app.include_router(pipeline_schema_registry_router)
    app.include_router(pipeline_scheduler_router)
    app.include_router(pipeline_executor_router)
    app.include_router(pipeline_checkpoint_router)
    app.include_router(pipeline_analytics_router)
    app.include_router(pipeline_dashboard_router)
    app.include_router(pipeline_export_router)
    app.include_router(pipeline_registry_router)
    bootstrap_pipeline_subsystem()

    # AI Model Management & Inference Platform subsystem
    # Route ordering matters here for the same reason as the plugin and pipeline
    # subsystems above: model_registry_router's catch-all "/ai/models/{name}"
    # route must be included LAST, since any other router adding a static
    # second-segment path under "/ai/models/" (e.g. "/loaded") would otherwise be
    # shadowed by it.
    #   - model_loader's bare "GET /ai/models/loaded" would otherwise be shadowed
    #     by the registry's "/ai/models/{name}" route (its "POST /load",
    #     "/reload/{model}", and "/unload/{model}" sub-paths don't collide with
    #     anything).
    # inference_engine, model_versioning, prompt_templates, batch_inference,
    # model_routing, model_benchmark, model_deployment, inference_analytics, and
    # dashboard/export are order-independent relative to the registry: none of
    # them define a bare 2-segment GET under "/ai/models/", so they never collide
    # with "/{name}".
    app.include_router(ai_model_loader_router)
    app.include_router(ai_inference_router)
    app.include_router(ai_model_versioning_router)
    app.include_router(ai_prompt_templates_router)
    app.include_router(ai_batch_inference_router)
    app.include_router(ai_model_routing_router)
    app.include_router(ai_model_benchmark_router)
    app.include_router(ai_model_deployment_router)
    app.include_router(ai_inference_analytics_router)
    app.include_router(ai_dashboard_router)
    app.include_router(ai_export_router)
    app.include_router(ai_model_registry_router)
    bootstrap_ai_subsystem()

    # Distributed Execution & Compute Orchestration subsystem
    # Each router owns its own distinct top-level path segment under "/cluster/"
    # (workers, discovery, dispatch, tasks, executions, health, schedule/
    # scheduler, scaling, recovery, analytics, dashboard, export), so unlike the
    # plugin/pipeline/ai registries above there's no catch-all "/{name}" route
    # for a sibling router's static path to collide with, and inclusion order is
    # not significant here.
    app.include_router(cluster_worker_registry_router)
    app.include_router(cluster_worker_discovery_router)
    app.include_router(cluster_job_dispatcher_router)
    app.include_router(cluster_task_serializer_router)
    app.include_router(cluster_execution_coordinator_router)
    app.include_router(cluster_worker_health_router)
    app.include_router(cluster_distributed_scheduler_router)
    app.include_router(cluster_auto_scaling_router)
    app.include_router(cluster_fault_tolerance_router)
    app.include_router(cluster_analytics_router)
    app.include_router(cluster_dashboard_router)
    app.include_router(cluster_export_router)
    bootstrap_cluster_subsystem()


if experimental_routers_enabled():
    _mount_experimental_routers()


def _frontend_index_file():
    """The built frontend's index.html when one is mounted, else None."""

    dist_dir = getattr(app.state, "frontend_dist", None)
    index = Path(dist_dir) / "index.html" if dist_dir is not None else None

    return index if index is not None and index.is_file() else None


@app.get("/")
async def root():
    index = _frontend_index_file()

    if index is not None:
        return FileResponse(index)

    return {
        "status": "running",
        "service": "notebook-to-api Dashboard API",
        "version": NOTEBOOK_TO_API_VERSION,
        "docs": "/docs"
    }


# Build output directory for the bundled React frontend (see
# frontend/package.json's own "build" script, `vite build`) -- not
# checked into this repo, only ever produced by actually running that
# build.
FRONTEND_DIST_DIR = PROJECT_ROOT / "frontend" / "dist"


# URL prefixes owned by the API. Anything under them that no route matched is a 404 for every
# method and every environment: the frontend files must never answer for the API namespace
# (a DELETE would get 405 and a GET the SPA shell instead of "not found"), which is what made the
# outcome for an absolute version id like %2Fetc%2Fpasswd depend on whether the frontend was built.
_API_PREFIXES = ("api/", "governance/")


class _FrontendFiles(StaticFiles):
    async def get_response(self, path, scope):
        if path.startswith(_API_PREFIXES) or path in ("api", "governance"):
            raise HTTPException(status_code=404)

        return await super().get_response(path, scope)


def mount_frontend_static_files(app, dist_dir=FRONTEND_DIST_DIR):
    """Mount a built frontend as static files served from "/", so this
    dashboard can actually serve the React frontend its own module
    docstring above already promises, not just the /api/* endpoints it
    talks to.

    `from fastapi.staticfiles import StaticFiles` was imported here and
    never used at all: the local dev workflow (start-dashboard.sh) runs
    the frontend as a completely separate `npm run dev` process on its
    own port, CORS-linked to this one via allowed_origins() above -- but
    there was no equivalent for an actual deployment. A production
    deploy wanting a single process to run, with no second frontend
    server to stand up and keep alive alongside it, had no way to get
    that: visiting "/" only ever returned root()'s plain
    {"status": "running", ...} JSON, and every other frontend route
    (whatever client-side path the SPA itself defines) 404'd outright,
    with no way to reach the frontend through this server at all.

    A no-op if `dist_dir` doesn't exist -- frontend/dist is a build
    artifact (`npm run build`), not something checked into this repo, so
    a fresh checkout or a backend-only deployment that never runs the
    frontend build has nothing to serve. StaticFiles itself raises
    RuntimeError for a missing directory, which would otherwise crash
    importing this entire module -- and every route it defines -- over a
    frontend that simply hasn't been built, exactly the same
    fail-safe-not-fail-crashing precedent every other optional,
    environment-dependent piece of this dashboard already follows.

    Mounted at "/" specifically (not a sub-path): frontend/vite.config.js
    configures no "base", so Vite's own build assumes root-relative asset
    paths ("/assets/..."); anything other than "/" would 404 on every one
    of them. Safe alongside every route already registered above:
    FastAPI/Starlette matches routes in registration order, and this is
    always called last (after every app.include_router/@app.get in this
    module), so each of those already matches its own exact path before
    this catch-all mount is ever consulted. GET / is the exception by design:
    root() itself returns the mounted index.html (its JSON status when no
    frontend is built).
    """
    if not dist_dir.is_dir():
        return

    app.state.frontend_dist = dist_dir
    app.mount("/", _FrontendFiles(directory=str(dist_dir), html=True), name="frontend")


mount_frontend_static_files(app)


def dashboard_host():
    """Host the dashboard API server binds to when run directly (`python
    -m backend.dashboard`).

    Matches the app's existing NOTEBOOK_API_* env-var convention (see
    allowed_origins() above, MAX_UPLOAD_BYTES and
    DEPLOY_SUBPROCESS_TIMEOUT_SECONDS in routes/upload.py) rather than the
    fixed "0.0.0.0" previously hardcoded directly into the uvicorn.run()
    call below, with no way to bind to a specific interface instead
    without editing this file.
    """
    return os.getenv("NOTEBOOK_API_DASHBOARD_HOST", "127.0.0.1")


def host_is_loopback(host):
    """True for localhost and loopback addresses (127.0.0.0/8, ::1) -- the
    only interfaces that cannot be reached from another machine. Anything
    else, including 0.0.0.0, :: and unresolvable names, is treated as
    network-exposed."""
    import ipaddress

    host = (host or "").strip().strip("[]")

    if host.lower() == "localhost":
        return True

    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_dashboard_exposure(host, key):
    """Refuse to serve the dashboard's administrative API (secrets, API
    keys, upload, delete, compile) on a network interface without
    authentication. Raises ValueError, before uvicorn starts, when `host`
    is not loopback and `key` (NOTEBOOK_API_DASHBOARD_KEY) is empty. The
    key itself is never included in the message."""
    if host_is_loopback(host) or key or dashboard_api_keys():
        return

    raise ValueError(
        f"Refusing to start: NOTEBOOK_API_DASHBOARD_HOST={host!r} exposes the "
        "dashboard on a network interface, but NOTEBOOK_API_DASHBOARD_KEY is "
        "not set, so every request (including secrets, API keys, upload, "
        "delete and compile) would be unauthenticated. Set "
        "NOTEBOOK_API_DASHBOARD_KEY (or NOTEBOOK_API_DASHBOARD_KEYS) to a secret value (clients send it as the "
        "X-API-Key header), or bind to 127.0.0.1."
    )


def dashboard_port():
    """Port the dashboard API server binds to when run directly.

    Defaults to 8001 -- the same port previously hardcoded here, and the
    one the bundled frontend (see frontend/src/components/Dashboard.jsx
    and NotebookUpload.jsx) already calls the dashboard API on. This is
    deliberately a different port than the *generated* app's own default
    of 8000 (see `serve --port` and the generated Dockerfile's EXPOSE
    8000): the dashboard and a compiled/served app are two separate
    services a developer commonly runs side by side, and defaulting both
    to the same port would make that impossible without one of them
    already overriding it.
    """
    return int(os.getenv("NOTEBOOK_API_DASHBOARD_PORT", "8001"))


# Values NOTEBOOK_API_DASHBOARD_RELOAD (below) treats as "off" -- matched
# case-insensitively so "False"/"FALSE"/"0" all behave identically, the
# same tolerance every other project this convention is modeled on
# (Django's DEBUG, Flask's FLASK_DEBUG, ...) already extends to a
# hand-typed env var.
_DASHBOARD_RELOAD_FALSY_VALUES = frozenset({"false", "0", "no", "off"})


def dashboard_reload():
    """Whether `python -m backend.dashboard` runs uvicorn with hot-reload
    enabled.

    Matches the app's existing NOTEBOOK_API_DASHBOARD_* env-var
    convention (see dashboard_host()/dashboard_port() above) rather than
    the fixed reload=True previously hardcoded directly into the
    uvicorn.run() call below, with no way to turn it off without editing
    this file.

    Defaults to True, preserving this project's own existing local-dev
    workflow exactly as it already is: start-dashboard.sh runs this
    module directly and relies on hot-reload while iterating on the
    dashboard itself. But uvicorn's own reload mode spawns a supervising
    subprocess that restarts the whole server on every filesystem change
    under this process's own working directory -- real, avoidable
    overhead (and a startup/shutdown wrinkle: reload mode also silently
    ignores a real deployment's own `workers=N`, per uvicorn's own
    precedence between the two) for a production deployment that never
    edits this file's own source after it's built. Set
    NOTEBOOK_API_DASHBOARD_RELOAD=false to run this module directly in
    that kind of deployment without needing an external process
    manager/reverse proxy just to invoke uvicorn without it.
    """
    return os.getenv(
        "NOTEBOOK_API_DASHBOARD_RELOAD", "true"
    ).strip().lower() not in _DASHBOARD_RELOAD_FALSY_VALUES


def dashboard_ssl_config():
    """(ssl_keyfile, ssl_certfile) for `python -m backend.dashboard`'s own
    uvicorn.run() call below, read from
    NOTEBOOK_API_DASHBOARD_SSL_KEYFILE/NOTEBOOK_API_DASHBOARD_SSL_CERTFILE
    -- (None, None) if neither is set, the same "unset changes nothing"
    convention dashboard_host()/dashboard_port() above already follow:
    plain HTTP, exactly the only thing this module could ever serve
    before this existed.

    Without this, running this dashboard directly -- rather than behind
    an external reverse proxy/load balancer already terminating TLS --
    had no way to serve HTTPS at all: every one of its own endpoints
    (including POST /api/upload and POST /api/compile) would otherwise
    have to be reachable in plaintext for any deployment with no such
    proxy already sitting in front of it.

    Raises ValueError -- at startup, before uvicorn.run is ever called --
    if only one of the two is set: a keyfile with no matching certfile
    (or vice versa) isn't a configuration uvicorn could do anything
    useful with, and failing fast here beats it failing less obviously
    once uvicorn itself tries to load just the one that was actually
    given.
    """
    ssl_keyfile = os.getenv("NOTEBOOK_API_DASHBOARD_SSL_KEYFILE")
    ssl_certfile = os.getenv("NOTEBOOK_API_DASHBOARD_SSL_CERTFILE")

    if bool(ssl_keyfile) != bool(ssl_certfile):

        set_name = (
            "NOTEBOOK_API_DASHBOARD_SSL_KEYFILE" if ssl_keyfile
            else "NOTEBOOK_API_DASHBOARD_SSL_CERTFILE"
        )
        unset_name = (
            "NOTEBOOK_API_DASHBOARD_SSL_CERTFILE" if ssl_keyfile
            else "NOTEBOOK_API_DASHBOARD_SSL_KEYFILE"
        )

        raise ValueError(
            f"{set_name} is set but {unset_name} is not -- both must be "
            "set to serve HTTPS, or neither to serve plain HTTP."
        )

    return ssl_keyfile, ssl_certfile


def dashboard_log_level():
    """uvicorn's own --log-level for `python -m backend.dashboard`, via
    NOTEBOOK_API_DASHBOARD_LOG_LEVEL -- unset (the default, None) leaves
    uvicorn's own default ("info") exactly as before, the same "unset
    changes nothing" convention every other NOTEBOOK_API_DASHBOARD_* knob
    above already follows. Lets a production deployment quiet (or
    increase) this dashboard's own log verbosity without editing this
    file, the same operational knob every other NOTEBOOK_API_* limit in
    this project (see GET /api/config, routes/upload.py) is already meant
    to be configurable without.
    """
    return os.getenv("NOTEBOOK_API_DASHBOARD_LOG_LEVEL")


if __name__ == "__main__":
    ssl_keyfile, ssl_certfile = dashboard_ssl_config()

    try:
        validate_dashboard_auth_config(dashboard_host())
        validate_dashboard_exposure(dashboard_host(), dashboard_api_key())
    except ValueError as exc:
        sys.exit(f"error: {exc}")

    from backend.observability.deployment_governance_audit_store import (
        GovernanceAuditStorageError,
        validate_audit_storage_for_startup,
    )

    try:
        _audit_status = validate_audit_storage_for_startup()
    except GovernanceAuditStorageError as exc:
        sys.exit(f"error: {exc}")

    for _issue in _audit_status["issues"]:
        logging.getLogger("uvicorn.error").warning("governance audit storage: %s", _issue)

    uvicorn.run(
        "backend.dashboard:app",
        host=dashboard_host(),
        port=dashboard_port(),
        reload=dashboard_reload(),
        ssl_keyfile=ssl_keyfile,
        ssl_certfile=ssl_certfile,
        log_level=dashboard_log_level(),
    )
