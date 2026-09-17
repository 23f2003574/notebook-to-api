from .api_gateway import (
    APIGateway,
    GatewayAlreadyRunningError,
    GatewayNotRunningError,
    GatewayRequest,
    GatewayResponse,
    GatewayStatus,
    UnknownRouteError,
    get_api_gateway,
    router as api_gateway_router,
)
from .api_versioning import router as api_versioning_router
from .bootstrap import (
    GatewayAlreadyInitializedError,
    GatewayBootstrap,
    GatewayNotInitializedError,
    get_bootstrap,
)
from .gateway_analytics import router as gateway_analytics_router
from .gateway_dashboard_api import router as gateway_dashboard_router
from .gateway_export import router as gateway_export_router
from .load_balancer import router as load_balancer_router
from .middleware import router as middleware_router, compression_router
from .rate_limiter import router as rate_limiter_router
from .request_router import router as request_router_router
from .request_validation import router as request_validation_router
from .route_registry import router as route_registry_router
from .traffic_policy import router as traffic_policy_router

# request_router_router must precede route_registry_router: its
# "/gateway/routes/match" and "/gateway/routes/resolve" paths would
# otherwise be swallowed by route_registry_router's "/gateway/routes/{route:path}"
# catch-all if that router were registered first.
all_routers = (
    api_gateway_router,
    request_router_router,
    route_registry_router,
    middleware_router,
    rate_limiter_router,
    request_validation_router,
    load_balancer_router,
    traffic_policy_router,
    api_versioning_router,
    gateway_analytics_router,
    gateway_dashboard_router,
    gateway_export_router,
    compression_router,
)

__all__ = [
    "APIGateway",
    "GatewayAlreadyRunningError",
    "GatewayNotRunningError",
    "GatewayRequest",
    "GatewayResponse",
    "GatewayStatus",
    "UnknownRouteError",
    "get_api_gateway",
    "api_gateway_router",
    "GatewayAlreadyInitializedError",
    "GatewayBootstrap",
    "GatewayNotInitializedError",
    "get_bootstrap",
    "all_routers",
    "compression_router",
]
