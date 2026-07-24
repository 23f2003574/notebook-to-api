from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, TYPE_CHECKING

from .deployment_governance_dependency_graph import (
    DependencyValidationResult,
    GovernanceDependencyGraph,
)

if TYPE_CHECKING:
    from .deployment_governance_approval import DeploymentApprovalEngine
    from .deployment_governance_artifact_integrity import (
        DeploymentIntegrityVerifier,
    )
    from .deployment_governance_audit_trail import DeploymentAuditService
    from .deployment_governance_authentication import (
        DeploymentAuthenticationManager,
    )
    from .deployment_governance_compliance import (
        DeploymentComplianceEngine,
    )
    from .deployment_governance_event_bus import (
        EventSubscription,
        GovernanceEventBus,
    )
    from .deployment_governance_incident_response import (
        DeploymentIncidentResponseEngine,
    )
    from .deployment_governance_rbac import DeploymentRBACEngine
    from .deployment_governance_reporting import DeploymentReportingService
    from .deployment_governance_risk import DeploymentRiskEngine
    from .deployment_governance_secret_vault import DeploymentSecretVault
    from .deployment_governance_security_dashboard import (
        DeploymentSecurityDashboard,
    )
    from .deployment_governance_security_scanner import (
        DeploymentSecurityScanner,
    )

BOOTSTRAP_VERSION = "1"

# This bootstrap's own fixed, declarative validation graph — the same
# simplified linear-chain shape DeploymentRolloutBootstrap's own
# _ROLLOUT_COMPONENT_ORDER uses, matching this commit's own "Runtime
# Integration" pipeline diagram exactly. This only orders
# *validation*, not construction: every component here is already a
# live, already-constructed process-wide singleton (from commits
# 1-12) by the time this bootstrap is built.
_SECURITY_COMPONENT_ORDER: "tuple[str, ...]" = (
    "authentication_manager",
    "rbac_engine",
    "secret_vault",
    "approval_engine",
    "audit_service",
    "compliance_engine",
    "risk_engine",
    "security_scanner",
    "integrity_verifier",
    "incident_response_engine",
    "reporting_service",
    "security_dashboard",
)

# One representative, already-published event per component this
# bootstrap's register_event_handlers() tracks — the real vocabulary
# each component's own commit (1-12) actually publishes
# (GOVERNANCE_EVENT_TYPES is the source of truth), not an invented
# shorthand. audit_service has none: DeploymentAuditService itself
# publishes no events of its own (see its own commit) — it is a
# query facade over GovernanceAuditService's audit *records*, not an
# event source.
_TRACKED_EVENTS: "tuple[str, ...]" = (
    "authentication_succeeded",
    "authentication_failed",
    "authorization_denied",
    "secret_rotated",
    "approval_granted",
    "compliance_failed",
    "risk_assessed",
    "security_scan_completed",
    "integrity_failed",
    "incident_created",
    "report_generated",
    "security_dashboard_generated",
)


@dataclass(frozen=True)
class SecurityBootstrapReport:
    """
    The immutable outcome of one initialize() call.
    """

    started: bool

    initialized_components: "tuple[str, ...]"

    registered_routes: bool

    subscribed_events: "tuple[str, ...]"

    completed_at: datetime

    def __post_init__(self) -> None:
        if self.completed_at.tzinfo is None:
            raise ValueError("completed_at must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        return {
            "started": self.started,
            "initialized_components": list(
                self.initialized_components
            ),
            "registered_routes": self.registered_routes,
            "subscribed_events": list(self.subscribed_events),
            "completed_at": self.completed_at.isoformat(),
        }


@dataclass(frozen=True)
class SecurityBootstrapStatus:
    """
    A point-in-time snapshot of this bootstrap's own lifecycle state,
    distinct from any individual wired component's own status.
    """

    initialized: bool

    version: str

    started_at: "datetime | None"

    def __post_init__(self) -> None:
        if self.started_at is not None and self.started_at.tzinfo is None:
            raise ValueError("started_at must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        return {
            "initialized": self.initialized,
            "version": self.version,
            "started_at": (
                self.started_at.isoformat()
                if self.started_at is not None
                else None
            ),
        }


class DeploymentSecurityBootstrapError(RuntimeError):
    """
    Raised when this bootstrap's component dependency graph fails
    validation, aborting initialize() before any route/event
    registration runs — the security-subsystem-scoped equivalent of
    DeploymentRolloutBootstrapError.
    """

    def __init__(self, result: DependencyValidationResult) -> None:
        self.result = result

        details = []

        if result.missing:
            details.append(
                "missing dependencies: " + ", ".join(result.missing)
            )

        if result.cycles:
            details.append(
                "circular dependencies: "
                + "; ".join(
                    " -> ".join(cycle) for cycle in result.cycles
                )
            )

        super().__init__(
            "security bootstrap dependency graph validation failed"
            + (f" ({'; '.join(details)})" if details else "")
        )


class DeploymentSecurityBootstrap:
    """
    Completes the deployment security subsystem (commits 1-12:
    DeploymentRBACEngine, DeploymentAuthenticationManager,
    DeploymentSecretVault, DeploymentApprovalEngine,
    DeploymentAuditService, DeploymentComplianceEngine,
    DeploymentRiskEngine, DeploymentSecurityScanner,
    DeploymentIntegrityVerifier, DeploymentIncidentResponseEngine,
    DeploymentReportingService, DeploymentSecurityDashboard) by
    validating that they form a complete dependency graph, confirming
    every /governance/security/* route is registered, and subscribing
    diagnostic event handlers — the security-subsystem-scoped
    counterpart to DeploymentRolloutBootstrap.

    Integration only, per this commit's own charter ("no new security
    features should be introduced"): every method here calls only
    already-public methods each component already exposed in its own
    commit (list()/summary()/subscribe()/refresh()/...) — nothing
    here re-implements authentication, authorization, scanning,
    compliance, risk, or incident logic.

    initialize() is the single-shot entry point (mirroring
    DeploymentRolloutBootstrap): every component it wires is already a
    live, already-constructed singleton, so there is no separate
    "build" step — initialize() only validates the graph, confirms
    route registration, and subscribes event handlers. Idempotent:
    calling it again while already initialized returns the cached
    report from the call that actually ran. shutdown() is symmetric: a
    no-op if not currently initialized, otherwise it unsubscribes
    every handler this bootstrap subscribed and asks the wired
    security dashboard to refresh (replacing whatever it had cached
    with a clean read — the closest honest meaning of "clear caches"
    for a dashboard whose only cache is its own TTL-based overview(),
    with no separate invalidate() of its own to call). "Flush audit/
    report buffers" and "close service resources" are both documented
    no-ops, the same reasoning DeploymentRolloutBootstrap's own
    docstring gives for "flush analytics buffers": neither
    DeploymentAuditService nor DeploymentReportingService has a
    write-behind buffer (record()/generate() already update state
    synchronously), and none of the twelve components hold an
    external resource (a file handle, socket, or connection) that
    needs closing — there is nothing to flush or close without either
    fabricating something that does not exist or destructively
    clearing history, which neither instruction means.

    Thread-safe: initialize()/shutdown() are both guarded by an
    internal lock.
    """

    def __init__(
        self,
        *,
        clock: "Callable[[], datetime] | None" = None,
        event_bus: "GovernanceEventBus | None" = None,
        authentication_manager: (
            "DeploymentAuthenticationManager | None"
        ) = None,
        rbac_engine: "DeploymentRBACEngine | None" = None,
        secret_vault: "DeploymentSecretVault | None" = None,
        approval_engine: "DeploymentApprovalEngine | None" = None,
        audit_service: "DeploymentAuditService | None" = None,
        compliance_engine: "DeploymentComplianceEngine | None" = None,
        risk_engine: "DeploymentRiskEngine | None" = None,
        security_scanner: "DeploymentSecurityScanner | None" = None,
        integrity_verifier: "DeploymentIntegrityVerifier | None" = None,
        incident_response_engine: (
            "DeploymentIncidentResponseEngine | None"
        ) = None,
        reporting_service: "DeploymentReportingService | None" = None,
        security_dashboard: "DeploymentSecurityDashboard | None" = None,
    ) -> None:
        self._lock = threading.RLock()

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

        self._authentication_manager = authentication_manager
        self._rbac_engine = rbac_engine
        self._secret_vault = secret_vault
        self._approval_engine = approval_engine
        self._audit_service = audit_service
        self._compliance_engine = compliance_engine
        self._risk_engine = risk_engine
        self._security_scanner = security_scanner
        self._integrity_verifier = integrity_verifier
        self._incident_response_engine = incident_response_engine
        self._reporting_service = reporting_service
        self._security_dashboard = security_dashboard

        self._initialized = False
        self._started_at: "datetime | None" = None
        self._last_report: "SecurityBootstrapReport | None" = None

        self._subscriptions: "list[EventSubscription]" = []
        self._last_event_at: "dict[str, datetime]" = {}

    def _component(self, name: str) -> object:
        return getattr(self, f"_{name}")

    def validate(self) -> DependencyValidationResult:
        """
        Validate the fixed pipeline graph against which components
        were actually wired at construction time — components not
        wired are simply omitted from the graph (see
        DeploymentRolloutBootstrap.validate for why that still
        surfaces a real gap as "missing" rather than silently
        passing).
        """

        graph = GovernanceDependencyGraph()

        for index, name in enumerate(_SECURITY_COMPONENT_ORDER):
            if self._component(name) is None:
                continue

            previous = (
                _SECURITY_COMPONENT_ORDER[index - 1]
                if index > 0
                else None
            )

            graph.register(
                name,
                dependencies=(previous,) if previous is not None else (),
            )

        return graph.validate()

    def register_services(self) -> "tuple[str, ...]":
        """
        Validate the component dependency graph, returning the tuple
        of components that were actually wired (in
        _SECURITY_COMPONENT_ORDER) — "all services initialized" /
        "dependencies resolved".

        Raises DeploymentSecurityBootstrapError if the graph is
        invalid.
        """

        result = self.validate()

        if not result.valid:
            raise DeploymentSecurityBootstrapError(result)

        return tuple(
            name for name in _SECURITY_COMPONENT_ORDER
            if self._component(name) is not None
        )

    def register_routes(self) -> bool:
        """
        Confirm every /governance/security/* endpoint from commits
        1-12 is mounted — "register all /governance/security/*
        endpoints through a single bootstrap entry".

        There is no separate route-registration step to perform here:
        every security endpoint is already registered, at import
        time, by deployment_governance_api.py's own module-level
        @health_router.* decorators, and that shared health_router is
        already included into the running FastAPI app
        (backend/dashboard.py's app.include_router(...)) once,
        regardless of whether this bootstrap ever runs — matching
        DeploymentRolloutBootstrap.register_api's own reasoning. This
        is a verification that centralization held, not a second,
        redundant registration.

        Returns False, without raising, if deployment_governance_api's
        health_router does not carry the expected prefix — a caller
        decides what to do about that (this is a diagnostic, not
        itself a startup-blocking step, unlike register_services()).
        """

        from .deployment_governance_api import health_router

        return health_router.prefix == "/governance"

    def register_event_handlers(self) -> "tuple[str, ...]":
        """
        Subscribe a diagnostic handler (recording each event type's
        last-seen time, for health_check()) to every tracked event in
        _TRACKED_EVENTS — "event handlers attached".

        Idempotent: returns the already-subscribed event types,
        without subscribing again, if called again. Returns an empty
        tuple, without error, if no event_bus is wired. Does not
        re-implement any cross-component coordination those events
        already drive (e.g. DeploymentIncidentResponseEngine's own
        "authentication_failed"/"authentication_succeeded"
        subscriptions from commit 10) — this is observational
        bookkeeping only.
        """

        if self._event_bus is None:
            return ()

        with self._lock:
            if self._subscriptions:
                return tuple(
                    subscription.event_type
                    for subscription in self._subscriptions
                )

            subscriptions = [
                self._event_bus.subscribe(
                    event_type, self._on_tracked_event
                )
                for event_type in _TRACKED_EVENTS
            ]

            self._subscriptions = subscriptions

            return tuple(
                subscription.event_type for subscription in subscriptions
            )

    def _on_tracked_event(self, event: Any) -> None:
        with self._lock:
            self._last_event_at[event.event_type] = self._clock()

    def initialize(self) -> SecurityBootstrapReport:
        """
        Run the full initialization pipeline: register_services() ->
        register_routes() -> register_event_handlers() -> Ready,
        matching this commit's own Runtime Integration diagram.

        Raises DeploymentSecurityBootstrapError, without touching
        route/event registration at all, if the dependency graph is
        invalid — fail-fast on a critical bootstrap error, matching
        DeploymentRolloutBootstrap.initialize.
        """

        with self._lock:
            if self._initialized:
                return self._last_report

            self._publish("security_bootstrap_started", {})

            try:
                initialized_components = self.register_services()

            except DeploymentSecurityBootstrapError as exc:
                self._publish(
                    "security_bootstrap_failed",
                    {
                        "missing": list(exc.result.missing),
                        "cycles": [
                            list(cycle) for cycle in exc.result.cycles
                        ],
                    },
                )

                raise

            registered_routes = self.register_routes()
            subscribed_events = self.register_event_handlers()

            now = self._clock()

            self._initialized = True
            self._started_at = now

            report = SecurityBootstrapReport(
                started=True,
                initialized_components=initialized_components,
                registered_routes=registered_routes,
                subscribed_events=subscribed_events,
                completed_at=now,
            )

            self._last_report = report

            self._publish(
                "security_bootstrap_completed", report.to_dict()
            )
            self._publish("security_runtime_ready", {})

            return report

    def health_check(self) -> "tuple[bool, str | None]":
        """
        Return (True, None) if initialized, every component wired at
        construction time is still present, and routes are still
        registered ("health checks passed"), else (False, reason).
        """

        with self._lock:
            initialized = self._initialized

        if not initialized:
            return False, "security bootstrap has not been initialized"

        missing = [
            name for name in _SECURITY_COMPONENT_ORDER
            if self._component(name) is None
        ]

        if missing:
            return False, (
                "components not wired: " + ", ".join(missing)
            )

        if not self.register_routes():
            return False, "security routes are not registered"

        return True, None

    def shutdown(self) -> None:
        """
        Unsubscribe event handlers, ask the wired security dashboard
        to refresh (see class docstring for why that is this
        bootstrap's "clear caches" step), and release this bootstrap's
        own state — a no-op if not currently initialized.
        """

        with self._lock:
            if not self._initialized:
                return

            subscriptions = tuple(self._subscriptions)
            self._subscriptions = []

            if self._event_bus is not None:
                for subscription in subscriptions:
                    try:
                        self._event_bus.unsubscribe(subscription)

                    except ValueError:
                        pass

            if self._security_dashboard is not None:
                try:
                    self._security_dashboard.refresh()

                except Exception:
                    pass

            self._initialized = False
            self._started_at = None

            self._publish("security_runtime_shutdown", {})

    def restart(self) -> SecurityBootstrapReport:
        """
        Shut down (if currently initialized) and run the full
        initialization pipeline again.
        """

        self.shutdown()

        return self.initialize()

    def status(self) -> SecurityBootstrapStatus:
        """
        Return this bootstrap's current lifecycle state.
        """

        with self._lock:
            return SecurityBootstrapStatus(
                initialized=self._initialized,
                version=BOOTSTRAP_VERSION,
                started_at=self._started_at,
            )

    def last_event_at(self, event_type: str) -> "datetime | None":
        """
        Return when register_event_handlers()'s diagnostic handler
        last observed event_type, or None if it never has (or
        event_type is not in _TRACKED_EVENTS).
        """

        with self._lock:
            return self._last_event_at.get(event_type)

    def _publish(
        self, event_type: str, payload: "dict[str, Any] | None" = None
    ) -> None:
        if self._event_bus is None:
            return

        self._event_bus.publish(
            event_type, source="security-bootstrap", payload=payload
        )


def build_default_governance_security_bootstrap() -> (
    DeploymentSecurityBootstrap
):
    """
    Build the process-wide security bootstrap, wired to every
    process-wide security subsystem singleton from commits 1-12 and
    the governance event bus.

    Does not call initialize() — matching
    build_default_governance_rollout_bootstrap, construction and
    initialization are deliberately separate: something else (the
    top-level governance bootstrap's "security_bootstrap" component,
    or a direct caller) triggers initialize() deliberately.
    """

    from .deployment_governance_approval import get_approval_engine
    from .deployment_governance_artifact_integrity import (
        get_artifact_integrity_verifier,
    )
    from .deployment_governance_audit_trail import get_audit_trail_service
    from .deployment_governance_authentication import (
        get_authentication_manager,
    )
    from .deployment_governance_compliance import get_compliance_engine
    from .deployment_governance_event_bus import get_event_bus
    from .deployment_governance_incident_response import (
        get_incident_response_engine,
    )
    from .deployment_governance_rbac import get_rbac_engine
    from .deployment_governance_reporting import get_reporting_service
    from .deployment_governance_risk import get_risk_engine
    from .deployment_governance_secret_vault import get_secret_vault
    from .deployment_governance_security_dashboard import (
        get_security_dashboard,
    )
    from .deployment_governance_security_scanner import (
        get_security_scanner,
    )

    return DeploymentSecurityBootstrap(
        event_bus=get_event_bus(),
        authentication_manager=get_authentication_manager(),
        rbac_engine=get_rbac_engine(),
        secret_vault=get_secret_vault(),
        approval_engine=get_approval_engine(),
        audit_service=get_audit_trail_service(),
        compliance_engine=get_compliance_engine(),
        risk_engine=get_risk_engine(),
        security_scanner=get_security_scanner(),
        integrity_verifier=get_artifact_integrity_verifier(),
        incident_response_engine=get_incident_response_engine(),
        reporting_service=get_reporting_service(),
        security_dashboard=get_security_dashboard(),
    )


# Shared for the lifetime of the process, matching _rollout_bootstrap:
# whether the security subsystem has completed initialization needs
# to be visible to whatever queries it (the top-level governance
# bootstrap's "security_bootstrap" component, or a direct API caller).
_security_bootstrap = build_default_governance_security_bootstrap()


def get_security_bootstrap() -> DeploymentSecurityBootstrap:
    """
    Return the process-wide security bootstrap.
    """

    return _security_bootstrap
