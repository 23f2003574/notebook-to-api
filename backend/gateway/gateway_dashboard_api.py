from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from fastapi import APIRouter, Depends

from .api_gateway import APIGateway, get_api_gateway
from .api_versioning import APIVersionManager, get_version_manager
from .gateway_analytics import GatewayAnalytics
from .load_balancer import LoadBalancer, get_load_balancer
from .middleware import MiddlewarePipeline, get_middleware_pipeline
from .route_registry import RouteRegistry, get_route_registry
from .traffic_policy import TrafficPolicyEngine, get_policy_engine

ERROR_RATE_THRESHOLD = 0.5


@dataclass(frozen=True)
class DashboardOverview:
    """A single-glance summary of the gateway's current state."""

    gateway_status: str
    dispatch_count: int
    total_routes: int
    total_backends: int
    healthy_backends: int
    total_middleware: int
    total_policies: int
    total_versions: int
    analytics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "gateway_status": self.gateway_status,
            "dispatch_count": self.dispatch_count,
            "total_routes": self.total_routes,
            "total_backends": self.total_backends,
            "healthy_backends": self.healthy_backends,
            "total_middleware": self.total_middleware,
            "total_policies": self.total_policies,
            "total_versions": self.total_versions,
            "analytics": self.analytics,
        }


@dataclass(frozen=True)
class GatewayHealth:
    """An overall health verdict for the gateway and its backends."""

    status: str
    gateway_running: bool
    healthy_backends: int
    total_backends: int
    issues: tuple = ()

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "gateway_running": self.gateway_running,
            "healthy_backends": self.healthy_backends,
            "total_backends": self.total_backends,
            "issues": list(self.issues),
        }


class GatewayDashboardAPI:
    """Aggregates gateway state into read/control endpoints for a dashboard."""

    def __init__(
        self,
        gateway: APIGateway,
        analytics: GatewayAnalytics,
        route_registry: RouteRegistry,
        *,
        load_balancer: Optional[LoadBalancer] = None,
        middleware_pipeline: Optional[MiddlewarePipeline] = None,
        policy_engine: Optional[TrafficPolicyEngine] = None,
        version_manager: Optional[APIVersionManager] = None,
    ) -> None:
        self._gateway = gateway
        self._analytics = analytics
        self._route_registry = route_registry
        self._load_balancer = load_balancer
        self._middleware_pipeline = middleware_pipeline
        self._policy_engine = policy_engine
        self._version_manager = version_manager

    def overview(self) -> DashboardOverview:
        status = self._gateway.status()
        backend_state = self._load_balancer.rebalance() if self._load_balancer is not None else None
        return DashboardOverview(
            gateway_status=status["status"],
            dispatch_count=status["dispatch_count"],
            total_routes=len(self._route_registry.list_routes()),
            total_backends=backend_state.total_backends if backend_state else 0,
            healthy_backends=backend_state.healthy_backends if backend_state else 0,
            total_middleware=len(self._middleware_pipeline.list_middleware()) if self._middleware_pipeline else 0,
            total_policies=len(self._policy_engine.list_policies()) if self._policy_engine else 0,
            total_versions=len(self._version_manager.supported_versions()) if self._version_manager else 0,
            analytics=self._analytics.compute_statistics().to_dict(),
        )

    def metrics(self) -> dict:
        return self._analytics.compute_statistics().to_dict()

    def health(self) -> GatewayHealth:
        status = self._gateway.status()
        gateway_running = status["status"] == "running"
        healthy_backends = 0
        total_backends = 0
        issues: list = []

        if not gateway_running:
            issues.append("gateway is not running")

        if self._load_balancer is not None:
            backend_state = self._load_balancer.rebalance()
            healthy_backends = backend_state.healthy_backends
            total_backends = backend_state.total_backends
            if total_backends > 0 and healthy_backends == 0:
                issues.append("no healthy backends available")

        if self._analytics.compute_statistics().error_rate > ERROR_RATE_THRESHOLD:
            issues.append("elevated error rate")

        if not gateway_running or (total_backends > 0 and healthy_backends == 0):
            overall = "unhealthy"
        elif issues:
            overall = "degraded"
        else:
            overall = "healthy"

        return GatewayHealth(
            status=overall,
            gateway_running=gateway_running,
            healthy_backends=healthy_backends,
            total_backends=total_backends,
            issues=tuple(issues),
        )

    def configuration(self) -> dict:
        return {
            "middleware": (
                [middleware.to_dict() for middleware in self._middleware_pipeline.list_middleware()]
                if self._middleware_pipeline
                else []
            ),
            "policies": (
                [policy.to_dict() for policy in self._policy_engine.list_policies()]
                if self._policy_engine
                else []
            ),
            "versions": (
                [version.to_dict() for version in self._version_manager.supported_versions()]
                if self._version_manager
                else []
            ),
            "load_balancer_strategy": self._load_balancer.rebalance().strategy if self._load_balancer else None,
        }

    def reset_metrics(self) -> None:
        self._analytics.reset()


_dashboard_api = GatewayDashboardAPI(
    get_api_gateway(),
    get_api_gateway().analytics,
    get_route_registry(),
    load_balancer=get_load_balancer(),
    middleware_pipeline=get_middleware_pipeline(),
    policy_engine=get_policy_engine(),
    version_manager=get_version_manager(),
)


def get_dashboard_api() -> GatewayDashboardAPI:
    return _dashboard_api


router = APIRouter(prefix="/gateway/dashboard", tags=["gateway-dashboard"])


@router.get("/overview")
def dashboard_overview_endpoint(dashboard: GatewayDashboardAPI = Depends(get_dashboard_api)) -> dict:
    return dashboard.overview().to_dict()


@router.get("/metrics")
def dashboard_metrics_endpoint(dashboard: GatewayDashboardAPI = Depends(get_dashboard_api)) -> dict:
    return dashboard.metrics()


@router.get("/health")
def dashboard_health_endpoint(dashboard: GatewayDashboardAPI = Depends(get_dashboard_api)) -> dict:
    return dashboard.health().to_dict()


@router.get("/configuration")
def dashboard_configuration_endpoint(dashboard: GatewayDashboardAPI = Depends(get_dashboard_api)) -> dict:
    return dashboard.configuration()


@router.post("/reset-metrics")
def dashboard_reset_metrics_endpoint(dashboard: GatewayDashboardAPI = Depends(get_dashboard_api)) -> dict:
    dashboard.reset_metrics()
    return {"status": "reset"}
