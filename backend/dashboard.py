"""
Dashboard API Server
Serves the React dashboard frontend and provides API endpoints for compilation
"""

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

app = FastAPI(
    title="notebook-to-api Dashboard",
    description="Transform Jupyter notebooks into production APIs",
    version="0.1.0"
)

# Enable CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://localhost:5174", "*"],
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


@app.get("/")
async def root():
    return {
        "status": "running",
        "service": "notebook-to-api Dashboard API",
        "version": "0.1.0",
        "docs": "/docs"
    }


if __name__ == "__main__":
    uvicorn.run(
        "backend.dashboard:app",
        host="0.0.0.0",
        port=8001,
        reload=True
    )
