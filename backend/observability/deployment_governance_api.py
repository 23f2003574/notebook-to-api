from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Query

from .deployment_governance_metrics_api import (
    GovernanceIntegrityMetricsApi,
)
from .deployment_governance_metrics_middleware import (
    GovernanceIntegrityMetricsMiddleware,
    GovernanceIntegrityRequestMetricsCollector,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

router = APIRouter(
    prefix="/governance/metrics",
    tags=["governance-metrics"],
)

health_router = APIRouter(
    prefix="/governance",
    tags=["governance-health"],
)

# Shared for the lifetime of the process: the middleware records into
# this collector on every request, and get_request_metrics_collector()
# below reads from the same instance. A separate process (e.g. a CLI
# invocation) has no access to a running server's in-memory state and
# will only ever see a fresh, empty collector of its own.
_request_metrics_collector = GovernanceIntegrityRequestMetricsCollector()


def get_request_metrics_collector() -> (
    GovernanceIntegrityRequestMetricsCollector
):
    """
    Return the process-wide governance API request metrics
    collector.
    """

    return _request_metrics_collector


def register_governance_metrics_middleware(app: "FastAPI") -> None:
    """
    Register the governance API request metrics middleware on app,
    bound to the process-wide collector.

    Intended to be called once during API initialization.
    """

    app.add_middleware(
        GovernanceIntegrityMetricsMiddleware,
        collector=_request_metrics_collector,
    )


def _build_metrics_api() -> GovernanceIntegrityMetricsApi:
    """
    Bootstrap persistence for this request and resync the metrics
    service from durable storage before building the API facade.

    Each request builds a fresh persistence runtime with fresh,
    empty in-memory metrics state, so the metrics service must load
    from its repository before anything is read; the underlying
    repository itself is what is actually durable across requests.
    """

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    metrics_service = runtime.build_integrity_metrics_service()

    metrics_service.load()

    alert_service = runtime.build_integrity_metrics_alert_service()

    alert_service.evaluate(metrics_service.snapshot())

    return GovernanceIntegrityMetricsApi(
        metrics_service, alert_service=alert_service
    )


@router.get("")
async def get_governance_metrics():
    """
    Return the current governance audit notification delivery
    metrics counters.
    """

    return _build_metrics_api().summary().to_dict()


@router.get("/dashboard")
async def get_governance_metrics_dashboard():
    """
    Return a compact governance audit notification delivery metrics
    dashboard: current counters, derived percentages, and active
    alert count.
    """

    return _build_metrics_api().dashboard().to_dict()


@router.get("/history")
async def get_governance_metrics_history(
    limit: int | None = Query(default=None, ge=0),
    offset: int = Query(default=0, ge=0),
):
    """
    Return captured governance audit notification delivery metrics
    snapshots, newest first, paginated by limit/offset.
    """

    snapshots = _build_metrics_api().history(
        limit=limit, offset=offset
    )

    return [snapshot.to_dict() for snapshot in snapshots]


@router.get("/alerts")
async def get_governance_metrics_alerts():
    """
    Return every currently active (triggered) governance audit
    notification delivery metric alert.
    """

    alerts = _build_metrics_api().alerts()

    return [alert.to_dict() for alert in alerts]


@health_router.get("/health")
async def get_governance_health():
    """
    Return the overall governance health status plus the health of
    each individually checked component.
    """

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    return runtime.build_integrity_health_service().summary().to_dict()


@health_router.get("/ready")
async def get_governance_readiness():
    """
    Return the overall governance readiness status plus the
    readiness of each individually checked component.
    """

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    return runtime.build_integrity_readiness_service().summary().to_dict()


@health_router.get("/live")
async def get_governance_liveness():
    """
    Return whether the governance runtime process is alive, plus its
    current uptime.
    """

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    return runtime.build_integrity_liveness_service().check().to_dict()
