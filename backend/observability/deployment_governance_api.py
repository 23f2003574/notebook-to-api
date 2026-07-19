from __future__ import annotations

from fastapi import APIRouter, Query

from .deployment_governance_metrics_api import (
    GovernanceIntegrityMetricsApi,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)

router = APIRouter(
    prefix="/governance/metrics",
    tags=["governance-metrics"],
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
