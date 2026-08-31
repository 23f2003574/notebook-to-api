"""
Dashboard API Server
Serves the React dashboard frontend and provides API endpoints for compilation
"""

import os
import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn

from backend.routes.upload import router as upload_router
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

app = FastAPI(
    title="notebook-to-api Dashboard",
    description="Transform Jupyter notebooks into production APIs",
    version="0.1.0"
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


# Enable CORS for the frontend, credentialed requests restricted to a
# known allowlist -- see allowed_origins() docstring.
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Collect request metrics for the governance API endpoints
register_governance_metrics_middleware(app)

# Include API routes
app.include_router(upload_router)
app.include_router(governance_metrics_router)
app.include_router(governance_health_router)

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


@app.get("/")
async def root():
    return {
        "status": "running",
        "service": "notebook-to-api Dashboard API",
        "version": "0.1.0",
        "docs": "/docs"
    }


# Build output directory for the bundled React frontend (see
# frontend/package.json's own "build" script, `vite build`) -- not
# checked into this repo, only ever produced by actually running that
# build.
FRONTEND_DIST_DIR = PROJECT_ROOT / "frontend" / "dist"


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
    this catch-all mount is ever consulted -- confirmed live: GET / still
    reaches root()'s own JSON response, not a file from `dist_dir`, even
    with a frontend mounted.
    """
    if not dist_dir.is_dir():
        return

    app.mount("/", StaticFiles(directory=str(dist_dir), html=True), name="frontend")


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
    return os.getenv("NOTEBOOK_API_DASHBOARD_HOST", "0.0.0.0")


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

    uvicorn.run(
        "backend.dashboard:app",
        host=dashboard_host(),
        port=dashboard_port(),
        reload=dashboard_reload(),
        ssl_keyfile=ssl_keyfile,
        ssl_certfile=ssl_certfile,
        log_level=dashboard_log_level(),
    )
