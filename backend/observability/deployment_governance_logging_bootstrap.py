from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_logging import GovernanceIntegrityLogger
    from .deployment_governance_log_repository import (
        GovernanceLogRepository,
    )
    from .deployment_governance_log_batcher import GovernanceLogBatcher
    from .deployment_governance_log_sampling import (
        GovernanceLogSamplingService,
    )
    from .deployment_governance_log_redaction import (
        GovernanceLogRedactionService,
    )
    from .deployment_governance_log_rotation import (
        GovernanceLogRotationService,
    )
    from .deployment_governance_log_context import (
        GovernanceLogContextService,
    )
    from .deployment_governance_log_correlation import (
        GovernanceCorrelationService,
    )
    from .deployment_governance_log_search import (
        GovernanceLogSearchService,
    )
    from .deployment_governance_log_export import (
        GovernanceLogExportService,
    )
    from .deployment_governance_log_replay import (
        GovernanceLogReplayService,
    )
    from .deployment_governance_log_config import (
        GovernanceLogConfigService,
    )
    from .deployment_governance_persistence import (
        DeploymentGovernancePersistenceRuntime,
    )

_REQUIRED_DEPENDENCY_NAMES = (
    "log_config_service",
    "logger",
    "log_repository",
    "batcher",
    "sampling_service",
    "redaction_service",
    "log_rotation_service",
    "context_service",
    "correlation_service",
    "search_service",
    "export_service",
    "replay_service",
)


@dataclass(frozen=True)
class GovernanceLoggingBootstrapHealth:
    """
    A point-in-time snapshot of the logging bootstrap's own
    lifecycle state, distinct from the logs it wires the subsystem
    to record: this describes whether the subsystem itself is
    built, initialized, and structurally sound, not what has been
    logged.
    """

    built: bool

    initialized: bool

    dependencies_valid: bool

    pending_batch_count: int

    buffered_entry_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "built": self.built,
            "initialized": self.initialized,
            "dependencies_valid": self.dependencies_valid,
            "pending_batch_count": self.pending_batch_count,
            "buffered_entry_count": self.buffered_entry_count,
        }


class GovernanceLoggingBootstrap:
    """
    Constructs, wires, and manages the lifecycle of every governance
    logging component as a single unit: the logger itself, its
    repository, batcher, sampler, redaction and rotation services,
    execution-context and correlation services, and the
    search/export/replay services built on top of them.

    Replaces the previous pattern of
    DeploymentGovernancePersistenceRuntime.__post_init__ wiring each
    logging-related dependency independently: every one of those
    same shared singletons is still built through the persistence
    runtime's own build_integrity_log_* accessors (this bootstrap
    does not construct a second, competing set of instances), so a
    bootstrap and the persistence runtime it wraps always agree on
    which logger, repository, etc. are actually live. Constructing
    more than one bootstrap for the same persistence runtime is
    supported but pointless: every build_integrity_log_* accessor
    it delegates to already returns that persistence runtime's own
    singleton, so two bootstraps for the same runtime always end up
    holding the exact same underlying instances.

    Lifecycle is two-phase and strict, mirroring
    GovernanceIntegrityMetricsBootstrap: build() constructs and
    wires every service and validates that none of them came back
    None (failing fast on a broken persistence runtime rather than
    surfacing a confusing AttributeError somewhere later).
    initialize() then applies current configuration to the wired
    services. Both phases are single-shot: calling either twice
    raises rather than silently reconstructing or re-initializing an
    already-live subsystem. shutdown() is safe to call at any point,
    including before build()/initialize(), or more than once, and
    always flushes any batcher entries still pending so a shutdown
    never silently drops unwritten logs.
    """

    def __init__(
        self,
        persistence_runtime: "DeploymentGovernancePersistenceRuntime",
    ) -> None:
        if persistence_runtime is None:
            raise ValueError(
                "persistence_runtime is required"
            )

        self._persistence_runtime = persistence_runtime

        self._built = False

        self._initialized = False

        self.log_config_service: (
            "GovernanceLogConfigService | None"
        ) = None

        self.logger: "GovernanceIntegrityLogger | None" = None

        self.log_repository: "GovernanceLogRepository | None" = None

        self.batcher: "GovernanceLogBatcher | None" = None

        self.sampling_service: (
            "GovernanceLogSamplingService | None"
        ) = None

        self.redaction_service: (
            "GovernanceLogRedactionService | None"
        ) = None

        self.log_rotation_service: (
            "GovernanceLogRotationService | None"
        ) = None

        self.context_service: (
            "GovernanceLogContextService | None"
        ) = None

        self.correlation_service: (
            "GovernanceCorrelationService | None"
        ) = None

        self.search_service: (
            "GovernanceLogSearchService | None"
        ) = None

        self.export_service: (
            "GovernanceLogExportService | None"
        ) = None

        self.replay_service: (
            "GovernanceLogReplayService | None"
        ) = None

    def build(self) -> "GovernanceLoggingBootstrap":
        """
        Construct (by delegating to the persistence runtime's own
        build_integrity_log_* accessors) and validate every logging
        service. Performs no I/O and starts nothing beyond what the
        persistence runtime already did at its own construction;
        call initialize() afterward to apply current configuration.

        Raises RuntimeError if already built, or if any required
        dependency is missing after construction.
        """

        if self._built:
            raise RuntimeError(
                "logging bootstrap has already been built"
            )

        runtime = self._persistence_runtime

        self.log_config_service = (
            runtime.build_integrity_log_config_service()
        )

        self.logger = runtime.build_integrity_logger()

        self.log_repository = runtime.build_integrity_log_repository()

        self.batcher = runtime.build_integrity_log_batcher()

        self.sampling_service = (
            runtime.build_integrity_log_sampling_service()
        )

        self.redaction_service = (
            runtime.build_integrity_log_redaction_service()
        )

        self.log_rotation_service = (
            runtime.build_integrity_log_rotation_service()
        )

        self.context_service = (
            runtime.build_integrity_log_context_service()
        )

        self.correlation_service = (
            runtime.build_integrity_log_correlation_service()
        )

        self.search_service = (
            runtime.build_integrity_log_search_service()
        )

        self.export_service = (
            runtime.build_integrity_log_export_service()
        )

        self.replay_service = (
            runtime.build_integrity_log_replay_service()
        )

        self._validate_dependencies()

        self._built = True

        return self

    def initialize(self) -> None:
        """
        Activate the built subsystem: apply the currently loaded
        governance logging configuration to the logger (minimum
        level, sampling/redaction toggles) and the batcher (batch
        size, flush interval).

        Raises RuntimeError if build() has not run yet, or if
        already initialized.
        """

        if not self._built:
            raise RuntimeError(
                "logging bootstrap must be built before it can be "
                "initialized"
            )

        if self._initialized:
            raise RuntimeError(
                "logging bootstrap has already been initialized"
            )

        self._persistence_runtime.reload_log_config()

        self._initialized = True

    def shutdown(self) -> None:
        """
        Flush any batcher entries still pending to the repository.

        Safe to call multiple times, and safe to call even if
        initialize() was never reached: both are no-ops in that
        case beyond the flush itself, which always runs if a
        batcher was built.
        """

        if self.batcher is not None:
            self.batcher.flush()

        self._initialized = False

    def health(self) -> GovernanceLoggingBootstrapHealth:
        """
        Return the bootstrap's current lifecycle state.
        """

        dependencies_valid = self._built and all(
            getattr(self, name) is not None
            for name in _REQUIRED_DEPENDENCY_NAMES
        )

        return GovernanceLoggingBootstrapHealth(
            built=self._built,
            initialized=self._initialized,
            dependencies_valid=dependencies_valid,
            pending_batch_count=(
                0
                if self.batcher is None
                else self.batcher.pending_count()
            ),
            buffered_entry_count=(
                0
                if self.logger is None
                else self.logger.buffered_count()
            ),
        )

    def _validate_dependencies(self) -> None:
        missing = [
            name
            for name in _REQUIRED_DEPENDENCY_NAMES
            if getattr(self, name) is None
        ]

        if missing:
            raise RuntimeError(
                "governance logging bootstrap is missing required "
                f"dependencies: {', '.join(missing)}"
            )


def build_integrity_logging_bootstrap(
    persistence_runtime: "DeploymentGovernancePersistenceRuntime",
) -> GovernanceLoggingBootstrap:
    """
    Build (but do not initialize) a GovernanceLoggingBootstrap for
    persistence_runtime.

    Raises ValueError if persistence_runtime is None, or RuntimeError
    if any required dependency is missing after construction.
    """

    return GovernanceLoggingBootstrap(persistence_runtime).build()
