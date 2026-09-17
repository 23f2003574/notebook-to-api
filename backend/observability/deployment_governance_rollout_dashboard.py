from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_event_bus import GovernanceEventBus
    from .deployment_governance_rbac import DeploymentRBACEngine
    from .deployment_governance_rollback import (
        DeploymentRollbackEngine,
        RollbackPlan,
    )
    from .deployment_governance_rollout_analytics import (
        DeploymentRolloutAnalytics,
        RolloutAnalyticsSnapshot,
    )
    from .deployment_governance_rollout_health import (
        DeploymentRolloutHealthEngine,
        RolloutHealthSnapshot,
    )
    from .deployment_governance_rollout_manager import (
        DeploymentRolloutManager,
    )
    from .deployment_governance_rollout_policy import (
        DeploymentRolloutPolicyEngine,
        RolloutPolicy,
    )
    from .deployment_governance_traffic_router import (
        DeploymentTrafficRouter,
        RoutingSnapshot,
    )
    from .deployment_governance_version_registry import (
        DeploymentVersionRegistry,
    )

_ROLLOUT_ACTIVE_STATES: "frozenset[str]" = frozenset(
    {"PENDING", "RUNNING", "PAUSED"}
)


@dataclass(frozen=True)
class DeploymentDashboardEntry:
    """
    One deployment's current picture, assembled from whichever wired
    subsystems have something to say about it. A field with no
    available data reads as an explicit, honest placeholder ("" for
    version/strategy/state, "UNKNOWN" for health, 0.0 for
    traffic_percentage) rather than being omitted — DeploymentDashboardEntry
    has no optional fields, so "unavailable" has to be representable
    within them.
    """

    deployment_id: str

    version: str

    strategy: str

    health: str

    traffic_percentage: float

    state: str

    updated_at: datetime

    def __post_init__(self) -> None:
        if not self.deployment_id:
            raise ValueError("deployment_id must not be empty")

        if self.traffic_percentage < 0:
            raise ValueError("traffic_percentage must not be negative")

        if self.updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        return {
            "deployment_id": self.deployment_id,
            "version": self.version,
            "strategy": self.strategy,
            "health": self.health,
            "traffic_percentage": self.traffic_percentage,
            "state": self.state,
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass(frozen=True)
class RolloutDashboard:
    """
    One complete, point-in-time operational view across every
    deployment this dashboard could find data for.
    """

    generated_at: datetime

    active_rollouts: int

    completed_rollouts: int

    failed_rollouts: int

    deployments: "tuple[DeploymentDashboardEntry, ...]"

    def __post_init__(self) -> None:
        if self.generated_at.tzinfo is None:
            raise ValueError("generated_at must be timezone-aware")

        if self.active_rollouts < 0:
            raise ValueError("active_rollouts must be >= 0")

        if self.completed_rollouts < 0:
            raise ValueError("completed_rollouts must be >= 0")

        if self.failed_rollouts < 0:
            raise ValueError("failed_rollouts must be >= 0")

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "active_rollouts": self.active_rollouts,
            "completed_rollouts": self.completed_rollouts,
            "failed_rollouts": self.failed_rollouts,
            "deployments": [
                entry.to_dict() for entry in self.deployments
            ],
        }


class DeploymentRolloutDashboard:
    """
    A read-only aggregation service sitting above the Rollout Manager,
    Version Registry, Traffic Router, Health Engine, Rollback Engine,
    Analytics Engine, and Rollout Policy Engine — the same shape as
    GovernanceSchedulerDashboard: every method here only ever calls an
    already-public, already-read-only accessor on one of those
    (list()/status()/snapshot()/latest()/summary()) and combines the
    results. It never registers, starts, allocates, evaluates, or
    otherwise mutates anything, and every constructor dependency is
    optional — a component that was not wired simply contributes its
    "nothing here yet" default instead of raising, the same graceful-
    degradation contract GovernanceSchedulerDashboard's own docstring
    describes. Nothing in this codebase depends on *this* dashboard in
    turn, so — unlike the last several engines — there is no circular-
    singleton concern here: every dependency below is wired directly
    by build_default_governance_rollout_dashboard().

    Unlike GovernanceSchedulerDashboard (which caches nothing),
    overview() caches its result for cache_ttl_seconds (0, the
    default, disables caching — every call rebuilds). refresh()
    always rebuilds and re-caches, regardless of cache_ttl_seconds or
    how recently overview() last ran; the six section accessors
    (deployments/health/analytics/traffic/rollbacks/policies) are
    always fresh, uncached reads of their one underlying subsystem —
    only the full cross-subsystem overview() aggregation is ever
    cached.

    Thread-safe: the cached snapshot is guarded by an internal lock;
    nothing else here holds mutable state of its own beyond that.
    """

    def __init__(
        self,
        *,
        clock: "Callable[[], datetime] | None" = None,
        event_bus: "GovernanceEventBus | None" = None,
        rollout_manager: "DeploymentRolloutManager | None" = None,
        version_registry: "DeploymentVersionRegistry | None" = None,
        traffic_router: "DeploymentTrafficRouter | None" = None,
        health_engine: "DeploymentRolloutHealthEngine | None" = None,
        rollback_engine: "DeploymentRollbackEngine | None" = None,
        analytics: "DeploymentRolloutAnalytics | None" = None,
        policy_engine: "DeploymentRolloutPolicyEngine | None" = None,
        rbac_engine: "DeploymentRBACEngine | None" = None,
        cache_ttl_seconds: float = 0.0,
    ) -> None:
        if cache_ttl_seconds < 0:
            raise ValueError("cache_ttl_seconds must not be negative")

        self._lock = threading.Lock()

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

        self._rollout_manager = rollout_manager

        self._version_registry = version_registry

        self._traffic_router = traffic_router

        self._health_engine = health_engine

        self._rollback_engine = rollback_engine

        self._analytics = analytics

        self._policy_engine = policy_engine

        self._rbac_engine = rbac_engine

        self._cache_ttl_seconds = cache_ttl_seconds

        self._cached: "RolloutDashboard | None" = None

        self._cached_at: "datetime | None" = None

    def overview(self) -> RolloutDashboard:
        """
        Return the full cross-subsystem dashboard, serving a cached
        copy if one was built within cache_ttl_seconds — a cache hit
        publishes nothing (it is not a new "generation").
        """

        with self._lock:
            cached = self._cached
            cached_at = self._cached_at

        if (
            cached is not None
            and cached_at is not None
            and self._cache_ttl_seconds > 0
            and (self._clock() - cached_at).total_seconds()
            < self._cache_ttl_seconds
        ):
            return cached

        return self._build(event_type="rollout_dashboard_generated")

    def refresh(
        self, *, principal_id: "str | None" = None
    ) -> RolloutDashboard:
        """
        Rebuild the dashboard unconditionally, bypassing (and
        replacing) any cached copy, publishing "rollout_dashboard_
        refreshed" instead of "rollout_dashboard_generated" — for a
        caller that means "I explicitly asked for the latest view"
        rather than an incidental read. With principal_id given and an
        rbac_engine wired in, also raises PermissionError if
        principal_id is not authorized for "deployment.manage".
        """

        self._check_authorization(principal_id, "deployment.manage")

        return self._build(event_type="rollout_dashboard_refreshed")

    def deployments(self) -> "tuple[DeploymentDashboardEntry, ...]":
        """
        Return one entry per deployment_id this dashboard could find
        data for (the union of every wired subsystem's known
        deployment_ids), ordered deterministically by deployment_id.
        Always a fresh read, independent of overview()'s cache.
        """

        return self._build_deployment_entries()

    def health(self) -> "tuple[RolloutHealthSnapshot, ...]":
        """
        Return the wired health engine's most recent snapshot per
        deployment, ordered by deployment_id. Empty if no
        health_engine is wired.
        """

        if self._health_engine is None:
            return ()

        return self._health_engine.list()

    def analytics(self) -> "RolloutAnalyticsSnapshot | None":
        """
        Return the wired analytics engine's current global snapshot,
        or None if no analytics engine is wired.
        """

        if self._analytics is None:
            return None

        return self._analytics.snapshot()

    def traffic(self) -> "tuple[RoutingSnapshot, ...]":
        """
        Return the wired traffic router's current routing snapshot per
        deployment, ordered by deployment_id. Empty if no
        traffic_router is wired.
        """

        if self._traffic_router is None:
            return ()

        return self._traffic_router.list()

    def rollbacks(self) -> "tuple[RollbackPlan, ...]":
        """
        Return the wired rollback engine's current plan per
        deployment, ordered by deployment_id. Empty if no
        rollback_engine is wired.
        """

        if self._rollback_engine is None:
            return ()

        return self._rollback_engine.list()

    def policies(self) -> "tuple[RolloutPolicy, ...]":
        """
        Return the wired policy engine's registered policies, ordered
        by priority then name. Empty if no policy_engine is wired.
        """

        if self._policy_engine is None:
            return ()

        return self._policy_engine.list()

    def set_rbac_engine(
        self, rbac_engine: "DeploymentRBACEngine"
    ) -> None:
        """
        Wire rbac_engine in after construction, matching how
        build_default_governance_rbac_engine wires the process-wide
        RBAC engine into this dashboard's own singleton.
        """

        self._rbac_engine = rbac_engine

    def _check_authorization(
        self, principal_id: "str | None", permission: str
    ) -> None:
        """
        Raise PermissionError if principal_id is given, an
        rbac_engine is wired, and principal_id is not authorized for
        permission. A no-op if principal_id is None (authorization was
        not requested) or no rbac_engine is wired.
        """

        if principal_id is None or self._rbac_engine is None:
            return

        decision = self._rbac_engine.authorize(principal_id, permission)

        if not decision.allowed:
            raise PermissionError(
                f"principal '{principal_id}' is not authorized for "
                f"'{permission}'"
            )

    def _build(self, *, event_type: str) -> RolloutDashboard:
        deployments = self._build_deployment_entries()

        active = 0
        completed = 0
        failed = 0

        if self._rollout_manager is not None:
            for rollout in self._rollout_manager.list():
                if rollout.state in _ROLLOUT_ACTIVE_STATES:
                    active += 1

                elif rollout.state == "COMPLETED":
                    completed += 1

                elif rollout.state == "FAILED":
                    failed += 1

        dashboard = RolloutDashboard(
            generated_at=self._clock(),
            active_rollouts=active,
            completed_rollouts=completed,
            failed_rollouts=failed,
            deployments=deployments,
        )

        with self._lock:
            self._cached = dashboard
            self._cached_at = dashboard.generated_at

        self._publish(event_type, dashboard)

        return dashboard

    def _build_deployment_entries(
        self,
    ) -> "tuple[DeploymentDashboardEntry, ...]":
        deployment_ids: "set[str]" = set()

        if self._rollout_manager is not None:
            deployment_ids.update(
                rollout.deployment_id
                for rollout in self._rollout_manager.list()
            )

        if self._version_registry is not None:
            deployment_ids.update(
                version.deployment_id
                for version in self._version_registry.list()
            )

        if self._traffic_router is not None:
            deployment_ids.update(
                snapshot.deployment_id
                for snapshot in self._traffic_router.list()
            )

        if self._health_engine is not None:
            deployment_ids.update(
                snapshot.deployment_id
                for snapshot in self._health_engine.list()
            )

        now = self._clock()

        return tuple(
            self._build_entry(deployment_id, now)
            for deployment_id in sorted(deployment_ids)
        )

    def _build_entry(
        self, deployment_id: str, now: datetime
    ) -> DeploymentDashboardEntry:
        version = ""
        latest_timestamps: "list[datetime]" = []

        if self._version_registry is not None:
            try:
                version = self._version_registry.get(
                    deployment_id
                ).version

            except KeyError:
                pass

        strategy = ""
        state = ""

        if self._rollout_manager is not None:
            deployment_rollouts = sorted(
                (
                    rollout
                    for rollout in self._rollout_manager.list()
                    if rollout.deployment_id == deployment_id
                ),
                key=lambda rollout: rollout.created_at,
            )

            if deployment_rollouts:
                latest_rollout = deployment_rollouts[-1]
                strategy = latest_rollout.strategy

                try:
                    status = self._rollout_manager.status(
                        latest_rollout.rollout_id
                    )
                    state = status.state
                    latest_timestamps.append(status.updated_at)

                except KeyError:
                    state = latest_rollout.state

        health = "UNKNOWN"

        if self._health_engine is not None:
            try:
                snapshot = self._health_engine.latest(deployment_id)
                health = snapshot.status
                latest_timestamps.append(snapshot.evaluated_at)

            except KeyError:
                pass

        traffic_percentage = 0.0

        if self._traffic_router is not None:
            try:
                routing_snapshot = self._traffic_router.snapshot(
                    deployment_id
                )
                latest_timestamps.append(routing_snapshot.updated_at)

                traffic_percentage = self._traffic_percentage_for(
                    routing_snapshot, version
                )

            except KeyError:
                pass

        updated_at = max(latest_timestamps) if latest_timestamps else now

        return DeploymentDashboardEntry(
            deployment_id=deployment_id,
            version=version,
            strategy=strategy,
            health=health,
            traffic_percentage=traffic_percentage,
            state=state,
            updated_at=updated_at,
        )

    def _traffic_percentage_for(
        self, routing_snapshot: "RoutingSnapshot", version: str
    ) -> float:
        if not routing_snapshot.allocations:
            return 0.0

        for allocation in routing_snapshot.allocations:
            if allocation.version == version:
                return allocation.percentage

        return max(
            allocation.percentage
            for allocation in routing_snapshot.allocations
        )

    def _publish(
        self, event_type: str, dashboard: RolloutDashboard
    ) -> None:
        if self._event_bus is None:
            return

        self._event_bus.publish(
            event_type, source="rollout-dashboard",
            payload={
                "active_rollouts": dashboard.active_rollouts,
                "completed_rollouts": dashboard.completed_rollouts,
                "failed_rollouts": dashboard.failed_rollouts,
            },
        )


def build_default_governance_rollout_dashboard() -> (
    DeploymentRolloutDashboard
):
    """
    Build the process-wide rollout dashboard, wired to the process-
    wide governance event bus, rollout manager, version registry,
    traffic router, health engine, rollback engine, analytics engine,
    and rollout policy engine.
    """

    from .deployment_governance_event_bus import get_event_bus
    from .deployment_governance_rollback import get_rollback_engine
    from .deployment_governance_rollout_analytics import (
        get_rollout_analytics,
    )
    from .deployment_governance_rollout_health import (
        get_rollout_health_engine,
    )
    from .deployment_governance_rollout_manager import (
        get_rollout_manager,
    )
    from .deployment_governance_rollout_policy import (
        get_rollout_policy_engine,
    )
    from .deployment_governance_traffic_router import get_traffic_router
    from .deployment_governance_version_registry import (
        get_version_registry,
    )

    return DeploymentRolloutDashboard(
        event_bus=get_event_bus(),
        rollout_manager=get_rollout_manager(),
        version_registry=get_version_registry(),
        traffic_router=get_traffic_router(),
        health_engine=get_rollout_health_engine(),
        rollback_engine=get_rollback_engine(),
        analytics=get_rollout_analytics(),
        policy_engine=get_rollout_policy_engine(),
    )


# Shared for the lifetime of the process, matching every other
# dashboard/aggregation singleton in this codebase — mainly so its
# cache (when cache_ttl_seconds > 0) is actually shared across
# requests instead of being pointlessly rebuilt fresh, empty, every
# time.
_rollout_dashboard = build_default_governance_rollout_dashboard()


def get_rollout_dashboard() -> DeploymentRolloutDashboard:
    """
    Return the process-wide rollout dashboard.
    """

    return _rollout_dashboard
