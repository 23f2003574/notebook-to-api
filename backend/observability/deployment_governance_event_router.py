from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_event_bus import GovernanceEvent
    from .deployment_governance_audit import GovernanceAuditService

WILDCARD = "*"


@dataclass(frozen=True)
class EventRoute:
    """
    A named routing rule: which events (by event_type and source) it
    is interested in, how it ranks against other routes, and whether
    it is currently active.
    """

    name: str

    event_types: "tuple[str, ...]"

    sources: "tuple[str, ...]"

    priority: int

    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name must not be empty")

        if not self.event_types:
            raise ValueError("event_types must not be empty")

        if not self.sources:
            raise ValueError("sources must not be empty")

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "event_types": list(self.event_types),
            "sources": list(self.sources),
            "priority": self.priority,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class RouteMatch:
    """
    Whether one route matched one event, and why not if it did not.
    """

    route: str

    matched: bool

    reason: "str | None"

    def __post_init__(self) -> None:
        if self.matched and self.reason is not None:
            raise ValueError(
                "reason must not be set when matched is True"
            )

        if not self.matched and self.reason is None:
            raise ValueError(
                "reason must be set when matched is False"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "route": self.route,
            "matched": self.matched,
            "reason": self.reason,
        }


def route_matches(
    route: EventRoute, *, event_type: str, source: str
) -> bool:
    """
    Return whether route's event_types/sources criteria (honoring the
    "*" wildcard) match event_type and source.

    Ignores route.enabled: this is the pure matching predicate, used
    both by GovernanceEventRouter.route() (which layers the enabled
    check on top, so it can report "disabled" as a distinct reason)
    and by GovernanceEventHistory.matching() (which has no concept of
    enabled at all — it is asking what a route's criteria would have
    selected historically).
    """

    type_ok = WILDCARD in route.event_types or event_type in route.event_types

    source_ok = WILDCARD in route.sources or source in route.sources

    return type_ok and source_ok


class GovernanceEventRouter:
    """
    Rule-based routing, filtering, and fan-out for governance events:
    decides which named routes an event matches, without itself being
    another event bus or event store (those are
    GovernanceEventBus/GovernanceEventHistory; this builds on both
    without replacing either).

    Routing is a pure decision: route() never mutates the event it is
    given, and computing matches has no side effects. Actual fan-out
    (notifying whatever a route conceptually feeds into) happens
    through the optional on_match callback, invoked once per matched,
    enabled route when this router is fed events via handle_event()
    (its event bus subscriber entrypoint) — isolated the same way a
    bus handler is: a raising callback never stops evaluation of the
    remaining routes.
    """

    def __init__(
        self,
        *,
        on_match: "Callable[[str, GovernanceEvent], None] | None" = None,
        audit_service: "GovernanceAuditService | None" = None,
    ) -> None:
        self._routes: "dict[str, EventRoute]" = {}

        self._on_match = on_match or (lambda name, event: None)

        self._audit_service = audit_service

    def register_route(
        self,
        name: str,
        *,
        event_types: "tuple[str, ...]" = (WILDCARD,),
        sources: "tuple[str, ...]" = (WILDCARD,),
        priority: int = 0,
        enabled: bool = True,
    ) -> EventRoute:
        """
        Register a new named route.

        Raises ValueError if name is already registered.
        """

        if name in self._routes:
            raise ValueError(f"route '{name}' is already registered")

        route = EventRoute(
            name=name,
            event_types=tuple(event_types),
            sources=tuple(sources),
            priority=priority,
            enabled=enabled,
        )

        self._routes[name] = route

        self._record_audit("route_create", route)

        return route

    def remove_route(self, name: str) -> None:
        """
        Remove a registered route.

        Raises KeyError if name is not registered.
        """

        if name not in self._routes:
            raise KeyError(f"route '{name}' is not registered")

        route = self._routes[name]

        del self._routes[name]

        self._record_audit("route_delete", route)

    def enable_route(self, name: str) -> EventRoute:
        """
        Enable a registered route, returning its updated state.

        Raises KeyError if name is not registered. Idempotent: an
        already-enabled route stays enabled.
        """

        return self._set_enabled(name, True)

    def disable_route(self, name: str) -> EventRoute:
        """
        Disable a registered route, returning its updated state.

        Raises KeyError if name is not registered. Idempotent: an
        already-disabled route stays disabled.
        """

        return self._set_enabled(name, False)

    def route(self, event: "GovernanceEvent") -> "tuple[RouteMatch, ...]":
        """
        Evaluate every registered route against event (without
        mutating it in any way), ordered deterministically by
        priority then route name.

        Every route is represented in the result, including disabled
        ones and ones whose criteria did not match: this is a full
        diagnostic view of the routing decision, not just the fan-out
        list. Callers that want only the routes an event actually
        reaches should filter for matched=True themselves.
        """

        return tuple(self._evaluate(route, event) for route in self.routes())

    def routes(self) -> "tuple[EventRoute, ...]":
        """
        Return every registered route, ordered deterministically by
        priority then route name.
        """

        return tuple(
            sorted(
                self._routes.values(),
                key=lambda route: (route.priority, route.name),
            )
        )

    def clear(self) -> None:
        """
        Remove every registered route.
        """

        self._routes.clear()

    def handle_event(self, event: "GovernanceEvent") -> None:
        """
        This router's event bus subscriber entrypoint: for every
        route event matches (and that is enabled), call on_match with
        (route_name, event).

        Each route's callback is isolated: one raising callback does
        not stop the remaining matched routes from being notified.
        """

        for match in self.route(event):
            if not match.matched:
                continue

            try:
                self._on_match(match.route, event)

            except Exception:
                pass

    def _evaluate(
        self, route: EventRoute, event: "GovernanceEvent"
    ) -> RouteMatch:
        if not route.enabled:
            return RouteMatch(
                route=route.name,
                matched=False,
                reason="route is disabled",
            )

        if not route_matches(
            route, event_type=event.event_type, source=event.source
        ):
            return RouteMatch(
                route=route.name,
                matched=False,
                reason=(
                    f"event_type '{event.event_type}' from source "
                    f"'{event.source}' does not match route criteria"
                ),
            )

        return RouteMatch(route=route.name, matched=True, reason=None)

    def _set_enabled(self, name: str, enabled: bool) -> EventRoute:
        route = self._routes.get(name)

        if route is None:
            raise KeyError(f"route '{name}' is not registered")

        updated = dataclasses.replace(route, enabled=enabled)

        self._routes[name] = updated

        self._record_audit("route_update", updated)

        return updated

    def _record_audit(self, action: str, route: EventRoute) -> None:
        if self._audit_service is None:
            return

        self._audit_service.record(
            action=action,
            actor="system",
            resource=f"route:{route.name}",
            outcome="success",
            metadata=route.to_dict(),
        )


def build_default_governance_event_router(
    *,
    on_match: "Callable[[str, GovernanceEvent], None] | None" = None,
    audit_service: "GovernanceAuditService | None" = None,
) -> GovernanceEventRouter:
    """
    Build the governance event router's default route set: lifecycle
    events routed to logging and to metrics, health/readiness check
    completions routed to diagnostics, provider-registry-sourced
    events routed to metrics, and component failures routed to an
    alert pipeline placeholder — no real alerting subsystem exists
    yet, so this route currently has nothing wired to on_match for
    it, but registering it now means adding real alerting later does
    not require touching routing configuration again.

    Actual delivery for "lifecycle -> logging" and the others already
    happens today through the direct event bus subscriptions
    introduced in earlier commits (e.g.
    deployment_governance_logging.event_bus_log_handler); these
    routes make that same intent introspectable and manageable
    through the GET/POST/PATCH/DELETE /governance/routes API, without
    replacing those direct subscriptions.
    """

    router = GovernanceEventRouter(
        on_match=on_match, audit_service=audit_service
    )

    router.register_route(
        "failure_events_to_alert_pipeline",
        event_types=("component_failed",),
        sources=(WILDCARD,),
        priority=5,
    )

    router.register_route(
        "lifecycle_to_logging",
        event_types=(
            "lifecycle_completed",
            "component_started",
            "component_stopped",
            "component_failed",
        ),
        sources=(WILDCARD,),
        priority=10,
    )

    router.register_route(
        "lifecycle_to_metrics",
        event_types=("lifecycle_completed",),
        sources=(WILDCARD,),
        priority=20,
    )

    router.register_route(
        "health_to_diagnostics",
        event_types=("health_check_completed",),
        sources=(WILDCARD,),
        priority=30,
    )

    router.register_route(
        "readiness_to_diagnostics",
        event_types=("readiness_check_completed",),
        sources=(WILDCARD,),
        priority=40,
    )

    router.register_route(
        "provider_events_to_metrics",
        event_types=(WILDCARD,),
        sources=("provider_registry",),
        priority=50,
    )

    return router


# Shared for the lifetime of the process: routing rules registered
# through the API need to be visible to whatever is consuming events
# off the process-wide event bus, and vice versa. Wired to the
# process-wide audit service (a plain top-level import, not deferred:
# deployment_governance_audit has no dependency on this module, so
# there is no circular import to avoid) so every route
# create/update/delete is recorded in the tamper-evident audit trail.
from .deployment_governance_audit import get_audit_service  # noqa: E402

_event_router = build_default_governance_event_router(
    audit_service=get_audit_service()
)


def get_event_router() -> GovernanceEventRouter:
    """
    Return the process-wide governance event router, (re-)wiring it
    to the process-wide event bus on every access if its subscription
    is not currently present.

    Checked on every call rather than assumed permanent after a first
    wiring, for the same reason as
    deployment_governance_event_history.get_event_history():
    GovernanceEventBus.clear() (e.g. in test teardown) removes every
    subscription, including this one.
    """

    from .deployment_governance_event_bus import get_event_bus

    bus = get_event_bus()

    already_wired = any(
        subscription.handler == _event_router.handle_event
        for subscription in bus.subscribers(bus.WILDCARD_EVENT_TYPE)
    )

    if not already_wired:
        bus.route_through(_event_router)

    return _event_router
