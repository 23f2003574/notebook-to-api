from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query

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


@health_router.get("/diagnostics")
async def get_governance_diagnostics():
    """
    Return a read-only governance runtime diagnostics snapshot for
    debugging.
    """

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    return (
        runtime.build_integrity_diagnostics_service()
        .snapshot()
        .to_dict()
    )


@health_router.get("/dependencies")
async def get_governance_dependencies():
    """
    Return the governance runtime's component dependency graph:
    every registered component, its startup order, its dependency
    map, and whether the graph currently validates.
    """

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    graph = runtime.build_integrity_dependency_graph()
    result = graph.validate()
    components = graph.components()

    return {
        "components": [
            component.to_dict() for component in components
        ],
        "startup_order": list(result.startup_order),
        "dependency_map": {
            component.name: list(component.dependencies)
            for component in components
        },
        "valid": result.valid,
        "cycles": [list(cycle) for cycle in result.cycles],
        "missing": list(result.missing),
    }


def _build_lifecycle_manager():
    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    return runtime.build_integrity_lifecycle_manager()


@health_router.post("/lifecycle/start")
async def post_governance_lifecycle_start():
    """
    Start every registered governance component that is not already
    started, in validated dependency order.
    """

    return _build_lifecycle_manager().startup().to_dict()


@health_router.post("/lifecycle/stop")
async def post_governance_lifecycle_stop():
    """
    Stop every currently started governance component, in reverse
    startup order.
    """

    return _build_lifecycle_manager().shutdown().to_dict()


@health_router.post("/lifecycle/restart")
async def post_governance_lifecycle_restart():
    """
    Stop every currently started governance component, then start
    every registered component back up.
    """

    return _build_lifecycle_manager().restart().to_dict()


@health_router.get("/lifecycle/status")
async def get_governance_lifecycle_status():
    """
    Return every registered governance component's current lifecycle
    status.
    """

    components = _build_lifecycle_manager().status()

    return [component.to_dict() for component in components]


@health_router.get("/events/types")
async def get_governance_event_types():
    """
    Return the well-known governance event types published by the
    lifecycle manager, health service, and metrics bootstrap.
    """

    from .deployment_governance_event_bus import GOVERNANCE_EVENT_TYPES

    return list(GOVERNANCE_EVENT_TYPES)


@health_router.get("/events/subscribers")
async def get_governance_event_subscribers():
    """
    Return every subscription currently registered on the governance
    event bus.
    """

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    subscriptions = runtime.build_integrity_event_bus().subscribers()

    return [subscription.to_dict() for subscription in subscriptions]


def _build_event_query(
    *,
    event_type: "str | None",
    source: "str | None",
    start_time: "datetime | None",
    end_time: "datetime | None",
    limit: int,
):
    from .deployment_governance_event_history import EventQuery

    return EventQuery(
        event_type=event_type,
        source=source,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
    )


@health_router.get("/events")
async def get_governance_events(
    event_type: str | None = Query(default=None),
    source: str | None = Query(default=None),
    start_time: datetime | None = Query(default=None),
    end_time: datetime | None = Query(default=None),
    limit: int = Query(default=100, gt=0),
):
    """
    Return stored governance events, newest first, filtered by event
    type, source, and/or time range.
    """

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    query = _build_event_query(
        event_type=event_type,
        source=source,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
    )

    stored = runtime.build_integrity_event_history().query(query)

    return [entry.to_dict() for entry in stored]


@health_router.get("/events/latest")
async def get_governance_events_latest(
    limit: int = Query(default=10, gt=0),
):
    """
    Return the most recently stored governance events, newest first.
    """

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    stored = runtime.build_integrity_event_history().latest(limit)

    return [entry.to_dict() for entry in stored]


@health_router.post("/events/replay")
async def post_governance_events_replay(
    event_type: str | None = Query(default=None),
    source: str | None = Query(default=None),
    start_time: datetime | None = Query(default=None),
    end_time: datetime | None = Query(default=None),
    limit: int = Query(default=100, gt=0),
):
    """
    Replay stored governance events matching the given filters back
    onto the event bus's current subscribers, in the order they
    originally occurred, without persisting them again.
    """

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    query = _build_event_query(
        event_type=event_type,
        source=source,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
    )

    history = runtime.build_integrity_event_history()
    bus = runtime.build_integrity_event_bus()

    replayed = history.replay(query, bus)

    return [event.to_dict() for event in replayed]


@health_router.delete("/events")
async def delete_governance_events():
    """
    Purge every stored governance event, returning how many were
    removed.
    """

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    purged = runtime.build_integrity_event_history().purge()

    return {"purged": purged}


def _build_event_router():
    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    return runtime.build_integrity_event_router()


@health_router.get("/routes")
async def get_governance_routes():
    """
    Return every registered governance event route, ordered by
    priority then name.
    """

    routes = _build_event_router().routes()

    return [route.to_dict() for route in routes]


@health_router.post("/routes")
async def post_governance_route(
    name: str = Query(...),
    event_types: list[str] = Query(default=["*"]),
    sources: list[str] = Query(default=["*"]),
    priority: int = Query(default=0),
    enabled: bool = Query(default=True),
):
    """
    Register a new governance event route.
    """

    try:
        route = _build_event_router().register_route(
            name,
            event_types=tuple(event_types),
            sources=tuple(sources),
            priority=priority,
            enabled=enabled,
        )

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return route.to_dict()


@health_router.patch("/routes/{name}")
async def patch_governance_route(
    name: str,
    enabled: bool = Query(...),
):
    """
    Enable or disable a registered governance event route.
    """

    router = _build_event_router()

    try:
        route = (
            router.enable_route(name)
            if enabled
            else router.disable_route(name)
        )

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return route.to_dict()


@health_router.delete("/routes/{name}")
async def delete_governance_route(name: str):
    """
    Remove a registered governance event route.
    """

    try:
        _build_event_router().remove_route(name)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {"removed": name}


def _build_audit_service():
    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    return runtime.build_governance_audit_trail_service()


@health_router.get("/audit")
async def get_governance_audit(
    action: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    resource: str | None = Query(default=None),
    limit: int = Query(default=100, gt=0),
):
    """
    Return governance audit trail records, newest first, filtered by
    action, actor, and/or resource.
    """

    from .deployment_governance_audit import AuditQuery

    query = AuditQuery(
        action=action, actor=actor, resource=resource, limit=limit
    )

    records = _build_audit_service().query(query)

    return [record.to_dict() for record in records]


@health_router.get("/audit/latest")
async def get_governance_audit_latest(
    limit: int = Query(default=10, gt=0),
):
    """
    Return the most recent governance audit trail records, newest
    first.
    """

    records = _build_audit_service().latest(limit)

    return [record.to_dict() for record in records]


@health_router.get("/audit/verify")
async def get_governance_audit_verify():
    """
    Verify the governance audit trail's hash chain, returning whether
    it is intact and, if not, the first broken record.
    """

    result = _build_audit_service().verify_chain()

    return result.to_dict()


def _build_policy_engine():
    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    return runtime.build_governance_policy_engine()


def _parse_json_object(value: str, *, field_name: str) -> dict:
    try:
        parsed = json.loads(value)

    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} must be valid JSON: {exc}",
        ) from exc

    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} must be a JSON object",
        )

    return parsed


@health_router.get("/policies")
async def get_governance_policies():
    """
    Return every registered governance policy, ordered by priority
    then name.
    """

    policies = _build_policy_engine().list()

    return [policy.to_dict() for policy in policies]


@health_router.post("/policies")
async def post_governance_policy(
    name: str = Query(...),
    operation: str = Query(...),
    priority: int = Query(default=0),
    enabled: bool = Query(default=True),
    conditions: str = Query(default="{}"),
):
    """
    Register a new governance policy. conditions is a JSON object
    (as a query string) of context keys that must all match for the
    policy to deny an operation.
    """

    parsed_conditions = _parse_json_object(
        conditions, field_name="conditions"
    )

    try:
        policy = _build_policy_engine().register(
            name,
            operation=operation,
            priority=priority,
            enabled=enabled,
            conditions=parsed_conditions,
        )

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return policy.to_dict()


@health_router.patch("/policies/{name}")
async def patch_governance_policy(
    name: str,
    enabled: bool = Query(...),
):
    """
    Enable or disable a registered governance policy.
    """

    engine = _build_policy_engine()

    try:
        policy = engine.enable(name) if enabled else engine.disable(name)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return policy.to_dict()


@health_router.delete("/policies/{name}")
async def delete_governance_policy(name: str):
    """
    Remove a registered governance policy.
    """

    try:
        _build_policy_engine().remove(name)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {"removed": name}


@health_router.post("/policies/evaluate")
async def post_governance_policy_evaluate(
    operation: str = Query(...),
    context: str = Query(default="{}"),
):
    """
    Evaluate operation against every registered policy, returning the
    resulting decision. context is a JSON object (as a query string).
    """

    parsed_context = _parse_json_object(context, field_name="context")

    decision = _build_policy_engine().evaluate(operation, parsed_context)

    return decision.to_dict()
