from __future__ import annotations

from typing import Optional

from .api_gateway import APIGateway
from .api_versioning import APIVersionManager
from .gateway_analytics import GatewayAnalytics
from .gateway_dashboard_api import GatewayDashboardAPI, GatewayHealth
from .gateway_export import GatewayExportService
from .load_balancer import LoadBalancer
from .middleware import CORSMiddleware, LoggingMiddleware, MiddlewarePipeline
from .rate_limiter import RateLimiter
from .request_router import RequestRouter
from .request_validation import RequestValidationEngine
from .route_registry import RouteRegistry
from .traffic_policy import TrafficPolicyEngine


class GatewayAlreadyInitializedError(RuntimeError):
    pass


class GatewayNotInitializedError(RuntimeError):
    pass


class GatewayBootstrap:
    """Wires every gateway component into a single running subsystem."""

    def __init__(self) -> None:
        self._initialized = False
        self.gateway: Optional[APIGateway] = None
        self.route_registry: Optional[RouteRegistry] = None
        self.analytics: Optional[GatewayAnalytics] = None
        self.middleware_pipeline: Optional[MiddlewarePipeline] = None
        self.rate_limiter: Optional[RateLimiter] = None
        self.validation_engine: Optional[RequestValidationEngine] = None
        self.load_balancer: Optional[LoadBalancer] = None
        self.policy_engine: Optional[TrafficPolicyEngine] = None
        self.version_manager: Optional[APIVersionManager] = None
        self.request_router: Optional[RequestRouter] = None
        self.dashboard: Optional[GatewayDashboardAPI] = None
        self.export_service: Optional[GatewayExportService] = None

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    def initialize(self) -> "GatewayBootstrap":
        if self._initialized:
            raise GatewayAlreadyInitializedError("gateway bootstrap is already initialized")

        self.gateway = APIGateway()
        self.route_registry = self.gateway.route_registry
        self.analytics = self.gateway.analytics
        self.middleware_pipeline = MiddlewarePipeline()
        self.rate_limiter = RateLimiter()
        self.validation_engine = RequestValidationEngine()
        self.load_balancer = LoadBalancer()
        self.policy_engine = TrafficPolicyEngine()
        self.version_manager = APIVersionManager()

        self.register_components()
        self.wire_pipeline()

        self.gateway.start()
        self._initialized = True
        return self

    def register_components(self) -> None:
        registered = {middleware.name for middleware in self.middleware_pipeline.list_middleware()}
        if "logging" not in registered:
            logger = LoggingMiddleware()
            self.middleware_pipeline.register("logging", before=logger.before, after=logger.after, priority=0)
        if "cors" not in registered:
            cors = CORSMiddleware()
            self.middleware_pipeline.register("cors", after=cors.after, priority=10)

    def wire_pipeline(self) -> None:
        self.request_router = RequestRouter(
            self.route_registry,
            middleware_pipeline=self.middleware_pipeline,
            validation_engine=self.validation_engine,
            load_balancer=self.load_balancer,
        )
        self.dashboard = GatewayDashboardAPI(
            self.gateway,
            self.analytics,
            self.route_registry,
            load_balancer=self.load_balancer,
            middleware_pipeline=self.middleware_pipeline,
            policy_engine=self.policy_engine,
            version_manager=self.version_manager,
        )
        self.export_service = GatewayExportService(self.dashboard)

    def health_check(self) -> GatewayHealth:
        if not self._initialized:
            raise GatewayNotInitializedError("gateway bootstrap is not initialized")
        return self.dashboard.health()

    def shutdown(self) -> None:
        if not self._initialized:
            raise GatewayNotInitializedError("gateway bootstrap is not initialized")
        self.gateway.reset()
        self._initialized = False


_bootstrap = GatewayBootstrap()


def get_bootstrap() -> GatewayBootstrap:
    return _bootstrap
