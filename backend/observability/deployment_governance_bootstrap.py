from __future__ import annotations

from typing import TYPE_CHECKING

from .deployment_governance_dependency_graph import (
    DependencyValidationResult,
    GovernanceDependencyGraph,
)

if TYPE_CHECKING:
    from .deployment_governance_delivery_runtime import (
        GovernanceIntegrityDeliveryRuntime,
    )
    from .deployment_governance_lifecycle import (
        GovernanceLifecycleManager,
    )

# The fixed, declarative shape of the governance runtime's startup
# dependencies. provider_registry, metrics_bootstrap, and
# logging_bootstrap have no dependencies of their own and can be
# built in any order; delivery_runtime wires all three together; the
# observability services (health/readiness/liveness/diagnostics) are
# all built from an already-running delivery runtime, so each
# depends on it. security_bootstrap has no dependency on any of
# these either — the deployment security subsystem (RBAC,
# authentication, secrets, approvals, audit, compliance, risk,
# security scanning, integrity verification, incident response,
# reporting, and the security dashboard) is wired independently of
# the delivery runtime and rollout subsystem, the same way
# rollout_manager's own single "scheduler" dependency does not touch
# delivery_runtime either.
_COMPONENT_DEPENDENCIES: "dict[str, tuple[str, ...]]" = {
    "provider_registry": (),
    "metrics_bootstrap": (),
    "logging_bootstrap": (),
    "delivery_runtime": (
        "provider_registry",
        "metrics_bootstrap",
        "logging_bootstrap",
    ),
    "health_service": ("delivery_runtime",),
    "readiness_service": ("delivery_runtime",),
    "liveness_service": ("delivery_runtime",),
    "diagnostics_service": ("delivery_runtime",),
    "scheduler": (),
    "rollout_manager": ("scheduler",),
    "security_bootstrap": (),
}


class GovernanceBootstrapError(RuntimeError):
    """
    Raised when the governance runtime's dependency graph fails
    validation, aborting startup rather than letting a broken
    component order proceed.
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
            "governance dependency graph validation failed"
            + (f" ({'; '.join(details)})" if details else "")
        )


def build_governance_dependency_graph() -> GovernanceDependencyGraph:
    """
    Build the dependency graph describing the governance runtime's
    fixed component startup order: the provider registry, metrics
    bootstrap, and logging bootstrap are registered first (they have
    no dependencies), the delivery runtime that wires them together
    next, and finally the observability services built from it
    (health, readiness, liveness, diagnostics).
    """

    graph = GovernanceDependencyGraph()

    for name, dependencies in _COMPONENT_DEPENDENCIES.items():
        graph.register(name, dependencies=dependencies)

    return graph


def bootstrap_governance_runtime() -> DependencyValidationResult:
    """
    Validate the governance runtime's component dependency graph
    before startup.

    This is a pre-flight check, not a replacement for the existing
    build_integrity_* construction functions: those still perform the
    actual wiring. This only confirms, before any of them run, that
    the components they wire together form a valid startup order.

    Raises GovernanceBootstrapError (aborting startup) if validation
    fails — a missing dependency or a circular dependency.
    """

    result = build_governance_dependency_graph().validate()

    if not result.valid:
        raise GovernanceBootstrapError(result)

    return result


def build_governance_lifecycle_manager(
    delivery_runtime: "GovernanceIntegrityDeliveryRuntime",
) -> "GovernanceLifecycleManager":
    """
    Build a GovernanceLifecycleManager wired to orchestrate an
    already-constructed delivery runtime and the observability
    services built from it, replacing the previous pattern of each
    caller independently sequencing
    runtime.start()/liveness_service.start()/... calls by hand.

    provider_registry, metrics_bootstrap, and logging_bootstrap are
    already fully constructed and wired into delivery_runtime by the
    time it is passed in here (that composition is still
    build_integrity_delivery_runtime's job, not this manager's), and
    health_service/readiness_service/diagnostics_service are
    stateless builders with nothing of their own to start or stop —
    all five are registered with no-op start/stop so the dependency
    graph and status() reporting stay complete, not because this
    manager constructs or tears any of them down itself.

    delivery_runtime and liveness_service are the two components this
    manager actually drives: starting/stopping them calls
    delivery_runtime.start()/stop() and
    delivery_runtime.liveness_service.start()/reset() respectively.
    """

    from .deployment_governance_lifecycle import (
        GovernanceLifecycleManager,
    )

    manager = GovernanceLifecycleManager()

    def _noop() -> None:
        return None

    for name in (
        "provider_registry",
        "metrics_bootstrap",
        "logging_bootstrap",
        "health_service",
        "readiness_service",
        "diagnostics_service",
    ):
        manager.register(
            name,
            dependencies=_COMPONENT_DEPENDENCIES[name],
            start=_noop,
            stop=_noop,
        )

    manager.register(
        "delivery_runtime",
        dependencies=_COMPONENT_DEPENDENCIES["delivery_runtime"],
        start=delivery_runtime.start,
        stop=delivery_runtime.stop,
    )

    manager.register(
        "liveness_service",
        dependencies=_COMPONENT_DEPENDENCIES["liveness_service"],
        start=delivery_runtime.liveness_service.start,
        stop=delivery_runtime.liveness_service.reset,
    )

    return manager
