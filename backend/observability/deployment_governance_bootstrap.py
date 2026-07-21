from __future__ import annotations

from .deployment_governance_dependency_graph import (
    DependencyValidationResult,
    GovernanceDependencyGraph,
)

# The fixed, declarative shape of the governance runtime's startup
# dependencies. provider_registry, metrics_bootstrap, and
# logging_bootstrap have no dependencies of their own and can be
# built in any order; delivery_runtime wires all three together; the
# observability services (health/readiness/liveness/diagnostics) are
# all built from an already-running delivery runtime, so each
# depends on it.
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
