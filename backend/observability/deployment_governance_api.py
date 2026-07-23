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
from .deployment_governance_scheduler_bootstrap import (
    GovernanceSchedulerBootstrapError,
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


def _build_rule_engine():
    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    return runtime.build_governance_rule_engine()


@health_router.get("/rules")
async def get_governance_rules():
    """
    Return every registered governance rule, ordered by priority then
    name.
    """

    rules = _build_rule_engine().list()

    return [rule.to_dict() for rule in rules]


@health_router.post("/rules")
async def post_governance_rule(
    name: str = Query(...),
    operation: str = Query(default="*"),
    priority: int = Query(default=0),
    enabled: bool = Query(default=True),
    conditions: str = Query(default="{}"),
):
    """
    Register a new governance rule that passes when every key/value
    in conditions (a JSON object, as a query string) matches the
    evaluation context. The built-in system-state rules are
    registered with real predicates in code and cannot be recreated
    through this endpoint, only enabled/disabled/removed.
    """

    from .deployment_governance_rules import conditions_match

    parsed_conditions = _parse_json_object(
        conditions, field_name="conditions"
    )

    def _check(context: dict) -> bool:
        return conditions_match(parsed_conditions, context)

    try:
        rule = _build_rule_engine().register(
            name,
            operation=operation,
            priority=priority,
            enabled=enabled,
            check=_check,
        )

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return rule.to_dict()


@health_router.patch("/rules/{name}")
async def patch_governance_rule(
    name: str,
    enabled: bool = Query(...),
):
    """
    Enable or disable a registered governance rule.
    """

    engine = _build_rule_engine()

    try:
        rule = engine.enable(name) if enabled else engine.disable(name)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return rule.to_dict()


@health_router.delete("/rules/{name}")
async def delete_governance_rule(name: str):
    """
    Remove a registered governance rule.
    """

    try:
        _build_rule_engine().remove(name)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {"removed": name}


@health_router.post("/rules/evaluate")
async def post_governance_rule_evaluate(
    name: str = Query(...),
    context: str = Query(default="{}"),
):
    """
    Evaluate a named governance rule directly, returning the
    resulting evaluation. Records the outcome in the governance audit
    trail.
    """

    from .deployment_governance_audit import record_rule_evaluation

    parsed_context = _parse_json_object(context, field_name="context")

    try:
        result = _build_rule_engine().evaluate(name, parsed_context)

    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    record_rule_evaluation(_build_audit_service(), result)

    return result.to_dict()


def _build_recovery_manager():
    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    return runtime.build_governance_recovery_manager()


@health_router.get("/recovery")
async def get_governance_recovery():
    """
    Return every registered governance recovery plan, ordered by
    component name.
    """

    plans = _build_recovery_manager().status()

    return [plan.to_dict() for plan in plans]


@health_router.get("/recovery/history")
async def get_governance_recovery_history(
    component: str | None = Query(default=None),
    limit: int = Query(default=100, gt=0),
):
    """
    Return recorded recovery results, newest first, optionally
    filtered to one component.
    """

    results = _build_recovery_manager().history(component, limit)

    return [result.to_dict() for result in results]


@health_router.post("/recovery/all")
async def post_governance_recovery_all():
    """
    Attempt to recover every component with a registered recovery
    plan, in deterministic order.

    Registered before /recovery/{component}: FastAPI matches path
    routes in registration order, and "all" would otherwise be
    captured as a literal component name by that route first.
    """

    results = _build_recovery_manager().recover_all()

    return [result.to_dict() for result in results]


@health_router.post("/recovery/{component}")
async def post_governance_recovery(component: str):
    """
    Attempt to recover one component according to its registered
    recovery plan.
    """

    try:
        result = _build_recovery_manager().recover(component)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return result.to_dict()


def _build_scheduler():
    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    return runtime.build_governance_scheduler()


@health_router.get("/scheduler")
async def get_governance_scheduler():
    """
    Return the governance scheduler's current running state, active
    job count, and soonest next execution.
    """

    return _build_scheduler().status().to_dict()


@health_router.get("/scheduler/jobs")
async def get_governance_scheduler_jobs():
    """
    Return every job registered with the governance scheduler,
    ordered by next execution time.
    """

    jobs = _build_scheduler().jobs()

    return [job.to_dict() for job in jobs]


@health_router.post("/scheduler/start")
async def post_governance_scheduler_start():
    """
    Start the governance scheduler.
    """

    scheduler = _build_scheduler()
    scheduler.start()

    return scheduler.status().to_dict()


@health_router.post("/scheduler/stop")
async def post_governance_scheduler_stop():
    """
    Stop the governance scheduler.
    """

    scheduler = _build_scheduler()
    scheduler.stop()

    return scheduler.status().to_dict()


def _build_job_registry():
    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    return runtime.build_governance_job_registry()


@health_router.get("/jobs")
async def get_governance_jobs():
    """
    Return every registered governance job, ordered by namespace,
    then name, then job_id.
    """

    jobs = _build_job_registry().list()

    return [job.to_dict() for job in jobs]


@health_router.get("/jobs/namespace/{namespace}")
async def get_governance_jobs_namespace(namespace: str):
    """
    Return every registered governance job in namespace, ordered by
    name then job_id.

    Registered before /jobs/{job_id}: FastAPI matches routes by exact
    path shape, and this route has one more path segment than
    /jobs/{job_id}, so the two never actually collide regardless of
    registration order — registered here anyway for readability
    alongside the job_id routes below.
    """

    jobs = _build_job_registry().list_namespace(namespace)

    return [job.to_dict() for job in jobs]


@health_router.get("/jobs/{job_id}")
async def get_governance_job(job_id: str):
    """
    Return one registered governance job by job_id.
    """

    try:
        job = _build_job_registry().get(job_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return job.to_dict()


@health_router.patch("/jobs/{job_id}/enable")
async def patch_governance_job_enable(job_id: str):
    """
    Mark a registered governance job enabled.
    """

    try:
        job = _build_job_registry().enable(job_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return job.to_dict()


@health_router.patch("/jobs/{job_id}/disable")
async def patch_governance_job_disable(job_id: str):
    """
    Mark a registered governance job disabled.
    """

    try:
        job = _build_job_registry().disable(job_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return job.to_dict()


@health_router.delete("/jobs/{job_id}")
async def delete_governance_job(job_id: str):
    """
    Remove a registered governance job.
    """

    try:
        _build_job_registry().unregister(job_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {"removed": job_id}


def _build_trigger_engine():
    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    return runtime.build_governance_trigger_engine()


@health_router.get("/triggers")
async def get_governance_triggers():
    """
    Return every registered governance trigger, ordered by next_run
    then trigger_id.
    """

    triggers = _build_trigger_engine().list()

    return [trigger.to_dict() for trigger in triggers]


@health_router.get("/triggers/{trigger_id}")
async def get_governance_trigger(trigger_id: str):
    """
    Return one registered governance trigger by trigger_id.
    """

    trigger = next(
        (
            trigger
            for trigger in _build_trigger_engine().list()
            if trigger.trigger_id == trigger_id
        ),
        None,
    )

    if trigger is None:
        raise HTTPException(
            status_code=404,
            detail=f"trigger '{trigger_id}' is not registered",
        )

    return trigger.to_dict()


@health_router.post("/triggers")
async def post_governance_trigger(
    job_id: str = Query(...),
    trigger_type: str = Query(...),
    next_run: datetime | None = Query(default=None),
    enabled: bool = Query(default=True),
):
    """
    Register a new governance trigger for job_id.
    """

    try:
        trigger = _build_trigger_engine().register(
            job_id,
            trigger_type=trigger_type,
            next_run=next_run,
            enabled=enabled,
        )

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return trigger.to_dict()


@health_router.patch("/triggers/{trigger_id}")
async def patch_governance_trigger(
    trigger_id: str,
    next_run: datetime = Query(...),
):
    """
    Reschedule a registered governance trigger's next_run.
    """

    try:
        trigger = _build_trigger_engine().reschedule(trigger_id, next_run)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return trigger.to_dict()


@health_router.delete("/triggers/{trigger_id}")
async def delete_governance_trigger(trigger_id: str):
    """
    Remove a registered governance trigger.
    """

    try:
        _build_trigger_engine().remove(trigger_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {"removed": trigger_id}


def _build_execution_manager():
    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    return runtime.build_governance_execution_manager()


@health_router.get("/executions")
async def get_governance_executions(
    limit: int = Query(default=100, gt=0),
):
    """
    Return recorded governance execution results, newest first.
    """

    results = _build_execution_manager().history(limit=limit)

    return [result.to_dict() for result in results]


@health_router.get("/executions/active")
async def get_governance_executions_active():
    """
    Return every currently active governance execution.
    """

    executions = _build_execution_manager().active()

    return [execution.to_dict() for execution in executions]


@health_router.get("/executions/{execution_id}")
async def get_governance_execution(execution_id: str):
    """
    Return one governance execution (active or completed) by
    execution_id.
    """

    manager = _build_execution_manager()

    active = next(
        (
            execution
            for execution in manager.active()
            if execution.execution_id == execution_id
        ),
        None,
    )

    if active is not None:
        return active.to_dict()

    completed = next(
        (
            result
            for result in manager.history()
            if result.execution_id == execution_id
        ),
        None,
    )

    if completed is None:
        raise HTTPException(
            status_code=404,
            detail=f"execution '{execution_id}' is not registered",
        )

    return completed.to_dict()


@health_router.post("/executions/{job_id}")
async def post_governance_execution(job_id: str):
    """
    Run job_id's execution (a no-op callable, since this API server
    has no live job callable of its own to invoke), recording exactly
    one execution.
    """

    try:
        result = _build_execution_manager().execute(job_id)

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return result.to_dict()


@health_router.delete("/executions/{execution_id}")
async def delete_governance_execution(execution_id: str):
    """
    Cancel a currently active governance execution.
    """

    try:
        result = _build_execution_manager().cancel(execution_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return result.to_dict()


def _build_retry_engine():
    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    return runtime.build_governance_retry_engine()


@health_router.get("/retries")
async def get_governance_retries(
    limit: int = Query(default=100, gt=0),
):
    """
    Return recorded governance retry attempts, newest first.
    """

    attempts = _build_retry_engine().history(limit=limit)

    return [attempt.to_dict() for attempt in attempts]


@health_router.get("/retries/pending")
async def get_governance_retries_pending():
    """
    Return every currently pending governance retry attempt, ordered
    by scheduled_at then execution_id.
    """

    attempts = _build_retry_engine().pending()

    return [attempt.to_dict() for attempt in attempts]


@health_router.post("/retries/{execution_id}")
async def post_governance_retry(execution_id: str):
    """
    Dispatch execution_id's pending retry attempt now, through the
    governance execution manager.
    """

    try:
        result = _build_retry_engine().retry(
            execution_id, _build_execution_manager()
        )

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return result.to_dict()


@health_router.delete("/retries/{execution_id}")
async def delete_governance_retry(execution_id: str):
    """
    Cancel execution_id's entire pending retry chain.
    """

    try:
        _build_retry_engine().cancel_retry(execution_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {"cancelled": execution_id}


def _build_job_persistence():
    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    return runtime.build_governance_job_persistence()


@health_router.get("/persistence")
async def get_governance_persistence():
    """
    Return a summary of the currently stored governance job
    persistence snapshot.
    """

    return _build_job_persistence().snapshot().to_dict()


@health_router.post("/persistence/save")
async def post_governance_persistence_save():
    """
    Save every registered job, trigger, and pending retry into a
    versioned snapshot.
    """

    return _build_job_persistence().save().to_dict()


@health_router.post("/persistence/load")
async def post_governance_persistence_load():
    """
    Restore jobs, triggers, and pending retries from the stored
    governance job persistence snapshot.
    """

    return _build_job_persistence().load().to_dict()


@health_router.delete("/persistence")
async def delete_governance_persistence():
    """
    Delete the stored governance job persistence snapshot.
    """

    _build_job_persistence().clear()

    return {"cleared": True}


def _build_cron_scheduler():
    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    return runtime.build_governance_cron_scheduler()


@health_router.get("/cron")
async def get_governance_cron_triggers():
    """
    Return every registered governance cron trigger, ordered by
    next_run then trigger_id.
    """

    triggers = _build_cron_scheduler().list()

    return [trigger.to_dict() for trigger in triggers]


@health_router.get("/cron/{trigger_id}")
async def get_governance_cron_trigger(trigger_id: str):
    """
    Return one registered governance cron trigger by trigger_id.
    """

    trigger = next(
        (
            trigger
            for trigger in _build_cron_scheduler().list()
            if trigger.trigger_id == trigger_id
        ),
        None,
    )

    if trigger is None:
        raise HTTPException(
            status_code=404,
            detail=f"cron trigger '{trigger_id}' is not registered",
        )

    return trigger.to_dict()


@health_router.post("/cron")
async def post_governance_cron_trigger(
    job_id: str = Query(...),
    expression: str = Query(...),
    timezone: str = Query(default="UTC"),
    enabled: bool = Query(default=True),
):
    """
    Register a new governance cron trigger for job_id.
    """

    try:
        trigger = _build_cron_scheduler().register(
            job_id,
            expression=expression,
            timezone=timezone,
            enabled=enabled,
        )

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return trigger.to_dict()


@health_router.patch("/cron/{trigger_id}")
async def patch_governance_cron_trigger(
    trigger_id: str,
    at: "datetime | None" = Query(default=None),
):
    """
    Recompute and store a registered governance cron trigger's
    next_run, as of at (default now).
    """

    try:
        trigger = _build_cron_scheduler().reschedule(trigger_id, at=at)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return trigger.to_dict()


@health_router.delete("/cron/{trigger_id}")
async def delete_governance_cron_trigger(trigger_id: str):
    """
    Remove a registered governance cron trigger.
    """

    try:
        _build_cron_scheduler().remove(trigger_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {"removed": trigger_id}


@health_router.post("/cron/validate")
async def post_governance_cron_validate(
    expression: str = Query(...),
):
    """
    Validate a cron expression without registering anything.
    """

    valid = _build_cron_scheduler().validate(expression)

    return {"expression": expression, "valid": valid}


def _build_job_dependency_manager():
    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    return runtime.build_governance_job_dependency_manager()


@health_router.get("/job-dependencies")
async def get_governance_job_dependencies():
    """
    Return every registered job dependency definition, ordered by
    job_id.

    A job pulled into the dependency graph purely because something
    else depends on it, with no JobDependency entry of its own, is not
    included here — GET /governance/job-dependencies/{job_id} still
    returns 404 for it, matching dependencies() raising KeyError.
    """

    manager = _build_job_dependency_manager()
    entries = []

    for job_id in manager.validate().startup_order:
        try:
            depends_on = manager.dependencies(job_id)

        except KeyError:
            continue

        entries.append({"job_id": job_id, "depends_on": list(depends_on)})

    return sorted(entries, key=lambda entry: entry["job_id"])


@health_router.get("/job-dependencies/{job_id}")
async def get_governance_job_dependency(job_id: str):
    """
    Return one job's registered dependencies.
    """

    try:
        depends_on = _build_job_dependency_manager().dependencies(job_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {"job_id": job_id, "depends_on": list(depends_on)}


@health_router.post("/job-dependencies")
async def post_governance_job_dependency(
    job_id: str = Query(...),
    depends_on: "list[str]" = Query(default=[]),
):
    """
    Register job_id's prerequisites.
    """

    try:
        dependency = _build_job_dependency_manager().register(
            job_id, depends_on=tuple(depends_on),
        )

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return dependency.to_dict()


@health_router.delete("/job-dependencies/{job_id}")
async def delete_governance_job_dependency(job_id: str):
    """
    Remove job_id's registered dependencies.
    """

    try:
        _build_job_dependency_manager().remove(job_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {"removed": job_id}


@health_router.post("/job-dependencies/validate")
async def post_governance_job_dependencies_validate():
    """
    Validate the currently registered job dependency graph: missing
    references, circular dependencies, and the resulting deterministic
    topological order if valid.
    """

    return _build_job_dependency_manager().validate().to_dict()


def _build_scheduler_lock_manager():
    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    return runtime.build_governance_scheduler_lock_manager()


@health_router.get("/locks")
async def get_governance_locks():
    """
    Return every currently stored governance scheduler lock (expired
    or not), ordered by job_id.
    """

    locks = _build_scheduler_lock_manager().list()

    return [lock.to_dict() for lock in locks]


@health_router.get("/locks/{job_id}")
async def get_governance_lock(job_id: str):
    """
    Return job_id's currently stored governance scheduler lock.
    """

    lock = next(
        (
            lock
            for lock in _build_scheduler_lock_manager().list()
            if lock.job_id == job_id
        ),
        None,
    )

    if lock is None:
        raise HTTPException(
            status_code=404,
            detail=f"no lock stored for job '{job_id}'",
        )

    return lock.to_dict()


@health_router.post("/locks/{job_id}/acquire")
async def post_governance_lock_acquire(
    job_id: str,
    owner_id: str = Query(...),
    lease_seconds: "int | None" = Query(default=None),
):
    """
    Attempt to acquire job_id's governance scheduler lock for
    owner_id.
    """

    result = _build_scheduler_lock_manager().acquire(
        job_id, owner_id, lease_seconds=lease_seconds,
    )

    return result.to_dict()


@health_router.post("/locks/{job_id}/release")
async def post_governance_lock_release(
    job_id: str,
    owner_id: str = Query(...),
):
    """
    Release job_id's governance scheduler lock, if owner_id currently
    holds it. Idempotent: a no-op if it does not.
    """

    released = _build_scheduler_lock_manager().release(job_id, owner_id)

    return {"released": released}


@health_router.post("/locks/cleanup")
async def post_governance_locks_cleanup():
    """
    Remove every expired governance scheduler lock, returning how many
    were removed.
    """

    removed = _build_scheduler_lock_manager().cleanup()

    return {"removed": removed}


def _build_scheduler_metrics():
    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    return runtime.build_governance_scheduler_metrics()


@health_router.get("/scheduler/metrics")
async def get_governance_scheduler_metrics_snapshot():
    """
    Return the scheduler pipeline's current counters and gauges.
    """

    return _build_scheduler_metrics().snapshot().to_dict()


@health_router.get("/scheduler/metrics/summary")
async def get_governance_scheduler_metrics_summary():
    """
    Return the scheduler pipeline's derived performance indicators:
    rolling averages and ratios.
    """

    return _build_scheduler_metrics().summary().to_dict()


@health_router.post("/scheduler/metrics/reset")
async def post_governance_scheduler_metrics_reset():
    """
    Reset every governance scheduler metric counter, gauge, and
    rolling-average timer.
    """

    _build_scheduler_metrics().reset()

    return {"reset": True}


def _build_scheduler_policy_engine():
    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    return runtime.build_governance_scheduler_policy_engine()


@health_router.get("/scheduler/policies")
async def get_governance_scheduler_policies():
    """
    Return every registered governance scheduler policy, ordered by
    priority then name.
    """

    policies = _build_scheduler_policy_engine().list()

    return [policy.to_dict() for policy in policies]


@health_router.post("/scheduler/policies")
async def post_governance_scheduler_policy(
    name: str = Query(...),
    priority: int = Query(default=0),
    enabled: bool = Query(default=True),
    conditions: str = Query(default="{}"),
    policy_type: str | None = Query(default=None),
):
    """
    Register a new governance scheduler policy. conditions is a JSON
    object (as a query string). If policy_type names one of the
    built-in scheduling checks, it is used instead of plain
    conditions-matching.
    """

    parsed_conditions = _parse_json_object(
        conditions, field_name="conditions"
    )

    try:
        policy = _build_scheduler_policy_engine().register(
            name,
            priority=priority,
            enabled=enabled,
            conditions=parsed_conditions,
            policy_type=policy_type,
        )

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return policy.to_dict()


@health_router.patch("/scheduler/policies/{name}")
async def patch_governance_scheduler_policy(
    name: str,
    enabled: bool = Query(...),
):
    """
    Enable or disable a registered governance scheduler policy.
    """

    engine = _build_scheduler_policy_engine()

    try:
        policy = engine.enable(name) if enabled else engine.disable(name)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return policy.to_dict()


@health_router.delete("/scheduler/policies/{name}")
async def delete_governance_scheduler_policy(name: str):
    """
    Remove a registered governance scheduler policy.
    """

    try:
        _build_scheduler_policy_engine().remove(name)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {"removed": name}


@health_router.post("/scheduler/policies/evaluate")
async def post_governance_scheduler_policy_evaluate(
    job_id: str = Query(...),
    context: str = Query(default="{}"),
):
    """
    Evaluate job_id against every registered governance scheduler
    policy. context is a JSON object (as a query string).
    """

    parsed_context = _parse_json_object(context, field_name="context")

    decision = _build_scheduler_policy_engine().evaluate(
        job_id, parsed_context
    )

    return decision.to_dict()


def _build_scheduler_dashboard():
    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    return runtime.build_governance_scheduler_dashboard()


@health_router.get("/scheduler/dashboard")
async def get_governance_scheduler_dashboard():
    """
    Return the aggregated governance scheduler dashboard.
    """

    return _build_scheduler_dashboard().dashboard().to_dict()


@health_router.get("/scheduler/dashboard/summary")
async def get_governance_scheduler_dashboard_summary():
    """
    Return the compact governance scheduler dashboard summary.
    """

    return _build_scheduler_dashboard().summary().to_dict()


@health_router.get("/scheduler/dashboard/jobs")
async def get_governance_scheduler_dashboard_jobs():
    """
    Return every registered job, as seen by the governance scheduler
    dashboard.
    """

    jobs = _build_scheduler_dashboard().jobs()

    return [job.to_dict() for job in jobs]


@health_router.get("/scheduler/dashboard/executions")
async def get_governance_scheduler_dashboard_executions():
    """
    Return recorded execution history, as seen by the governance
    scheduler dashboard.
    """

    executions = _build_scheduler_dashboard().executions()

    return [execution.to_dict() for execution in executions]


@health_router.get("/scheduler/dashboard/retries")
async def get_governance_scheduler_dashboard_retries():
    """
    Return every currently pending retry attempt, as seen by the
    governance scheduler dashboard.
    """

    retries = _build_scheduler_dashboard().retries()

    return [retry.to_dict() for retry in retries]


@health_router.get("/scheduler/dashboard/locks")
async def get_governance_scheduler_dashboard_locks():
    """
    Return every currently stored lock, as seen by the governance
    scheduler dashboard.
    """

    locks = _build_scheduler_dashboard().locks()

    return [lock.to_dict() for lock in locks]


def _build_scheduler_bootstrap():
    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    return runtime.build_governance_scheduler_bootstrap()


@health_router.get("/scheduler/bootstrap")
async def get_governance_scheduler_bootstrap():
    """
    Return the governance scheduler bootstrap's current lifecycle
    state.
    """

    return _build_scheduler_bootstrap().status().to_dict()


@health_router.post("/scheduler/bootstrap")
async def post_governance_scheduler_bootstrap():
    """
    Run the governance scheduler bootstrap's initialization pipeline:
    validate the scheduling component dependency graph, restore
    persisted scheduler state, and start the scheduler. Idempotent —
    a no-op returning the original report if already initialized.
    """

    try:
        report = _build_scheduler_bootstrap().initialize()

    except GovernanceSchedulerBootstrapError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return report.to_dict()


@health_router.post("/scheduler/restart")
async def post_governance_scheduler_restart():
    """
    Shut down (if currently initialized) and re-run the governance
    scheduler bootstrap's initialization pipeline.
    """

    try:
        report = _build_scheduler_bootstrap().restart()

    except GovernanceSchedulerBootstrapError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return report.to_dict()


def _build_rollout_manager():
    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    return runtime.build_governance_rollout_manager()


@health_router.get("/rollouts")
async def get_governance_rollouts():
    """
    Return every registered rollout, ordered by created_at then
    rollout_id.
    """

    rollouts = _build_rollout_manager().list()

    return [rollout.to_dict() for rollout in rollouts]


@health_router.get("/rollouts/{rollout_id}")
async def get_governance_rollout(rollout_id: str):
    """
    Return one rollout's current status snapshot by rollout_id.
    """

    try:
        status = _build_rollout_manager().status(rollout_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return status.to_dict()


@health_router.post("/rollouts")
async def post_governance_rollout(
    deployment_id: str = Query(...),
    strategy: str = Query(...),
):
    """
    Create a new PENDING rollout for deployment_id.
    """

    try:
        rollout = _build_rollout_manager().create(
            deployment_id, strategy
        )

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return rollout.to_dict()


@health_router.post("/rollouts/{rollout_id}/start")
async def post_governance_rollout_start(rollout_id: str):
    """
    Start a registered rollout, transitioning it to RUNNING.
    """

    manager = _build_rollout_manager()

    try:
        rollout = manager.start(rollout_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return rollout.to_dict()


@health_router.post("/rollouts/{rollout_id}/pause")
async def post_governance_rollout_pause(rollout_id: str):
    """
    Pause a running rollout.
    """

    manager = _build_rollout_manager()

    try:
        rollout = manager.pause(rollout_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return rollout.to_dict()


@health_router.post("/rollouts/{rollout_id}/resume")
async def post_governance_rollout_resume(rollout_id: str):
    """
    Resume a paused rollout, transitioning it back to RUNNING.
    """

    manager = _build_rollout_manager()

    try:
        rollout = manager.resume(rollout_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return rollout.to_dict()


@health_router.delete("/rollouts/{rollout_id}")
async def delete_governance_rollout(rollout_id: str):
    """
    Cancel a registered rollout.
    """

    manager = _build_rollout_manager()

    try:
        rollout = manager.cancel(rollout_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return rollout.to_dict()


def _build_version_registry():
    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    return runtime.build_governance_deployment_registry()


@health_router.get("/deployments")
async def get_governance_deployments():
    """
    Return every currently registered deployment version, ordered by
    deployment_id.
    """

    versions = _build_version_registry().list()

    return [version.to_dict() for version in versions]


@health_router.get("/deployments/{deployment_id}")
async def get_governance_deployment(deployment_id: str):
    """
    Return one deployment's currently registered version.
    """

    try:
        version = _build_version_registry().get(deployment_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return version.to_dict()


@health_router.get("/deployments/{deployment_id}/history")
async def get_governance_deployment_history(deployment_id: str):
    """
    Return every revision ever recorded for deployment_id, oldest
    first, including entries recorded before a later removal.
    """

    revisions = _build_version_registry().history(deployment_id)

    return [revision.to_dict() for revision in revisions]


@health_router.post("/deployments")
async def post_governance_deployment(
    deployment_id: str = Query(...),
    version: str = Query(...),
    artifact: str = Query(...),
    checksum: str = Query(...),
    metadata: str = Query(default="{}"),
):
    """
    Register a new deployment's first version.
    """

    parsed_metadata = _parse_json_object(
        metadata, field_name="metadata"
    )

    try:
        record = _build_version_registry().register(
            deployment_id,
            version,
            artifact,
            checksum,
            metadata=parsed_metadata,
        )

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return record.to_dict()


@health_router.patch("/deployments/{deployment_id}")
async def patch_governance_deployment(
    deployment_id: str,
    version: str = Query(...),
    artifact: str = Query(...),
    checksum: str = Query(...),
    metadata: str = Query(default="{}"),
):
    """
    Replace deployment_id's currently registered version, appending a
    new revision to its history.
    """

    parsed_metadata = _parse_json_object(
        metadata, field_name="metadata"
    )

    registry = _build_version_registry()

    try:
        record = registry.update(
            deployment_id,
            version,
            artifact,
            checksum,
            metadata=parsed_metadata,
        )

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return record.to_dict()


@health_router.delete("/deployments/{deployment_id}")
async def delete_governance_deployment(deployment_id: str):
    """
    Remove deployment_id's active registration. Its revision history
    remains available through GET .../history.
    """

    try:
        _build_version_registry().remove(deployment_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {"removed": deployment_id}


def _build_blue_green_engine():
    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    return runtime.build_governance_blue_green_engine()


@health_router.get("/blue-green")
async def get_governance_blue_green_deployments():
    """
    Return every currently tracked Blue/Green deployment, ordered by
    deployment_id.
    """

    deployments = _build_blue_green_engine().list()

    return [deployment.to_dict() for deployment in deployments]


@health_router.get("/blue-green/{deployment_id}")
async def get_governance_blue_green_deployment(deployment_id: str):
    """
    Return one deployment's current Blue/Green state.
    """

    try:
        status = _build_blue_green_engine().status(deployment_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return status.to_dict()


@health_router.post("/blue-green/{deployment_id}/deploy")
async def post_governance_blue_green_deploy(
    deployment_id: str,
    green_version: str = Query(...),
    blue_version: "str | None" = Query(default=None),
):
    """
    Deploy green_version into deployment_id's idle environment. If
    blue_version is omitted, it is resolved from the Version Registry.
    """

    engine = _build_blue_green_engine()

    try:
        deployment = engine.deploy(
            deployment_id, green_version, blue_version=blue_version
        )

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return deployment.to_dict()


@health_router.post("/blue-green/{deployment_id}/validate")
async def post_governance_blue_green_validate(deployment_id: str):
    """
    Validate deployment_id's currently idle environment, gating
    POST .../switch. Not part of the originally specified endpoint
    set, but added alongside it: switch() unconditionally requires a
    passing validate() first, so without this there would be no way
    to ever successfully call POST .../switch over the API.
    """

    engine = _build_blue_green_engine()

    try:
        passed = engine.validate(deployment_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {"deployment_id": deployment_id, "validated": passed}


@health_router.post("/blue-green/{deployment_id}/switch")
async def post_governance_blue_green_switch(deployment_id: str):
    """
    Atomically switch deployment_id's live traffic to its currently
    idle environment. Requires a prior, passing
    POST .../validate call for the currently deployed idle
    environment.
    """

    engine = _build_blue_green_engine()

    try:
        result = engine.switch(deployment_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return result.to_dict()


@health_router.post("/blue-green/{deployment_id}/rollback")
async def post_governance_blue_green_rollback(deployment_id: str):
    """
    Restore deployment_id's previously active environment, reverting
    its most recent switch.
    """

    engine = _build_blue_green_engine()

    try:
        result = engine.rollback(deployment_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return result.to_dict()


def _build_canary_engine():
    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    return runtime.build_governance_canary_engine()


@health_router.get("/canary")
async def get_governance_canary_deployments():
    """
    Return every currently tracked canary deployment, ordered by
    deployment_id.
    """

    deployments = _build_canary_engine().list()

    return [deployment.to_dict() for deployment in deployments]


@health_router.get("/canary/{deployment_id}")
async def get_governance_canary_deployment(deployment_id: str):
    """
    Return one deployment's current canary state.
    """

    try:
        status = _build_canary_engine().status(deployment_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return status.to_dict()


@health_router.post("/canary/{deployment_id}/deploy")
async def post_governance_canary_deploy(
    deployment_id: str,
    canary_version: str = Query(...),
    stable_version: "str | None" = Query(default=None),
    stages: "list[int] | None" = Query(default=None),
):
    """
    Start a new canary rollout for deployment_id at its first
    configured stage. If stable_version is omitted, it is resolved
    from the Version Registry.
    """

    engine = _build_canary_engine()

    try:
        deployment = engine.deploy(
            deployment_id,
            canary_version,
            stable_version=stable_version,
            stages=tuple(stages) if stages is not None else None,
        )

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return deployment.to_dict()


@health_router.post("/canary/{deployment_id}/evaluate")
async def post_governance_canary_evaluate(deployment_id: str):
    """
    Run one health evaluation for deployment_id's canary. Not part of
    the originally specified endpoint set, but added alongside it:
    promote() unconditionally requires a passing evaluation since the
    last promotion, so without this there would be no way to ever
    successfully call POST .../promote over the API. A failing
    evaluation automatically rolls the canary back.
    """

    engine = _build_canary_engine()

    try:
        evaluation = engine.evaluate(deployment_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return evaluation.to_dict()


@health_router.post("/canary/{deployment_id}/promote")
async def post_governance_canary_promote(deployment_id: str):
    """
    Advance deployment_id's canary to the next configured stage.
    Requires a prior, passing POST .../evaluate call since the last
    promotion.
    """

    engine = _build_canary_engine()

    try:
        deployment = engine.promote(deployment_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return deployment.to_dict()


@health_router.post("/canary/{deployment_id}/pause")
async def post_governance_canary_pause(deployment_id: str):
    """
    Pause deployment_id's canary, blocking further promotion until
    resumed.
    """

    engine = _build_canary_engine()

    try:
        deployment = engine.pause(deployment_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return deployment.to_dict()


@health_router.post("/canary/{deployment_id}/resume")
async def post_governance_canary_resume(deployment_id: str):
    """
    Resume deployment_id's paused canary.
    """

    engine = _build_canary_engine()

    try:
        deployment = engine.resume(deployment_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return deployment.to_dict()


@health_router.post("/canary/{deployment_id}/rollback")
async def post_governance_canary_rollback(deployment_id: str):
    """
    Roll deployment_id's canary back to 0% traffic and mark it
    terminal.
    """

    engine = _build_canary_engine()

    try:
        deployment = engine.rollback(deployment_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return deployment.to_dict()


def _build_rolling_engine():
    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    return runtime.build_governance_rolling_engine()


@health_router.get("/rolling")
async def get_governance_rolling_deployments():
    """
    Return every currently tracked rolling deployment, ordered by
    deployment_id.
    """

    deployments = _build_rolling_engine().list()

    return [deployment.to_dict() for deployment in deployments]


@health_router.get("/rolling/{deployment_id}")
async def get_governance_rolling_deployment(deployment_id: str):
    """
    Return one deployment's current rolling deployment state.
    """

    try:
        status = _build_rolling_engine().status(deployment_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return status.to_dict()


@health_router.post("/rolling/{deployment_id}/deploy")
async def post_governance_rolling_deploy(
    deployment_id: str,
    target_version: str = Query(...),
    total_instances: int = Query(..., gt=0),
    batch_size: "int | None" = Query(default=None),
    batch_percentage: "int | None" = Query(default=None),
):
    """
    Start a new rolling update for deployment_id.
    """

    engine = _build_rolling_engine()

    try:
        deployment = engine.deploy(
            deployment_id,
            target_version,
            total_instances,
            batch_size=batch_size,
            batch_percentage=batch_percentage,
        )

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return deployment.to_dict()


@health_router.post("/rolling/{deployment_id}/next-batch")
async def post_governance_rolling_next_batch(deployment_id: str):
    """
    Apply the next batch of deployment_id's rolling update.
    """

    engine = _build_rolling_engine()

    try:
        deployment = engine.next_batch(deployment_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return deployment.to_dict()


@health_router.post("/rolling/{deployment_id}/validate-batch")
async def post_governance_rolling_validate_batch(deployment_id: str):
    """
    Validate deployment_id's most recently applied batch. Not part of
    the originally specified endpoint set, but added alongside it:
    every next-batch call after the first unconditionally requires a
    passing validation of the previous batch, so without this there
    would be no way to progress a rolling update past its first batch
    over the API. A failing validation pauses the rollout.
    """

    engine = _build_rolling_engine()

    try:
        result = engine.validate_batch(deployment_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return result.to_dict()


@health_router.post("/rolling/{deployment_id}/pause")
async def post_governance_rolling_pause(deployment_id: str):
    """
    Pause deployment_id's rolling update, blocking further batches
    until resumed.
    """

    engine = _build_rolling_engine()

    try:
        deployment = engine.pause(deployment_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return deployment.to_dict()


@health_router.post("/rolling/{deployment_id}/resume")
async def post_governance_rolling_resume(deployment_id: str):
    """
    Resume deployment_id's paused rolling update.
    """

    engine = _build_rolling_engine()

    try:
        deployment = engine.resume(deployment_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return deployment.to_dict()


@health_router.post("/rolling/{deployment_id}/rollback")
async def post_governance_rolling_rollback(deployment_id: str):
    """
    Restore deployment_id's already-updated instances to their
    previous version and mark the rollout terminal.
    """

    engine = _build_rolling_engine()

    try:
        deployment = engine.rollback(deployment_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return deployment.to_dict()


def _build_progressive_delivery_engine():
    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    return runtime.build_governance_progressive_delivery_engine()


@health_router.get("/progressive")
async def get_governance_progressive_deployments():
    """
    Return every currently tracked progressive delivery deployment,
    ordered by deployment_id.
    """

    deployments = _build_progressive_delivery_engine().list()

    return [deployment.to_dict() for deployment in deployments]


@health_router.get("/progressive/{deployment_id}")
async def get_governance_progressive_deployment(deployment_id: str):
    """
    Return one deployment's current progressive delivery state.
    """

    try:
        status = _build_progressive_delivery_engine().status(
            deployment_id
        )

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return status.to_dict()


@health_router.post("/progressive/{deployment_id}/deploy")
async def post_governance_progressive_deploy(
    deployment_id: str,
    stage_names: "list[str]" = Query(...),
    stage_strategies: "list[str]" = Query(...),
    stage_approval_required: "list[bool]" = Query(...),
):
    """
    Start a new progressive delivery pipeline for deployment_id.
    stage_names, stage_strategies, and stage_approval_required are
    parallel lists, one entry per pipeline stage, in order.
    """

    if not (
        len(stage_names)
        == len(stage_strategies)
        == len(stage_approval_required)
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "stage_names, stage_strategies, and "
                "stage_approval_required must be the same length"
            ),
        )

    stages = list(
        zip(stage_names, stage_strategies, stage_approval_required)
    )

    try:
        deployment = _build_progressive_delivery_engine().deploy(
            deployment_id, stages
        )

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return deployment.to_dict()


@health_router.post("/progressive/{deployment_id}/advance")
async def post_governance_progressive_advance(deployment_id: str):
    """
    Attempt to complete deployment_id's current stage and move on to
    the next.
    """

    engine = _build_progressive_delivery_engine()

    try:
        deployment = engine.advance(deployment_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return deployment.to_dict()


@health_router.post("/progressive/{deployment_id}/approve")
async def post_governance_progressive_approve(deployment_id: str):
    """
    Grant deployment_id's pending approval, unblocking the next
    advance() call.
    """

    engine = _build_progressive_delivery_engine()

    try:
        deployment = engine.approve(deployment_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return deployment.to_dict()


@health_router.post("/progressive/{deployment_id}/reject")
async def post_governance_progressive_reject(deployment_id: str):
    """
    Reject deployment_id's pending approval, automatically rolling the
    whole deployment back.
    """

    engine = _build_progressive_delivery_engine()

    try:
        deployment = engine.reject(deployment_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return deployment.to_dict()


@health_router.post("/progressive/{deployment_id}/rollback")
async def post_governance_progressive_rollback(deployment_id: str):
    """
    Mark deployment_id's progressive deployment rolled back at
    whatever stage it is currently on.
    """

    engine = _build_progressive_delivery_engine()

    try:
        deployment = engine.rollback(deployment_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return deployment.to_dict()


def _build_traffic_router():
    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    return runtime.build_governance_traffic_router()


def _zipped_allocations(
    versions: "list[str]", percentages: "list[float]"
) -> "list[tuple[str, float]]":
    if len(versions) != len(percentages):
        raise HTTPException(
            status_code=422,
            detail="versions and percentages must be the same length",
        )

    return list(zip(versions, percentages))


@health_router.get("/routing")
async def get_governance_routing_snapshots():
    """
    Return every currently tracked deployment's routing snapshot,
    ordered by deployment_id.
    """

    snapshots = _build_traffic_router().list()

    return [snapshot.to_dict() for snapshot in snapshots]


@health_router.get("/routing/{deployment_id}")
async def get_governance_routing_snapshot(deployment_id: str):
    """
    Return one deployment's current routing snapshot.
    """

    try:
        snapshot = _build_traffic_router().snapshot(deployment_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return snapshot.to_dict()


@health_router.post("/routing/{deployment_id}")
async def post_governance_routing_configure(
    deployment_id: str,
    versions: "list[str]" = Query(...),
    percentages: "list[float]" = Query(...),
    strategy: str = Query(default="STATIC"),
):
    """
    Replace deployment_id's entire routing table. versions and
    percentages are parallel lists, one entry per allocation.
    """

    allocations = _zipped_allocations(versions, percentages)

    try:
        snapshot = _build_traffic_router().configure(
            deployment_id, allocations, strategy=strategy
        )

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return snapshot.to_dict()


@health_router.patch("/routing/{deployment_id}")
async def patch_governance_routing_update(
    deployment_id: str,
    versions: "list[str]" = Query(...),
    percentages: "list[float]" = Query(...),
):
    """
    Replace deployment_id's entire routing table, keeping its
    currently configured strategy.
    """

    allocations = _zipped_allocations(versions, percentages)

    try:
        snapshot = _build_traffic_router().update(
            deployment_id, allocations
        )

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return snapshot.to_dict()


@health_router.post("/routing/{deployment_id}/rebalance")
async def post_governance_routing_rebalance(deployment_id: str):
    """
    Reapply deployment_id's configured strategy's rebalance rule to
    its current allocation set.
    """

    try:
        snapshot = _build_traffic_router().rebalance(deployment_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return snapshot.to_dict()


@health_router.post("/routing/{deployment_id}/reset")
async def post_governance_routing_reset(deployment_id: str):
    """
    Clear deployment_id's routing table to an empty allocation set.
    """

    snapshot = _build_traffic_router().reset(deployment_id)

    return snapshot.to_dict()


def _build_rollback_engine():
    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    return runtime.build_governance_rollback_engine()


@health_router.get("/rollbacks")
async def get_governance_rollbacks():
    """
    Return every currently tracked deployment's rollback plan, ordered
    by deployment_id.
    """

    plans = _build_rollback_engine().list()

    return [plan.to_dict() for plan in plans]


@health_router.get("/rollbacks/{deployment_id}")
async def get_governance_rollback(deployment_id: str):
    """
    Return one deployment's current (or most recent) rollback plan.
    """

    try:
        plan = _build_rollback_engine().status(deployment_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return plan.to_dict()


@health_router.post("/rollbacks/{deployment_id}")
async def post_governance_rollback(
    deployment_id: str,
    target_version: "str | None" = Query(default=None),
    trigger: str = Query(default="MANUAL_ROLLBACK_REQUEST"),
):
    """
    Plan and immediately execute a rollback for deployment_id. If
    target_version is omitted, it is resolved as the version
    registered immediately before deployment_id's current one.
    """

    engine = _build_rollback_engine()

    try:
        engine.create_plan(
            deployment_id, target_version=target_version,
            trigger=trigger,
        )

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    result = engine.execute(deployment_id)

    return result.to_dict()


@health_router.delete("/rollbacks/{deployment_id}")
async def delete_governance_rollback(deployment_id: str):
    """
    Cancel deployment_id's active rollback plan without executing it.
    """

    try:
        plan = _build_rollback_engine().cancel(deployment_id)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return plan.to_dict()
