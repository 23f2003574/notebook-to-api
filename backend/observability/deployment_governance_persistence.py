from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Final, Mapping

from backend.persistence.sqlite_database import (
    SQLiteDatabase,
    SQLiteDatabaseConfig,
)

from .deployment_governance_audit_bookmarks import (
    GovernanceIntegrityAuditBookmarkRepository,
    InMemoryGovernanceIntegrityAuditBookmarkRepository,
)
from .deployment_governance_audit_labels import (
    GovernanceIntegrityAuditLabelRepository,
    InMemoryGovernanceIntegrityAuditLabelRepository,
)
from .deployment_governance_audit_saved_queries import (
    GovernanceIntegritySavedAuditQueryRepository,
    InMemoryGovernanceIntegritySavedAuditQueryRepository,
)
from .deployment_governance_audit_collections import (
    GovernanceIntegrityAuditCollectionRepository,
    InMemoryGovernanceIntegrityAuditCollectionRepository,
)
from .deployment_governance_audit_report_templates import (
    GovernanceIntegrityAuditReportTemplateRepository,
    InMemoryGovernanceIntegrityAuditReportTemplateRepository,
)
from .deployment_governance_audit_report_schedule import (
    GovernanceIntegrityAuditReportScheduleRepository,
    InMemoryGovernanceIntegrityAuditReportScheduleRepository,
)
from .deployment_governance_audit_execution_queue import (
    GovernanceIntegrityAuditExecutionQueueRepository,
    InMemoryGovernanceIntegrityAuditExecutionQueueRepository,
)
from .deployment_governance_audit_worker import (
    GovernanceIntegrityAuditExecutionRepository,
    InMemoryGovernanceIntegrityAuditExecutionRepository,
)
from .deployment_governance_audit_retry import (
    GovernanceIntegrityRetryRepository,
    InMemoryGovernanceIntegrityRetryRepository,
)
from .deployment_governance_dead_letter_queue import (
    GovernanceIntegrityDeadLetterRepository,
    InMemoryGovernanceIntegrityDeadLetterRepository,
)
from .deployment_governance_failure_policy import (
    GovernanceIntegrityFailurePolicyRepository,
    InMemoryGovernanceIntegrityFailurePolicyRepository,
)
from .deployment_governance_notifications import (
    GovernanceIntegrityNotificationRepository,
    InMemoryGovernanceIntegrityNotificationRepository,
)
from .deployment_governance_notification_channels import (
    GovernanceIntegrityNotificationChannelRepository,
    InMemoryGovernanceIntegrityNotificationChannelRepository,
)
from .deployment_governance_notification_dispatcher import (
    GovernanceIntegrityNotificationDispatchRepository,
    InMemoryGovernanceIntegrityNotificationDispatchRepository,
)
from .deployment_governance_delivery_history import (
    GovernanceIntegrityDeliveryHistoryRepository,
    InMemoryGovernanceIntegrityDeliveryHistoryRepository,
)
from .deployment_governance_notification_preferences import (
    GovernanceIntegrityNotificationPreferenceRepository,
    InMemoryGovernanceIntegrityNotificationPreferenceRepository,
)
from .deployment_governance_delivery_policies import (
    GovernanceIntegrityDeliveryPolicyRepository,
    InMemoryGovernanceIntegrityDeliveryPolicyRepository,
)
from .deployment_governance_provider_configuration import (
    GovernanceIntegrityProviderConfigurationRepository,
    InMemoryGovernanceIntegrityProviderConfigurationRepository,
)
from .deployment_governance_provider_secrets import (
    GovernanceIntegrityProviderSecretsRepository,
    InMemoryGovernanceIntegrityProviderSecretsRepository,
)
from .deployment_governance_delivery_scheduler import (
    GovernanceIntegrityDeliveryScheduleRepository,
    InMemoryGovernanceIntegrityDeliveryScheduleRepository,
)
from .deployment_governance_audit_history import (
    GovernanceIntegrityAuditHistoryRepository,
    InMemoryGovernanceIntegrityAuditHistoryRepository,
)
from .deployment_governance_audit_retention import (
    GovernanceIntegrityAuditAutomaticRetentionConfig,
)
from .deployment_governance_metrics import (
    GovernanceIntegrityMetricsService,
)
from .deployment_governance_metrics_alerts import (
    GovernanceIntegrityMetricsAlertService,
)
from .deployment_governance_logging import (
    GovernanceIntegrityLogger,
)
from .deployment_governance_metrics_repository import (
    GovernanceIntegrityMetricsRepository,
    InMemoryGovernanceIntegrityMetricsRepository,
)
from .deployment_governance_metrics_history import (
    GovernanceIntegrityMetricsHistoryRepository,
    InMemoryGovernanceIntegrityMetricsHistoryRepository,
)
from .deployment_governance_log_repository import (
    GovernanceLogRepository,
    InMemoryGovernanceLogRepository,
)
from .deployment_governance_log_rotation import (
    GovernanceLogRotationService,
)
from .deployment_governance_log_redaction import (
    GovernanceLogRedactionService,
)
from .deployment_governance_log_context import (
    GovernanceLogContextService,
)
from .deployment_governance_log_correlation import (
    GovernanceCorrelationService,
)
from .deployment_governance_log_sampling import (
    GovernanceLogSamplingService,
)
from .deployment_governance_log_batcher import (
    GovernanceLogBatcher,
)
from .deployment_governance_log_config import (
    GovernanceLogConfig,
    GovernanceLogConfigService,
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
from .deployment_governance_integrity_audit import (
    DeploymentGovernanceIntegrityAuditService,
    DeploymentGovernanceTraceIntegrityAuditSource,
)
from .deployment_governance_trace_engine import (
    DeploymentGovernanceTraceEngine,
)
from .deployment_governance_trace_registry import (
    DeploymentGovernanceTraceRegistry,
)
from .deployment_governance_trace_repository import (
    DeploymentGovernanceTraceRepository,
)
from .in_memory_deployment_governance_trace_repository import (
    InMemoryDeploymentGovernanceTraceRepository,
)
from .sqlite_deployment_governance_audit_bookmarks import (
    SQLiteGovernanceIntegrityAuditBookmarkRepository,
)
from .sqlite_deployment_governance_audit_labels import (
    SQLiteGovernanceIntegrityAuditLabelRepository,
)
from .sqlite_deployment_governance_audit_saved_queries import (
    SQLiteGovernanceIntegritySavedAuditQueryRepository,
)
from .sqlite_deployment_governance_audit_collections import (
    SQLiteGovernanceIntegrityAuditCollectionRepository,
)
from .sqlite_deployment_governance_audit_report_templates import (
    SQLiteGovernanceIntegrityAuditReportTemplateRepository,
)
from .sqlite_deployment_governance_audit_report_schedule import (
    SQLiteGovernanceIntegrityAuditReportScheduleRepository,
)
from .sqlite_deployment_governance_failure_policy import (
    SQLiteGovernanceIntegrityFailurePolicyRepository,
)
from .sqlite_deployment_governance_notifications import (
    SQLiteGovernanceIntegrityNotificationRepository,
)
from .sqlite_deployment_governance_notification_channels import (
    SQLiteGovernanceIntegrityNotificationChannelRepository,
)
from .sqlite_deployment_governance_notification_dispatcher import (
    SQLiteGovernanceIntegrityNotificationDispatchRepository,
)
from .sqlite_deployment_governance_delivery_history import (
    SQLiteGovernanceIntegrityDeliveryHistoryRepository,
)
from .sqlite_deployment_governance_notification_preferences import (
    SQLiteGovernanceIntegrityNotificationPreferenceRepository,
)
from .sqlite_deployment_governance_delivery_policies import (
    SQLiteGovernanceIntegrityDeliveryPolicyRepository,
)
from .sqlite_deployment_governance_provider_configuration import (
    SQLiteGovernanceIntegrityProviderConfigurationRepository,
)
from .sqlite_deployment_governance_provider_secrets import (
    SQLiteGovernanceIntegrityProviderSecretsRepository,
)
from .sqlite_deployment_governance_delivery_scheduler import (
    SQLiteGovernanceIntegrityDeliveryScheduleRepository,
)
from .deployment_governance_metrics_repository import (
    SQLiteGovernanceIntegrityMetricsRepository,
)
from .deployment_governance_metrics_history import (
    SQLiteGovernanceIntegrityMetricsHistoryRepository,
)
from .deployment_governance_log_repository import (
    SQLiteGovernanceLogRepository,
)
from .sqlite_deployment_governance_audit_history import (
    SQLiteGovernanceIntegrityAuditHistoryRepository,
)
from .sqlite_deployment_governance_trace_repository import (
    SQLiteDeploymentGovernanceTraceRepository,
)

if TYPE_CHECKING:
    from .deployment_governance_audit_history_service import (
        GovernanceIntegrityAuditHistoryService,
    )
    from .deployment_governance_audit_recording import (
        GovernanceIntegrityAuditRecordingService,
    )
    from .deployment_governance_audit_regression import (
        GovernanceIntegrityRegressionService,
    )
    from .deployment_governance_audit_trends import (
        GovernanceIntegrityAuditTrendService,
    )
    from .deployment_governance_audit_retention import (
        GovernanceIntegrityAuditRetentionService,
    )
    from .deployment_governance_audit_export import (
        GovernanceIntegrityAuditExportService,
    )
    from .deployment_governance_audit_statistics import (
        GovernanceIntegrityAuditStatisticsService,
    )
    from .deployment_governance_audit_replay import (
        GovernanceIntegrityAuditReplayService,
    )
    from .deployment_governance_audit_replay_diff import (
        GovernanceIntegrityAuditReplayDiffService,
    )
    from .deployment_governance_audit_timeline import (
        GovernanceIntegrityAuditTimelineService,
    )
    from .deployment_governance_audit_session import (
        GovernanceIntegrityAuditSessionService,
    )
    from .deployment_governance_audit_bookmarks import (
        GovernanceIntegrityAuditBookmarkService,
    )
    from .deployment_governance_audit_labels import (
        GovernanceIntegrityAuditLabelService,
    )
    from .deployment_governance_audit_search import (
        GovernanceIntegrityAuditSearchService,
    )
    from .deployment_governance_audit_saved_queries import (
        GovernanceIntegritySavedAuditQueryService,
    )
    from .deployment_governance_audit_collections import (
        GovernanceIntegrityAuditCollectionService,
    )
    from .deployment_governance_audit_reports import (
        GovernanceIntegrityAuditReportService,
    )
    from .deployment_governance_audit_report_templates import (
        GovernanceIntegrityAuditReportTemplateService,
    )
    from .deployment_governance_audit_report_schedule import (
        GovernanceIntegrityAuditReportScheduleService,
    )
    from .deployment_governance_audit_execution_queue import (
        GovernanceIntegrityAuditExecutionQueueService,
    )
    from .deployment_governance_audit_worker import (
        GovernanceIntegrityAuditWorker,
    )
    from .deployment_governance_audit_retry import (
        GovernanceIntegrityAuditRetryService,
    )
    from .deployment_governance_dead_letter_queue import (
        GovernanceIntegrityDeadLetterService,
    )
    from .deployment_governance_failure_policy import (
        GovernanceIntegrityFailurePolicyService,
    )
    from .deployment_governance_execution_metrics import (
        GovernanceIntegrityExecutionMetricsService,
    )
    from .deployment_governance_execution_alerts import (
        GovernanceIntegrityExecutionAlertService,
    )
    from .deployment_governance_notifications import (
        GovernanceIntegrityNotificationService,
    )
    from .deployment_governance_notification_channels import (
        GovernanceIntegrityNotificationChannelService,
    )
    from .deployment_governance_notification_dispatcher import (
        GovernanceIntegrityNotificationDispatcher,
    )
    from .deployment_governance_delivery_engine import (
        GovernanceIntegrityDeliveryEngine,
    )
    from .deployment_governance_provider_registry import (
        GovernanceIntegrityProviderRegistry,
    )
    from .deployment_governance_provider_health import (
        GovernanceIntegrityProviderHealthService,
    )
    from .deployment_governance_provider_configuration import (
        GovernanceIntegrityProviderConfigurationService,
    )
    from .deployment_governance_provider_secrets import (
        GovernanceIntegrityProviderSecretsService,
    )
    from .deployment_governance_provider_authentication import (
        GovernanceIntegrityProviderAuthenticationService,
    )
    from .deployment_governance_provider_requests import (
        GovernanceIntegrityProviderRequestService,
    )
    from .deployment_governance_provider_responses import (
        GovernanceIntegrityProviderResponseService,
    )
    from .deployment_governance_retry_orchestrator import (
        GovernanceIntegrityRetryOrchestrator,
    )
    from .deployment_governance_delivery_scheduler import (
        GovernanceIntegrityDeliveryScheduler,
    )
    from .deployment_governance_delivery_worker import (
        GovernanceIntegrityDeliveryWorker,
    )
    from .deployment_governance_delivery_history import (
        GovernanceIntegrityDeliveryHistoryService,
    )
    from .deployment_governance_notification_preferences import (
        GovernanceIntegrityNotificationPreferenceService,
    )
    from .deployment_governance_delivery_policies import (
        GovernanceIntegrityDeliveryPolicyService,
    )
    from .deployment_governance_check import (
        GovernanceIntegrityCheckService,
    )
    from .deployment_governance_persistence_diagnostics import (
        DeploymentGovernancePersistenceDiagnosticsService,
    )
    from .deployment_governance_scheduler_bootstrap import (
        GovernanceSchedulerBootstrap,
    )
    from .deployment_governance_rollout_manager import (
        DeploymentRolloutManager,
    )
    from .deployment_governance_version_registry import (
        DeploymentVersionRegistry,
    )
    from .deployment_governance_blue_green import (
        BlueGreenDeploymentEngine,
    )
    from .deployment_governance_canary import CanaryDeploymentEngine
    from .deployment_governance_rolling import RollingDeploymentEngine
    from .deployment_governance_progressive_delivery import (
        ProgressiveDeliveryEngine,
    )
    from .deployment_governance_traffic_router import (
        DeploymentTrafficRouter,
    )
    from .deployment_governance_rollback import DeploymentRollbackEngine
    from .deployment_governance_rollout_health import (
        DeploymentRolloutHealthEngine,
    )
    from .deployment_governance_rollout_analytics import (
        DeploymentRolloutAnalytics,
    )
    from .deployment_governance_rollout_policy import (
        DeploymentRolloutPolicyEngine,
    )
    from .deployment_governance_rollout_dashboard import (
        DeploymentRolloutDashboard,
    )
    from .deployment_governance_rbac import DeploymentRBACEngine
    from .deployment_governance_authentication import (
        DeploymentAuthenticationManager,
    )


DEFAULT_GOVERNANCE_DATABASE_PATH: Final[
    Path
] = Path(
    "data/notebook2api.db"
)

DEFAULT_METRICS_HISTORY_RETENTION: Final[int] = 500


class DeploymentGovernancePersistenceBackend(
    str,
    Enum,
):
    """
    Supported persistence backends for deployment governance traces.
    """

    MEMORY = "memory"
    SQLITE = "sqlite"

    @classmethod
    def parse(
        cls,
        value: "str | DeploymentGovernancePersistenceBackend",
    ) -> "DeploymentGovernancePersistenceBackend":
        """
        Normalize a backend value into the canonical enum.
        """

        if isinstance(
            value,
            cls,
        ):
            return value

        normalized = (
            str(
                value
            )
            .strip()
            .lower()
        )

        try:
            return cls(
                normalized
            )

        except ValueError as exc:
            supported = ", ".join(
                backend.value
                for backend in cls
            )

            raise ValueError(
                "unsupported deployment governance "
                f"persistence backend '{value}'; "
                f"expected one of: {supported}"
            ) from exc


@dataclass(frozen=True)
class DeploymentGovernancePersistenceConfig:
    """
    Configuration for deployment governance persistence composition.

    The configuration describes which repository backend should be used and,
    for durable SQLite mode, how the database should be configured. Field
    names here are the persistence bootstrap's own vocabulary; they are
    translated into the exact SQLiteDatabaseConfig fields
    (journal_mode, enable_foreign_keys) when building a runtime.
    """

    backend: DeploymentGovernancePersistenceBackend = (
        DeploymentGovernancePersistenceBackend.MEMORY
    )

    database_path: Path = (
        DEFAULT_GOVERNANCE_DATABASE_PATH
    )

    sqlite_timeout_seconds: float = 30.0

    sqlite_enable_wal: bool = True

    sqlite_enforce_foreign_keys: bool = True

    initialize_schema: bool = True

    def __post_init__(
        self,
    ) -> None:
        object.__setattr__(
            self,
            "backend",
            DeploymentGovernancePersistenceBackend.parse(
                self.backend
            ),
        )

        object.__setattr__(
            self,
            "database_path",
            Path(
                self.database_path
            ),
        )

        if self.sqlite_timeout_seconds <= 0:
            raise ValueError(
                "sqlite_timeout_seconds must be greater than zero"
            )

    @classmethod
    def memory(
        cls,
    ) -> "DeploymentGovernancePersistenceConfig":
        """
        Create an ephemeral in-memory governance persistence configuration.
        """

        return cls(
            backend=(
                DeploymentGovernancePersistenceBackend.MEMORY
            )
        )

    @classmethod
    def sqlite(
        cls,
        database_path: str | Path = (
            DEFAULT_GOVERNANCE_DATABASE_PATH
        ),
        *,
        timeout_seconds: float = 30.0,
        enable_wal: bool = True,
        enforce_foreign_keys: bool = True,
        initialize_schema: bool = True,
    ) -> "DeploymentGovernancePersistenceConfig":
        """
        Create a durable SQLite governance persistence configuration.
        """

        return cls(
            backend=(
                DeploymentGovernancePersistenceBackend.SQLITE
            ),
            database_path=Path(
                database_path
            ),
            sqlite_timeout_seconds=timeout_seconds,
            sqlite_enable_wal=enable_wal,
            sqlite_enforce_foreign_keys=(
                enforce_foreign_keys
            ),
            initialize_schema=initialize_schema,
        )


@dataclass(frozen=True)
class DeploymentGovernancePersistenceRuntime:
    """
    Fully composed deployment governance persistence runtime.

    The runtime exposes the storage-neutral repository and registry while
    retaining the optional SQLite database handle for infrastructure-level
    lifecycle and diagnostics.
    """

    config: DeploymentGovernancePersistenceConfig

    repository: DeploymentGovernanceTraceRepository

    registry: DeploymentGovernanceTraceRegistry

    audit_history_repository: GovernanceIntegrityAuditHistoryRepository

    bookmark_repository: GovernanceIntegrityAuditBookmarkRepository

    label_repository: GovernanceIntegrityAuditLabelRepository

    saved_query_repository: GovernanceIntegritySavedAuditQueryRepository

    collection_repository: GovernanceIntegrityAuditCollectionRepository

    report_template_repository: (
        GovernanceIntegrityAuditReportTemplateRepository
    )

    report_schedule_repository: (
        GovernanceIntegrityAuditReportScheduleRepository
    )

    execution_queue_repository: (
        GovernanceIntegrityAuditExecutionQueueRepository
    )

    execution_repository: (
        GovernanceIntegrityAuditExecutionRepository
    )

    retry_repository: (
        GovernanceIntegrityRetryRepository
    )

    dead_letter_repository: (
        GovernanceIntegrityDeadLetterRepository
    )

    failure_policy_repository: (
        GovernanceIntegrityFailurePolicyRepository
    )

    notification_repository: (
        GovernanceIntegrityNotificationRepository
    )

    notification_channel_repository: (
        GovernanceIntegrityNotificationChannelRepository
    )

    notification_dispatch_repository: (
        GovernanceIntegrityNotificationDispatchRepository
    )

    delivery_history_repository: (
        GovernanceIntegrityDeliveryHistoryRepository
    )

    notification_preference_repository: (
        GovernanceIntegrityNotificationPreferenceRepository
    )

    delivery_policy_repository: (
        GovernanceIntegrityDeliveryPolicyRepository
    )

    provider_configuration_repository: (
        GovernanceIntegrityProviderConfigurationRepository
    )

    provider_secrets_repository: (
        GovernanceIntegrityProviderSecretsRepository
    )

    delivery_schedule_repository: (
        GovernanceIntegrityDeliveryScheduleRepository
    )

    database: SQLiteDatabase | None = None

    automatic_audit_retention: (
        GovernanceIntegrityAuditAutomaticRetentionConfig
    ) = field(
        default_factory=(
            GovernanceIntegrityAuditAutomaticRetentionConfig.disabled
        )
    )

    metrics_repository: GovernanceIntegrityMetricsRepository = field(
        default_factory=InMemoryGovernanceIntegrityMetricsRepository
    )

    metrics_history_repository: (
        GovernanceIntegrityMetricsHistoryRepository
    ) = field(
        default_factory=(
            InMemoryGovernanceIntegrityMetricsHistoryRepository
        )
    )

    metrics_service: GovernanceIntegrityMetricsService = field(
        default_factory=GovernanceIntegrityMetricsService
    )

    metrics_alert_service: GovernanceIntegrityMetricsAlertService = field(
        default_factory=GovernanceIntegrityMetricsAlertService
    )

    logger: GovernanceIntegrityLogger = field(
        default_factory=GovernanceIntegrityLogger
    )

    log_repository: GovernanceLogRepository = field(
        default_factory=InMemoryGovernanceLogRepository
    )

    log_rotation_service: GovernanceLogRotationService = field(
        init=False
    )

    redaction_service: GovernanceLogRedactionService = field(
        default_factory=GovernanceLogRedactionService
    )

    context_service: GovernanceLogContextService = field(
        default_factory=GovernanceLogContextService
    )

    correlation_service: GovernanceCorrelationService = field(
        default_factory=GovernanceCorrelationService
    )

    sampling_service: GovernanceLogSamplingService = field(
        default_factory=GovernanceLogSamplingService
    )

    log_config_service: GovernanceLogConfigService = field(
        default_factory=GovernanceLogConfigService
    )

    batcher: GovernanceLogBatcher = field(init=False)

    def __post_init__(self) -> None:
        # metrics_service and logger are each constructed
        # independently by their own default_factory above, so the
        # cross-wiring is attached here rather than threaded through
        # at construction time. log_rotation_service depends on
        # log_repository, so it cannot be a plain default_factory
        # field either: it is built here instead.
        self.metrics_service.set_logger(self.logger)

        self.logger.set_repository(self.log_repository)

        # log_config_service is the source of truth for whether
        # redaction/sampling start out attached, the logger's
        # minimum level, and the batcher's initial size/interval:
        # loaded once here, up front, rather than each dependent
        # service applying its own separate default.
        log_config = self.log_config_service.load()

        self.logger.set_minimum_level(log_config.minimum_level)

        self.logger.set_redaction_service(
            self.redaction_service
            if log_config.enable_redaction
            else None
        )

        self.logger.set_context_service(self.context_service)

        self.logger.set_correlation_service(self.correlation_service)

        self.logger.set_sampling_service(
            self.sampling_service
            if log_config.enable_sampling
            else None
        )

        object.__setattr__(
            self,
            "log_rotation_service",
            GovernanceLogRotationService(self.log_repository),
        )

        self.log_repository.set_rotation_service(
            self.log_rotation_service
        )

        object.__setattr__(
            self,
            "batcher",
            GovernanceLogBatcher(
                self.log_repository,
                batch_size=log_config.batch_size,
                flush_interval_seconds=(
                    log_config.flush_interval_seconds
                ),
            ),
        )

        # Deliberately NOT attached to the logger here, unlike every
        # other service above: batching changes when an entry
        # actually becomes durable (only once its batch is flushed,
        # not immediately on the logger.info()/... call that
        # produced it), which would silently change the behavior
        # every existing caller of this runtime already depends on.
        # Attaching it is an explicit opt-in:
        # runtime.build_integrity_logger().set_batcher(
        #     runtime.build_integrity_log_batcher()
        # )

    def reload_log_config(self) -> GovernanceLogConfig:
        """
        Re-read governance logging configuration from its source and
        apply it to the logger (minimum level, and whether sampling
        and redaction are active) and the batcher (batch size, flush
        interval), without restarting anything.

        Returns the newly loaded config.
        """

        config = self.log_config_service.reload()

        self.logger.set_minimum_level(config.minimum_level)

        self.logger.set_redaction_service(
            self.redaction_service
            if config.enable_redaction
            else None
        )

        self.logger.set_sampling_service(
            self.sampling_service
            if config.enable_sampling
            else None
        )

        self.batcher.reconfigure(
            batch_size=config.batch_size,
            flush_interval_seconds=config.flush_interval_seconds,
        )

        return config

    @property
    def durable(
        self,
    ) -> bool:
        """
        Return whether the configured persistence backend survives restarts.
        """

        return (
            self.config.backend
            is DeploymentGovernancePersistenceBackend.SQLITE
        )

    @property
    def backend(
        self,
    ) -> DeploymentGovernancePersistenceBackend:
        """
        Return the active persistence backend.
        """

        return self.config.backend

    @property
    def supports_integrity_audit(
        self,
    ) -> bool:
        """
        Return whether the active repository exposes integrity audit
        candidates.
        """

        return isinstance(
            self.repository,
            DeploymentGovernanceTraceIntegrityAuditSource,
        )

    def build_integrity_audit_service(
        self,
    ) -> DeploymentGovernanceIntegrityAuditService:
        """
        Build an integrity audit service for the active repository.
        """

        if not isinstance(
            self.repository,
            DeploymentGovernanceTraceIntegrityAuditSource,
        ):
            raise RuntimeError(
                "the active deployment governance persistence "
                "backend does not support integrity auditing"
            )

        return DeploymentGovernanceIntegrityAuditService(
            self.repository
        )

    def build_integrity_audit_recording_service(
        self,
    ) -> "GovernanceIntegrityAuditRecordingService":
        """
        Build an integrity audit service that records completed audit
        history alongside the active persistence backend's trace
        repository.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_audit_recording import (
            GovernanceIntegrityAuditRecordMapper,
            GovernanceIntegrityAuditRecordingService,
        )

        return GovernanceIntegrityAuditRecordingService(
            audit_executor=self.build_integrity_audit_service(),
            history_repository=self.audit_history_repository,
            record_mapper=GovernanceIntegrityAuditRecordMapper(
                backend=self.backend.value
            ),
            retention_service=(
                self.build_integrity_audit_retention_service()
            ),
            automatic_retention=self.automatic_audit_retention,
        )

    def build_integrity_audit_history_service(
        self,
    ) -> "GovernanceIntegrityAuditHistoryService":
        """
        Build the read-only integrity audit-history query service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_audit_history_service import (
            GovernanceIntegrityAuditHistoryService,
        )

        return GovernanceIntegrityAuditHistoryService(
            self.audit_history_repository
        )

    def build_integrity_audit_trend_service(
        self,
    ) -> "GovernanceIntegrityAuditTrendService":
        """
        Build the governance integrity audit trend service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_audit_trends import (
            GovernanceIntegrityAuditTrendService,
        )

        return GovernanceIntegrityAuditTrendService(
            self.audit_history_repository
        )

    def build_integrity_regression_service(
        self,
    ) -> "GovernanceIntegrityRegressionService":
        """
        Build the governance integrity regression detection service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_audit_regression import (
            GovernanceIntegrityRegressionService,
        )

        return GovernanceIntegrityRegressionService(
            self.audit_history_repository
        )

    def build_integrity_check_service(
        self,
    ) -> "GovernanceIntegrityCheckService":
        """
        Build the CI-oriented governance integrity check service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_check import (
            GovernanceIntegrityCheckService,
        )

        return GovernanceIntegrityCheckService(
            recording_service=(
                self.build_integrity_audit_recording_service()
            ),
            regression_service=(
                self.build_integrity_regression_service()
            ),
        )

    def build_integrity_audit_retention_service(
        self,
    ) -> "GovernanceIntegrityAuditRetentionService":
        """
        Build the audit-history retention service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_audit_retention import (
            GovernanceIntegrityAuditRetentionService,
        )

        return GovernanceIntegrityAuditRetentionService(
            self.audit_history_repository
        )

    def build_integrity_audit_export_service(
        self,
    ) -> "GovernanceIntegrityAuditExportService":
        """
        Build the governance audit evidence export service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_audit_export import (
            GovernanceIntegrityAuditExportService,
        )

        return GovernanceIntegrityAuditExportService(
            repository=self.audit_history_repository
        )

    def build_integrity_audit_statistics_service(
        self,
    ) -> "GovernanceIntegrityAuditStatisticsService":
        """
        Build the audit-history statistics service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_audit_statistics import (
            GovernanceIntegrityAuditStatisticsService,
        )

        return GovernanceIntegrityAuditStatisticsService(
            self.audit_history_repository
        )

    def build_integrity_audit_replay_service(
        self,
    ) -> "GovernanceIntegrityAuditReplayService":
        """
        Build the governance audit replay service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_audit_replay import (
            GovernanceIntegrityAuditReplayService,
        )

        return GovernanceIntegrityAuditReplayService(
            self.audit_history_repository
        )

    def build_integrity_audit_replay_diff_service(
        self,
    ) -> "GovernanceIntegrityAuditReplayDiffService":
        """
        Build the governance audit replay diff service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_audit_replay_diff import (
            GovernanceIntegrityAuditReplayDiffService,
        )

        return GovernanceIntegrityAuditReplayDiffService(
            self.build_integrity_audit_replay_service()
        )

    def build_integrity_audit_timeline_service(
        self,
    ) -> "GovernanceIntegrityAuditTimelineService":
        """
        Build the governance audit timeline service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_audit_timeline import (
            GovernanceIntegrityAuditTimelineService,
        )

        return GovernanceIntegrityAuditTimelineService(
            self.audit_history_repository
        )

    def build_integrity_audit_session_service(
        self,
    ) -> "GovernanceIntegrityAuditSessionService":
        """
        Build the governance audit session reconstruction service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_audit_session import (
            GovernanceIntegrityAuditSessionService,
        )

        return GovernanceIntegrityAuditSessionService(
            self.audit_history_repository
        )

    def build_integrity_audit_bookmark_service(
        self,
    ) -> "GovernanceIntegrityAuditBookmarkService":
        """
        Build the governance audit bookmark service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_audit_bookmarks import (
            GovernanceIntegrityAuditBookmarkService,
        )

        return GovernanceIntegrityAuditBookmarkService(
            self.bookmark_repository,
            self.audit_history_repository,
        )

    def build_integrity_audit_label_service(
        self,
    ) -> "GovernanceIntegrityAuditLabelService":
        """
        Build the governance audit label service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_audit_labels import (
            GovernanceIntegrityAuditLabelService,
        )

        return GovernanceIntegrityAuditLabelService(
            self.label_repository,
            self.audit_history_repository,
        )

    def build_integrity_audit_search_service(
        self,
    ) -> "GovernanceIntegrityAuditSearchService":
        """
        Build the governance audit search service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_audit_search import (
            GovernanceIntegrityAuditSearchService,
        )

        return GovernanceIntegrityAuditSearchService(
            self.audit_history_repository,
            self.label_repository,
            self.bookmark_repository,
        )

    def build_integrity_saved_audit_query_service(
        self,
    ) -> "GovernanceIntegritySavedAuditQueryService":
        """
        Build the saved governance audit query service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_audit_saved_queries import (
            GovernanceIntegritySavedAuditQueryService,
        )

        return GovernanceIntegritySavedAuditQueryService(
            self.saved_query_repository,
            self.build_integrity_audit_search_service(),
        )

    def build_integrity_audit_collection_service(
        self,
    ) -> "GovernanceIntegrityAuditCollectionService":
        """
        Build the governance audit collection service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_audit_collections import (
            GovernanceIntegrityAuditCollectionService,
        )

        return GovernanceIntegrityAuditCollectionService(
            self.collection_repository,
            self.audit_history_repository,
        )

    def build_integrity_audit_report_service(
        self,
    ) -> "GovernanceIntegrityAuditReportService":
        """
        Build the governance audit report service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_audit_reports import (
            GovernanceIntegrityAuditReportService,
        )

        return GovernanceIntegrityAuditReportService(
            self.audit_history_repository,
            self.collection_repository,
            self.build_integrity_audit_statistics_service(),
        )

    def build_integrity_audit_report_template_service(
        self,
    ) -> "GovernanceIntegrityAuditReportTemplateService":
        """
        Build the governance audit report template service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_audit_report_templates import (
            GovernanceIntegrityAuditReportTemplateService,
        )

        return GovernanceIntegrityAuditReportTemplateService(
            self.report_template_repository,
            self.build_integrity_audit_report_service(),
            self.build_integrity_audit_collection_service(),
            self.build_integrity_saved_audit_query_service(),
        )

    def build_integrity_audit_report_schedule_service(
        self,
    ) -> "GovernanceIntegrityAuditReportScheduleService":
        """
        Build the governance audit report schedule service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_audit_report_schedule import (
            GovernanceIntegrityAuditReportScheduleService,
        )

        return GovernanceIntegrityAuditReportScheduleService(
            self.report_schedule_repository,
            self.build_integrity_audit_report_template_service(),
        )

    def build_integrity_audit_execution_queue_service(
        self,
    ) -> "GovernanceIntegrityAuditExecutionQueueService":
        """
        Build the governance audit execution queue service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_audit_execution_queue import (
            GovernanceIntegrityAuditExecutionQueueService,
        )

        return GovernanceIntegrityAuditExecutionQueueService(
            self.execution_queue_repository,
            self.build_integrity_audit_report_schedule_service(),
        )

    def build_integrity_audit_worker(
        self,
    ) -> "GovernanceIntegrityAuditWorker":
        """
        Build the governance audit execution worker.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_audit_worker import (
            GovernanceIntegrityAuditWorker,
        )

        return GovernanceIntegrityAuditWorker(
            self.build_integrity_audit_execution_queue_service(),
            self.build_integrity_audit_report_template_service(),
            self.execution_repository,
        )

    def build_integrity_audit_retry_service(
        self,
    ) -> "GovernanceIntegrityAuditRetryService":
        """
        Build the governance audit retry service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_audit_retry import (
            GovernanceIntegrityAuditRetryService,
        )

        return GovernanceIntegrityAuditRetryService(
            self.build_integrity_audit_execution_queue_service(),
            self.execution_repository,
            self.retry_repository,
        )

    def build_integrity_dead_letter_service(
        self,
    ) -> "GovernanceIntegrityDeadLetterService":
        """
        Build the governance audit dead letter queue service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_dead_letter_queue import (
            GovernanceIntegrityDeadLetterService,
        )

        return GovernanceIntegrityDeadLetterService(
            self.execution_repository,
            self.dead_letter_repository,
        )

    def build_integrity_failure_policy_service(
        self,
    ) -> "GovernanceIntegrityFailurePolicyService":
        """
        Build the governance audit failure policy service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_failure_policy import (
            GovernanceIntegrityFailurePolicyService,
        )

        return GovernanceIntegrityFailurePolicyService(
            self.failure_policy_repository,
        )

    def build_integrity_execution_metrics_service(
        self,
    ) -> "GovernanceIntegrityExecutionMetricsService":
        """
        Build the governance audit execution metrics service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_execution_metrics import (
            GovernanceIntegrityExecutionMetricsService,
        )

        return GovernanceIntegrityExecutionMetricsService(
            self.execution_repository,
        )

    def build_integrity_execution_alert_service(
        self,
    ) -> "GovernanceIntegrityExecutionAlertService":
        """
        Build the governance audit execution alert service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_execution_alerts import (
            GovernanceIntegrityExecutionAlertService,
        )

        return GovernanceIntegrityExecutionAlertService(
            self.build_integrity_execution_metrics_service(),
        )

    def build_integrity_notification_service(
        self,
    ) -> "GovernanceIntegrityNotificationService":
        """
        Build the governance audit notification pipeline service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_notifications import (
            GovernanceIntegrityNotificationService,
        )

        return GovernanceIntegrityNotificationService(
            self.build_integrity_execution_alert_service(),
            self.notification_repository,
        )

    def build_integrity_notification_channel_service(
        self,
    ) -> "GovernanceIntegrityNotificationChannelService":
        """
        Build the governance audit notification channel service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_notification_channels import (
            GovernanceIntegrityNotificationChannelService,
        )

        return GovernanceIntegrityNotificationChannelService(
            self.notification_channel_repository,
        )

    def build_integrity_notification_preference_service(
        self,
    ) -> "GovernanceIntegrityNotificationPreferenceService":
        """
        Build the governance audit notification preference service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_notification_preferences import (
            GovernanceIntegrityNotificationPreferenceService,
        )

        return GovernanceIntegrityNotificationPreferenceService(
            self.notification_preference_repository,
            self.build_integrity_notification_channel_service(),
        )

    def build_integrity_notification_dispatcher(
        self,
    ) -> "GovernanceIntegrityNotificationDispatcher":
        """
        Build the governance audit notification dispatcher.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_notification_dispatcher import (
            GovernanceIntegrityNotificationDispatcher,
        )

        return GovernanceIntegrityNotificationDispatcher(
            self.notification_repository,
            self.build_integrity_notification_preference_service(),
            self.notification_dispatch_repository,
        )

    def build_integrity_delivery_policy_service(
        self,
    ) -> "GovernanceIntegrityDeliveryPolicyService":
        """
        Build the governance audit delivery policy service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_delivery_policies import (
            GovernanceIntegrityDeliveryPolicyService,
        )

        return GovernanceIntegrityDeliveryPolicyService(
            self.delivery_policy_repository,
            self.build_integrity_notification_channel_service(),
        )

    def build_integrity_provider_registry(
        self,
    ) -> "GovernanceIntegrityProviderRegistry":
        """
        Build a governance audit delivery provider registry with every
        built-in provider registered.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_delivery_engine import (
            EmailProvider,
            SlackProvider,
            WebhookProvider,
        )
        from .deployment_governance_notification_channels import (
            GovernanceIntegrityNotificationChannelType,
        )
        from .deployment_governance_provider_registry import (
            GovernanceIntegrityProviderRegistry,
        )

        registry = GovernanceIntegrityProviderRegistry()

        registry.register(
            GovernanceIntegrityNotificationChannelType.EMAIL,
            EmailProvider(),
        )

        registry.register(
            GovernanceIntegrityNotificationChannelType.SLACK,
            SlackProvider(),
        )

        registry.register(
            GovernanceIntegrityNotificationChannelType.WEBHOOK,
            WebhookProvider(),
        )

        return registry

    def build_integrity_provider_health_service(
        self,
    ) -> "GovernanceIntegrityProviderHealthService":
        """
        Build the governance audit delivery provider health service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_provider_health import (
            GovernanceIntegrityProviderHealthService,
        )

        return GovernanceIntegrityProviderHealthService(
            self.build_integrity_provider_registry()
        )

    def build_integrity_health_service(
        self,
    ) -> "GovernanceHealthService":
        """
        Build a GovernanceHealthService with checks registered for
        the persistence-backed components a stateless request can
        observe: the provider registry and the shared metrics
        service.

        Unlike GovernanceIntegrityDeliveryRuntime.build_health_service,
        this has no long-lived delivery runtime or bootstraps to
        check: those are only wired together for a running worker
        process, not for a persistence runtime built fresh per
        request.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_health import GovernanceHealthService
        from .deployment_governance_provider_health import (
            GovernanceIntegrityProviderHealthStatus,
        )

        service = GovernanceHealthService()

        def _check_provider_registry():
            statuses = (
                self.build_integrity_provider_health_service()
                .check_all()
            )

            unhealthy = [
                status.channel_type.value
                for status in statuses
                if status.status
                is not GovernanceIntegrityProviderHealthStatus.HEALTHY
            ]

            if unhealthy:
                return False, (
                    "unhealthy providers: " + ", ".join(sorted(unhealthy))
                )

            return True

        def _check_metrics_service():
            self.build_integrity_metrics_service().snapshot()

            return True

        service.register("provider_registry", _check_provider_registry)
        service.register("metrics_service", _check_metrics_service)

        return service

    def build_integrity_readiness_service(
        self,
    ) -> "GovernanceReadinessService":
        """
        Build a GovernanceReadinessService with checks registered for
        the persistence-backed components a stateless request can
        observe: whether the provider registry is populated.

        Unlike GovernanceIntegrityDeliveryRuntime.build_readiness_service,
        this has no long-lived delivery worker, scheduler, or running
        state to check: those only exist for a running worker
        process, not for a persistence runtime built fresh per
        request.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_readiness import (
            GovernanceReadinessService,
        )

        service = GovernanceReadinessService()

        def _check_provider_registry():
            registrations = self.build_integrity_provider_registry().list()

            if not registrations:
                return False, "provider registry has no registered providers"

            return True

        service.register("provider_registry", _check_provider_registry)

        return service

    def build_integrity_liveness_service(
        self,
    ) -> "GovernanceLivenessService":
        """
        Return the process-wide governance liveness service.

        Unlike build_integrity_health_service/
        build_integrity_readiness_service, this does not construct a
        fresh instance: liveness answers "is this process alive",
        which is inherently process-wide state, not something a
        persistence runtime built fresh per request can meaningfully
        re-derive.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_liveness import get_liveness_service

        return get_liveness_service()

    def build_integrity_diagnostics_service(
        self,
    ) -> "GovernanceDiagnosticsService":
        """
        Build a GovernanceDiagnosticsService for the persistence-
        backed components a stateless request can observe: the
        provider registry.

        There is no live delivery runtime or scheduler in this
        context, so runtime_state always reports "not_running" and
        pending_dispatches is always 0 — unlike
        GovernanceIntegrityDeliveryRuntime.build_diagnostics_service,
        which reads live state from a running worker process.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_diagnostics import (
            GovernanceDiagnosticsService,
        )
        from .deployment_governance_readiness import (
            count_registered_providers,
        )

        def _registered_providers() -> int:
            count = count_registered_providers(
                self.build_integrity_provider_registry()
            )

            return count if count is not None else 0

        return GovernanceDiagnosticsService(
            runtime_state=lambda: "not_running",
            active_dispatches=lambda: 0,
            pending_dispatches=lambda: 0,
            registered_providers=_registered_providers,
        )

    def build_integrity_dependency_graph(
        self,
    ) -> "GovernanceDependencyGraph":
        """
        Build the governance runtime's component dependency graph.

        This is the same fixed, declarative graph
        bootstrap_governance_runtime() validates before startup; this
        accessor exists so a stateless request (e.g. the
        GET /governance/dependencies endpoint) can inspect it without
        needing a running delivery runtime.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_bootstrap import (
            build_governance_dependency_graph,
        )

        return build_governance_dependency_graph()

    def build_integrity_lifecycle_manager(
        self,
    ) -> "GovernanceLifecycleManager":
        """
        Return the process-wide governance lifecycle manager.

        Like build_integrity_liveness_service, this does not
        construct a fresh instance: lifecycle state (which components
        are currently started) is inherently process-wide, not
        something a persistence runtime built fresh per request can
        meaningfully re-derive.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_lifecycle import (
            get_lifecycle_manager,
        )

        return get_lifecycle_manager()

    def build_integrity_event_bus(
        self,
    ) -> "GovernanceEventBus":
        """
        Return the process-wide governance event bus.

        Like build_integrity_liveness_service, this does not
        construct a fresh instance: subscribers need to reach the
        same bus publishers (the lifecycle manager singleton, and any
        health/metrics service that opts in) publish to, which a
        persistence runtime built fresh per request cannot provide on
        its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_event_bus import get_event_bus

        return get_event_bus()

    def build_integrity_event_history(
        self,
    ) -> "GovernanceEventHistory":
        """
        Return the process-wide governance event history.

        Like build_integrity_event_bus, this does not construct a
        fresh instance: every event published on the process-wide
        event bus needs to reach the same history, which a
        persistence runtime built fresh per request cannot provide on
        its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_event_history import (
            get_event_history,
        )

        return get_event_history()

    def build_integrity_event_router(
        self,
    ) -> "GovernanceEventRouter":
        """
        Return the process-wide governance event router.

        Like build_integrity_event_bus, this does not construct a
        fresh instance: routes registered through the API need to be
        visible to whatever is consuming events off the process-wide
        event bus, which a persistence runtime built fresh per
        request cannot provide on its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_event_router import get_event_router

        return get_event_router()

    def build_governance_audit_trail_service(
        self,
    ) -> "GovernanceAuditService":
        """
        Return the process-wide governance audit trail service.

        Named distinctly from the build_integrity_audit_* family
        above (which all belong to the deployment trace integrity
        audit subsystem — a different, pre-existing concept): this is
        the tamper-evident hash-chained log of high-value governance
        actions (lifecycle transitions, route changes, and so on),
        introduced separately from that subsystem.

        Like build_integrity_event_bus, this does not construct a
        fresh instance: every action recorded by the lifecycle
        manager, event router, and event history singletons needs to
        reach the same audit trail, which a persistence runtime built
        fresh per request cannot provide on its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_audit import get_audit_service

        return get_audit_service()

    def build_governance_policy_engine(
        self,
    ) -> "GovernancePolicyEngine":
        """
        Return the process-wide governance policy engine.

        Like build_integrity_event_bus, this does not construct a
        fresh instance: policies registered through the API need to
        be enforced by whichever component (lifecycle manager, event
        router, audit service) evaluates against them, which a
        persistence runtime built fresh per request cannot provide on
        its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_policy import get_policy_engine

        return get_policy_engine()

    def build_governance_rule_engine(
        self,
    ) -> "GovernanceRuleEngine":
        """
        Return the process-wide governance rule engine.

        Like build_integrity_event_bus, this does not construct a
        fresh instance: rules registered through the API need to be
        visible to whatever evaluates them (the policy engine, or a
        direct API caller), which a persistence runtime built fresh
        per request cannot provide on its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_rules import get_rule_engine

        return get_rule_engine()

    def build_governance_recovery_manager(
        self,
    ) -> "GovernanceRecoveryManager":
        """
        Return the process-wide governance recovery manager.

        Like build_integrity_event_bus, this does not construct a
        fresh instance: recovery plans registered through the API
        need to be visible to whatever triggers recovery (the health
        service, or a direct API caller), which a persistence runtime
        built fresh per request cannot provide on its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_recovery import get_recovery_manager

        return get_recovery_manager()

    def build_governance_scheduler(
        self,
    ) -> "GovernanceScheduler":
        """
        Return the process-wide governance scheduler.

        Like build_integrity_event_bus, this does not construct a
        fresh instance: jobs registered through the API need to be
        visible to whatever queries the scheduler (the lifecycle
        manager, or a direct API caller), which a persistence runtime
        built fresh per request cannot provide on its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_scheduler import get_scheduler

        return get_scheduler()

    def build_governance_job_registry(
        self,
    ) -> "GovernanceJobRegistry":
        """
        Return the process-wide governance job registry.

        Like build_integrity_event_bus, this does not construct a
        fresh instance: job metadata registered through the scheduler
        (or directly through this registry's own API) needs to be
        visible to both, which a persistence runtime built fresh per
        request cannot provide on its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_job_registry import get_job_registry

        return get_job_registry()

    def build_governance_trigger_engine(
        self,
    ) -> "GovernanceTriggerEngine":
        """
        Return the process-wide governance trigger engine.

        Like build_integrity_event_bus, this does not construct a
        fresh instance: triggers registered through the scheduler need
        to be visible to whatever queries the engine directly, which a
        persistence runtime built fresh per request cannot provide on
        its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_trigger_engine import (
            get_trigger_engine,
        )

        return get_trigger_engine()

    def build_governance_execution_manager(
        self,
    ) -> "GovernanceExecutionManager":
        """
        Return the process-wide governance execution manager.

        Like build_integrity_event_bus, this does not construct a
        fresh instance: executions triggered through the API need to
        be visible to whatever queries the manager directly, which a
        persistence runtime built fresh per request cannot provide on
        its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_execution_manager import (
            get_execution_manager,
        )

        return get_execution_manager()

    def build_governance_retry_engine(
        self,
    ) -> "GovernanceRetryEngine":
        """
        Return the process-wide governance retry engine.

        Like build_integrity_event_bus, this does not construct a
        fresh instance: retries scheduled through the execution
        manager need to be visible to whatever queries the engine
        directly, which a persistence runtime built fresh per request
        cannot provide on its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_retry import get_retry_engine

        return get_retry_engine()

    def build_governance_job_persistence(
        self,
    ) -> "GovernanceJobPersistence":
        """
        Return the process-wide governance job persistence layer.

        Like build_integrity_event_bus, this does not construct a
        fresh instance: a save()/load() triggered through the API
        needs to act on the same live job registry/trigger engine/
        retry engine/scheduler every other request or component sees,
        which a persistence runtime built fresh per request cannot
        provide on its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_job_persistence import (
            get_job_persistence,
        )

        return get_job_persistence()

    def build_governance_cron_scheduler(
        self,
    ) -> "GovernanceCronScheduler":
        """
        Return the process-wide governance cron scheduler.

        Like build_integrity_event_bus, this does not construct a
        fresh instance: cron triggers registered through the API need
        to be visible to whatever queries the scheduler directly,
        which a persistence runtime built fresh per request cannot
        provide on its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_cron import get_cron_scheduler

        return get_cron_scheduler()

    def build_governance_job_dependency_manager(
        self,
    ) -> "GovernanceJobDependencyManager":
        """
        Return the process-wide governance job dependency manager.

        Like build_integrity_event_bus, this does not construct a
        fresh instance: dependencies registered through the API need
        to be visible to whatever queries the manager directly (the
        scheduler's own tick, or a direct API caller), which a
        persistence runtime built fresh per request cannot provide on
        its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_job_dependencies import (
            get_job_dependency_manager,
        )

        return get_job_dependency_manager()

    def build_governance_scheduler_lock_manager(
        self,
    ) -> "GovernanceSchedulerLockManager":
        """
        Return the process-wide governance scheduler lock manager.

        Like build_integrity_event_bus, this does not construct a
        fresh instance: locks acquired through the scheduler's own
        tick need to be visible to whatever queries the manager
        directly, which a persistence runtime built fresh per request
        cannot provide on its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_scheduler_locks import (
            get_scheduler_lock_manager,
        )

        return get_scheduler_lock_manager()

    def build_governance_scheduler_metrics(
        self,
    ) -> "GovernanceSchedulerMetrics":
        """
        Return the process-wide governance scheduler metrics
        collector.

        Like build_integrity_event_bus, this does not construct a
        fresh instance: every instrumented component (Scheduler,
        Execution Manager, Retry Engine, Lock Manager) needs to record
        into the same collector, which a persistence runtime built
        fresh per request cannot provide on its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_scheduler_metrics import (
            get_scheduler_metrics,
        )

        return get_scheduler_metrics()

    def build_governance_scheduler_policy_engine(
        self,
    ) -> "GovernanceSchedulerPolicyEngine":
        """
        Return the process-wide governance scheduler policy engine.

        Like build_integrity_event_bus, this does not construct a
        fresh instance: policies registered through the API need to be
        enforced by the same scheduler tick every other request or
        component sees, which a persistence runtime built fresh per
        request cannot provide on its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_scheduler_policy import (
            get_scheduler_policy_engine,
        )

        return get_scheduler_policy_engine()

    def build_governance_scheduler_dashboard(
        self,
    ) -> "GovernanceSchedulerDashboard":
        """
        Return the process-wide governance scheduler dashboard.

        Like build_integrity_event_bus, this does not construct a
        fresh instance — not that a fresh one would compute anything
        differently (the dashboard holds no state of its own beyond
        references to the other scheduling singletons), but so a
        persistence runtime built fresh per request still hands back
        one consistent object, matching every other get_*() accessor
        in this codebase.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_scheduler_dashboard import (
            get_scheduler_dashboard,
        )

        return get_scheduler_dashboard()

    def build_governance_scheduler_bootstrap(
        self,
    ) -> "GovernanceSchedulerBootstrap":
        """
        Return the process-wide governance scheduler bootstrap.

        Like build_integrity_event_bus, this does not construct a
        fresh instance: whether the scheduling subsystem has completed
        initialization needs to be visible to whatever queries it (the
        lifecycle manager's "scheduler" component, or a direct API
        caller), which a persistence runtime built fresh per request
        cannot provide on its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_scheduler_bootstrap import (
            get_scheduler_bootstrap,
        )

        return get_scheduler_bootstrap()

    def build_governance_rollout_manager(
        self,
    ) -> "DeploymentRolloutManager":
        """
        Return the process-wide governance rollout manager.

        Like build_integrity_event_bus, this does not construct a
        fresh instance: which deployments currently have an active
        rollout, and each rollout's lifecycle state, needs to be
        visible to every caller, which a persistence runtime built
        fresh per request cannot provide on its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_rollout_manager import (
            get_rollout_manager,
        )

        return get_rollout_manager()

    def build_governance_deployment_registry(
        self,
    ) -> "DeploymentVersionRegistry":
        """
        Return the process-wide governance deployment version
        registry.

        Like build_integrity_event_bus, this does not construct a
        fresh instance: which deployments are currently registered,
        and their complete revision history, needs to be visible to
        every caller (including the rollout manager resolving
        deployment_id against it), which a persistence runtime built
        fresh per request cannot provide on its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_version_registry import (
            get_version_registry,
        )

        return get_version_registry()

    def build_governance_blue_green_engine(
        self,
    ) -> "BlueGreenDeploymentEngine":
        """
        Return the process-wide Blue/Green deployment engine.

        Like build_integrity_event_bus, this does not construct a
        fresh instance: which environment is currently live for each
        deployment needs to be visible to every caller (including the
        rollout manager delegating strategy="BLUE_GREEN" completion to
        it), which a persistence runtime built fresh per request
        cannot provide on its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_blue_green import (
            get_blue_green_engine,
        )

        return get_blue_green_engine()

    def build_governance_canary_engine(
        self,
    ) -> "CanaryDeploymentEngine":
        """
        Return the process-wide canary deployment engine.

        Like build_integrity_event_bus, this does not construct a
        fresh instance: which deployments have an active canary, and
        their progression/history, needs to be visible to every
        caller (including the rollout manager delegating strategy=
        "CANARY" completion to it), which a persistence runtime built
        fresh per request cannot provide on its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_canary import get_canary_engine

        return get_canary_engine()

    def build_governance_rolling_engine(
        self,
    ) -> "RollingDeploymentEngine":
        """
        Return the process-wide rolling update engine.

        Like build_integrity_event_bus, this does not construct a
        fresh instance: which deployments have an active rolling
        update, and their progression/history, needs to be visible to
        every caller (including the rollout manager delegating
        strategy="ROLLING" completion to it), which a persistence
        runtime built fresh per request cannot provide on its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_rolling import get_rolling_engine

        return get_rolling_engine()

    def build_governance_progressive_delivery_engine(
        self,
    ) -> "ProgressiveDeliveryEngine":
        """
        Return the process-wide progressive delivery engine.

        Like build_integrity_event_bus, this does not construct a
        fresh instance: which deployments have an active progressive
        delivery pipeline, and their stage progression/history, needs
        to be visible to every caller (including the rollout manager
        delegating strategy="PROGRESSIVE" completion to it), which a
        persistence runtime built fresh per request cannot provide on
        its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_progressive_delivery import (
            get_progressive_delivery_engine,
        )

        return get_progressive_delivery_engine()

    def build_governance_traffic_router(
        self,
    ) -> "DeploymentTrafficRouter":
        """
        Return the process-wide deployment traffic router.

        Like build_integrity_event_bus, this does not construct a
        fresh instance: every rollout engine's routing changes (and
        every API reader) need to observe the same routing tables,
        which a persistence runtime built fresh per request cannot
        provide on its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_traffic_router import (
            get_traffic_router,
        )

        return get_traffic_router()

    def build_governance_rollback_engine(
        self,
    ) -> "DeploymentRollbackEngine":
        """
        Return the process-wide automated rollback engine.

        Like build_integrity_event_bus, this does not construct a
        fresh instance: which deployments have an active rollback
        plan, and their execution history, needs to be visible to
        every caller, which a persistence runtime built fresh per
        request cannot provide on its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_rollback import get_rollback_engine

        return get_rollback_engine()

    def build_governance_rollout_health_engine(
        self,
    ) -> "DeploymentRolloutHealthEngine":
        """
        Return the process-wide rollout health engine.

        Like build_integrity_event_bus, this does not construct a
        fresh instance: every deployment's evaluation history needs to
        be visible to every caller, which a persistence runtime built
        fresh per request cannot provide on its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_rollout_health import (
            get_rollout_health_engine,
        )

        return get_rollout_health_engine()

    def build_governance_rollout_analytics(
        self,
    ) -> "DeploymentRolloutAnalytics":
        """
        Return the process-wide rollout analytics engine.

        Like build_integrity_event_bus, this does not construct a
        fresh instance: the recorded outcome/rollback/health-score
        history every KPI and trend is derived from needs to be
        visible to every caller, which a persistence runtime built
        fresh per request cannot provide on its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_rollout_analytics import (
            get_rollout_analytics,
        )

        return get_rollout_analytics()

    def build_governance_rollout_policy_engine(
        self,
    ) -> "DeploymentRolloutPolicyEngine":
        """
        Return the process-wide rollout policy engine.

        Like build_integrity_event_bus, this does not construct a
        fresh instance: policies registered through the API need to be
        enforced identically by every rollout lifecycle checkpoint,
        which a persistence runtime built fresh per request cannot
        provide on its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_rollout_policy import (
            get_rollout_policy_engine,
        )

        return get_rollout_policy_engine()

    def build_governance_rollout_dashboard(
        self,
    ) -> "DeploymentRolloutDashboard":
        """
        Return the process-wide rollout dashboard.

        Like build_integrity_event_bus, this does not construct a
        fresh instance: its cache (when configured with a nonzero
        cache_ttl_seconds) needs to be shared across requests, which a
        persistence runtime built fresh per request cannot provide on
        its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_rollout_dashboard import (
            get_rollout_dashboard,
        )

        return get_rollout_dashboard()

    def build_governance_rbac_engine(
        self,
    ) -> "DeploymentRBACEngine":
        """
        Return the process-wide deployment RBAC engine.

        Like build_integrity_event_bus, this does not construct a
        fresh instance: roles and principal assignments registered
        through the API need to be enforced identically by every
        protected governance operation, which a persistence runtime
        built fresh per request cannot provide on its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_rbac import get_rbac_engine

        return get_rbac_engine()

    def build_governance_authentication_manager(
        self,
    ) -> "DeploymentAuthenticationManager":
        """
        Return the process-wide deployment authentication manager.

        Like build_integrity_event_bus, this does not construct a
        fresh instance: sessions issued through the API need to be
        validated and revoked identically by every caller, which a
        persistence runtime built fresh per request cannot provide on
        its own.

        Imported locally (not at module top level) to avoid a
        circular import, matching build_diagnostics_service below.
        """

        from .deployment_governance_authentication import (
            get_authentication_manager,
        )

        return get_authentication_manager()

    def build_integrity_provider_configuration_service(
        self,
    ) -> "GovernanceIntegrityProviderConfigurationService":
        """
        Build the governance audit provider configuration service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_provider_configuration import (
            GovernanceIntegrityProviderConfigurationService,
        )

        return GovernanceIntegrityProviderConfigurationService(
            self.provider_configuration_repository,
            self.build_integrity_provider_registry(),
        )

    def build_integrity_provider_secrets_service(
        self,
    ) -> "GovernanceIntegrityProviderSecretsService":
        """
        Build the governance audit provider secrets service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_provider_secrets import (
            GovernanceIntegrityProviderSecretsService,
        )

        return GovernanceIntegrityProviderSecretsService(
            self.provider_secrets_repository,
            self.build_integrity_provider_registry(),
        )

    def build_integrity_provider_authentication_service(
        self,
    ) -> "GovernanceIntegrityProviderAuthenticationService":
        """
        Build the governance audit provider authentication service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_provider_authentication import (
            GovernanceIntegrityProviderAuthenticationService,
        )

        return GovernanceIntegrityProviderAuthenticationService(
            self.build_integrity_provider_configuration_service(),
            self.build_integrity_provider_secrets_service(),
            self.build_integrity_provider_registry(),
        )

    def build_integrity_provider_request_service(
        self,
    ) -> "GovernanceIntegrityProviderRequestService":
        """
        Build the governance audit provider request service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_provider_requests import (
            GovernanceIntegrityProviderRequestService,
        )

        return GovernanceIntegrityProviderRequestService(
            self.build_integrity_provider_authentication_service(),
            self.build_integrity_provider_configuration_service(),
            self.build_integrity_delivery_policy_service(),
            self.build_integrity_provider_registry(),
        )

    def build_integrity_provider_response_service(
        self,
    ) -> "GovernanceIntegrityProviderResponseService":
        """
        Build the governance audit provider response service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_provider_responses import (
            GovernanceIntegrityProviderResponseService,
        )

        return GovernanceIntegrityProviderResponseService(
            self.build_integrity_provider_registry()
        )

    def build_integrity_metrics_repository(
        self,
    ) -> GovernanceIntegrityMetricsRepository:
        """
        Return the durable governance metrics repository.

        Like build_integrity_metrics_service, this returns the
        runtime's single stored instance.
        """

        return self.metrics_repository

    def build_integrity_metrics_history_repository(
        self,
    ) -> GovernanceIntegrityMetricsHistoryRepository:
        """
        Return the durable governance metrics history repository.

        Like build_integrity_metrics_service, this returns the
        runtime's single stored instance.
        """

        return self.metrics_history_repository

    def build_integrity_metrics_service(
        self,
    ) -> GovernanceIntegrityMetricsService:
        """
        Return the shared governance audit notification delivery
        metrics service.

        Unlike most build_integrity_* methods, this returns the
        runtime's single stored instance rather than constructing a
        new one: the delivery engine, retry orchestrator, and
        delivery runtime record into and read from the same live
        counters.
        """

        return self.metrics_service

    def build_integrity_metrics_alert_service(
        self,
    ) -> GovernanceIntegrityMetricsAlertService:
        """
        Return the shared governance metrics alert service.

        Like build_integrity_metrics_service, this returns the
        runtime's single stored instance so active alert state
        persists across calls within one process.
        """

        return self.metrics_alert_service

    def build_integrity_logger(
        self,
    ) -> GovernanceIntegrityLogger:
        """
        Return the shared governance structured logger.

        Like build_integrity_metrics_service, this returns the
        runtime's single stored instance rather than constructing a
        new one, so every governance component that logs through it
        shares one buffered history of recent activity.
        """

        return self.logger

    def build_integrity_log_repository(
        self,
    ) -> GovernanceLogRepository:
        """
        Return the shared governance log repository.

        Like build_integrity_logger, this returns the runtime's
        single stored instance. The logger writes every entry
        through this repository (see __post_init__), so it is
        durable under the SQLite backend and reflects the same
        history the logger itself observed.
        """

        return self.log_repository

    def build_integrity_log_rotation_service(
        self,
    ) -> GovernanceLogRotationService:
        """
        Return the shared governance log rotation service.

        Like build_integrity_log_repository, this returns the
        runtime's single stored instance. It is already attached to
        the log repository (see __post_init__), so it runs
        automatically after every log append; this accessor exists
        for callers that want to inspect its policy or trigger a
        rotation explicitly (e.g. the CLI or runtime startup).
        """

        return self.log_rotation_service

    def build_integrity_log_search_service(
        self,
    ) -> GovernanceLogSearchService:
        """
        Build a governance log search service bound to the shared
        log repository.

        Unlike build_integrity_logger/build_integrity_log_repository,
        this constructs a new (stateless) instance on every call: the
        search service holds no state of its own beyond a reference
        to the shared repository.
        """

        return GovernanceLogSearchService(self.log_repository)

    def build_integrity_log_replay_service(
        self,
        *,
        since: "datetime | None" = None,
        event: str | None = None,
    ) -> GovernanceLogReplayService:
        """
        Build a governance log replay service bound to a fresh
        search service over the shared log repository, scoped to
        the given since/event filters.

        Like build_integrity_log_search_service, this constructs a
        new instance on every call: a replay service's cursor is
        inherently per-session state (see
        GovernanceLogReplayService), not something to share across
        callers the way the logger or repository singletons are.
        """

        return GovernanceLogReplayService(
            self.build_integrity_log_search_service(),
            since=since,
            event=event,
        )

    def build_integrity_log_config_service(
        self,
    ) -> GovernanceLogConfigService:
        """
        Return the shared governance logging configuration service.

        Like build_integrity_logger, this returns the runtime's
        single stored instance, already applied once at __post_init__
        time; call reload_log_config() on this runtime (not this
        service directly) to re-read and re-apply it to the logger
        and batcher together.
        """

        return self.log_config_service

    def build_integrity_log_redaction_service(
        self,
    ) -> GovernanceLogRedactionService:
        """
        Return the shared governance log redaction service.

        Like build_integrity_logger, this returns the runtime's
        single stored instance. It is already attached to the
        logger (see __post_init__), so registering or unregistering
        a rule here takes effect for every future logged entry too.
        """

        return self.redaction_service

    def build_integrity_log_context_service(
        self,
    ) -> GovernanceLogContextService:
        """
        Return the shared governance log execution context service.

        Like build_integrity_logger, this returns the runtime's
        single stored instance. It is already attached to the
        logger (see __post_init__), so a scope pushed anywhere (the
        delivery worker, the delivery runtime, or the delivery
        engine's provider execution) is automatically merged into
        every log entry produced while it is active.
        """

        return self.context_service

    def build_integrity_log_correlation_service(
        self,
    ) -> GovernanceCorrelationService:
        """
        Return the shared governance log correlation service.

        Like build_integrity_logger, this returns the runtime's
        single stored instance. It is already attached to the
        logger (see __post_init__), so correlation_id and
        parent_correlation_id are automatically merged into every
        log entry produced while a correlation is active.
        """

        return self.correlation_service

    def build_integrity_log_sampling_service(
        self,
    ) -> GovernanceLogSamplingService:
        """
        Return the shared governance log sampling service.

        Like build_integrity_logger, this returns the runtime's
        single stored instance. It is already attached to the
        logger (see __post_init__), so a policy update here takes
        effect for every future logged entry too: a sampled-out
        entry is never durably persisted.
        """

        return self.sampling_service

    def build_integrity_log_batcher(
        self,
    ) -> GovernanceLogBatcher:
        """
        Return the shared governance log batcher, bound to the same
        log repository as the logger.

        Unlike every other build_integrity_log_* accessor here, this
        one is not pre-attached to build_integrity_logger()'s
        instance (see __post_init__): attach it explicitly with
        logger.set_batcher(...) to actually route log writes through
        it instead of writing directly.
        """

        return self.batcher

    def build_integrity_log_export_service(
        self,
    ) -> GovernanceLogExportService:
        """
        Build a governance log export service bound to a fresh
        search service over the shared log repository, and the
        shared redaction service: exported entries are redacted a
        second time on the way out, independent of whether they were
        already redacted before persistence.

        Like build_integrity_log_search_service, this constructs a
        new (stateless) instance on every call.
        """

        return GovernanceLogExportService(
            self.build_integrity_log_search_service(),
            redaction_service=(
                self.build_integrity_log_redaction_service()
            ),
        )

    def build_integrity_retry_orchestrator(
        self,
    ) -> "GovernanceIntegrityRetryOrchestrator":
        """
        Build the governance audit retry orchestrator.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_retry_orchestrator import (
            GovernanceIntegrityRetryOrchestrator,
        )

        return GovernanceIntegrityRetryOrchestrator(
            metrics_service=self.build_integrity_metrics_service(),
        )

    def build_integrity_delivery_scheduler(
        self,
    ) -> "GovernanceIntegrityDeliveryScheduler":
        """
        Build the governance audit delivery scheduler.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_delivery_scheduler import (
            GovernanceIntegrityDeliveryScheduler,
        )

        return GovernanceIntegrityDeliveryScheduler(
            self.delivery_schedule_repository
        )

    def build_integrity_delivery_engine(
        self,
    ) -> "GovernanceIntegrityDeliveryEngine":
        """
        Build the governance audit notification delivery engine.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_delivery_engine import (
            GovernanceIntegrityDeliveryEngine,
        )

        return GovernanceIntegrityDeliveryEngine(
            self.notification_dispatch_repository,
            self.notification_repository,
            self.notification_channel_repository,
            self.build_integrity_provider_registry(),
            self.build_integrity_delivery_policy_service(),
            self.build_integrity_provider_request_service(),
            self.build_integrity_provider_response_service(),
            self.build_integrity_retry_orchestrator(),
            scheduler=self.build_integrity_delivery_scheduler(),
            metrics_service=self.build_integrity_metrics_service(),
            logger=self.build_integrity_logger(),
            context_service=self.build_integrity_log_context_service(),
        )

    def build_integrity_delivery_worker(
        self,
    ) -> "GovernanceIntegrityDeliveryWorker":
        """
        Build the governance audit delivery worker.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_delivery_worker import (
            GovernanceIntegrityDeliveryWorker,
        )

        return GovernanceIntegrityDeliveryWorker(
            self.build_integrity_delivery_scheduler(),
            self.build_integrity_delivery_engine(),
            self.build_integrity_retry_orchestrator(),
            context_service=self.build_integrity_log_context_service(),
            correlation_service=(
                self.build_integrity_log_correlation_service()
            ),
        )

    def build_integrity_delivery_history_service(
        self,
    ) -> "GovernanceIntegrityDeliveryHistoryService":
        """
        Build the governance audit delivery history service.

        Imported locally (not at module top level) to avoid a circular
        import, matching build_diagnostics_service below.
        """

        from .deployment_governance_delivery_history import (
            GovernanceIntegrityDeliveryHistoryService,
        )

        return GovernanceIntegrityDeliveryHistoryService(
            self.build_integrity_delivery_engine(),
            self.delivery_history_repository,
        )

    def build_diagnostics_service(
        self,
    ) -> "DeploymentGovernancePersistenceDiagnosticsService":
        """
        Build a diagnostics service for this persistence runtime.

        Imported locally (not at module top level) because the diagnostics
        module imports this module's types; a top-level import here would
        create a circular import.
        """

        from .deployment_governance_persistence_diagnostics import (
            DeploymentGovernancePersistenceDiagnosticsService,
        )

        return (
            DeploymentGovernancePersistenceDiagnosticsService(
                self
            )
        )


def build_deployment_governance_persistence(
    config: DeploymentGovernancePersistenceConfig
    | None = None,
    *,
    automatic_audit_retention: (
        GovernanceIntegrityAuditAutomaticRetentionConfig | None
    ) = None,
) -> DeploymentGovernancePersistenceRuntime:
    """
    Build the configured deployment governance persistence runtime.

    The returned runtime always exposes:

    - a repository conforming to DeploymentGovernanceTraceRepository,
    - a repository-backed DeploymentGovernanceTraceRegistry.

    SQLite mode additionally exposes the underlying SQLiteDatabase instance.

    automatic_audit_retention defaults to disabled, preserving existing
    behavior for callers that do not opt in.
    """

    if config is None:
        config = (
            DeploymentGovernancePersistenceConfig.memory()
        )

    resolved_automatic_audit_retention = (
        automatic_audit_retention
        or GovernanceIntegrityAuditAutomaticRetentionConfig.disabled()
    )

    if (
        config.backend
        is DeploymentGovernancePersistenceBackend.MEMORY
    ):
        return _build_memory_runtime(
            config,
            automatic_audit_retention=(
                resolved_automatic_audit_retention
            ),
        )

    if (
        config.backend
        is DeploymentGovernancePersistenceBackend.SQLITE
    ):
        return _build_sqlite_runtime(
            config,
            automatic_audit_retention=(
                resolved_automatic_audit_retention
            ),
        )

    raise AssertionError(
        "unhandled deployment governance persistence backend "
        f"'{config.backend}'"
    )


def _build_memory_runtime(
    config: DeploymentGovernancePersistenceConfig,
    *,
    automatic_audit_retention: (
        GovernanceIntegrityAuditAutomaticRetentionConfig
    ),
) -> DeploymentGovernancePersistenceRuntime:
    """
    Build an ephemeral in-memory governance persistence runtime.
    """

    trace_engine = DeploymentGovernanceTraceEngine()

    repository = (
        InMemoryDeploymentGovernanceTraceRepository()
    )

    registry = DeploymentGovernanceTraceRegistry(
        trace_engine,
        repository=repository,
    )

    audit_history_repository = (
        InMemoryGovernanceIntegrityAuditHistoryRepository()
    )

    bookmark_repository = (
        InMemoryGovernanceIntegrityAuditBookmarkRepository()
    )

    label_repository = (
        InMemoryGovernanceIntegrityAuditLabelRepository()
    )

    saved_query_repository = (
        InMemoryGovernanceIntegritySavedAuditQueryRepository()
    )

    collection_repository = (
        InMemoryGovernanceIntegrityAuditCollectionRepository()
    )

    report_template_repository = (
        InMemoryGovernanceIntegrityAuditReportTemplateRepository()
    )

    report_schedule_repository = (
        InMemoryGovernanceIntegrityAuditReportScheduleRepository()
    )

    execution_queue_repository = (
        InMemoryGovernanceIntegrityAuditExecutionQueueRepository()
    )

    execution_repository = (
        InMemoryGovernanceIntegrityAuditExecutionRepository()
    )

    retry_repository = (
        InMemoryGovernanceIntegrityRetryRepository()
    )

    dead_letter_repository = (
        InMemoryGovernanceIntegrityDeadLetterRepository()
    )

    failure_policy_repository = (
        InMemoryGovernanceIntegrityFailurePolicyRepository()
    )

    notification_repository = (
        InMemoryGovernanceIntegrityNotificationRepository()
    )

    notification_channel_repository = (
        InMemoryGovernanceIntegrityNotificationChannelRepository()
    )

    notification_dispatch_repository = (
        InMemoryGovernanceIntegrityNotificationDispatchRepository()
    )

    delivery_history_repository = (
        InMemoryGovernanceIntegrityDeliveryHistoryRepository()
    )

    notification_preference_repository = (
        InMemoryGovernanceIntegrityNotificationPreferenceRepository()
    )

    delivery_policy_repository = (
        InMemoryGovernanceIntegrityDeliveryPolicyRepository()
    )

    provider_configuration_repository = (
        InMemoryGovernanceIntegrityProviderConfigurationRepository()
    )

    provider_secrets_repository = (
        InMemoryGovernanceIntegrityProviderSecretsRepository()
    )

    delivery_schedule_repository = (
        InMemoryGovernanceIntegrityDeliveryScheduleRepository()
    )

    metrics_repository = (
        InMemoryGovernanceIntegrityMetricsRepository()
    )

    metrics_history_repository = (
        InMemoryGovernanceIntegrityMetricsHistoryRepository()
    )

    metrics_service = GovernanceIntegrityMetricsService(
        metrics_repository,
        history_repository=metrics_history_repository,
        history_retention=DEFAULT_METRICS_HISTORY_RETENTION,
    )

    return DeploymentGovernancePersistenceRuntime(
        config=config,
        repository=repository,
        registry=registry,
        audit_history_repository=audit_history_repository,
        bookmark_repository=bookmark_repository,
        label_repository=label_repository,
        saved_query_repository=saved_query_repository,
        collection_repository=collection_repository,
        report_template_repository=report_template_repository,
        report_schedule_repository=report_schedule_repository,
        execution_queue_repository=execution_queue_repository,
        execution_repository=execution_repository,
        retry_repository=retry_repository,
        dead_letter_repository=dead_letter_repository,
        failure_policy_repository=failure_policy_repository,
        notification_repository=notification_repository,
        notification_channel_repository=notification_channel_repository,
        notification_dispatch_repository=(
            notification_dispatch_repository
        ),
        delivery_history_repository=delivery_history_repository,
        notification_preference_repository=(
            notification_preference_repository
        ),
        delivery_policy_repository=delivery_policy_repository,
        provider_configuration_repository=(
            provider_configuration_repository
        ),
        provider_secrets_repository=provider_secrets_repository,
        delivery_schedule_repository=delivery_schedule_repository,
        database=None,
        automatic_audit_retention=automatic_audit_retention,
        metrics_repository=metrics_repository,
        metrics_history_repository=metrics_history_repository,
        metrics_service=metrics_service,
    )


def _build_sqlite_runtime(
    config: DeploymentGovernancePersistenceConfig,
    *,
    automatic_audit_retention: (
        GovernanceIntegrityAuditAutomaticRetentionConfig
    ),
) -> DeploymentGovernancePersistenceRuntime:
    """
    Build a durable SQLite governance persistence runtime.
    """

    database = SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=config.database_path,
            timeout_seconds=(
                config.sqlite_timeout_seconds
            ),
            enable_foreign_keys=(
                config.sqlite_enforce_foreign_keys
            ),
            journal_mode=(
                "WAL"
                if config.sqlite_enable_wal
                else "DELETE"
            ),
        )
    )

    repository = (
        SQLiteDeploymentGovernanceTraceRepository(
            database,
            initialize_schema=(
                config.initialize_schema
            ),
        )
    )

    audit_history_repository = (
        SQLiteGovernanceIntegrityAuditHistoryRepository(
            database,
            initialize_schema=(
                config.initialize_schema
            ),
        )
    )

    bookmark_repository = (
        SQLiteGovernanceIntegrityAuditBookmarkRepository(
            database,
            initialize_schema=(
                config.initialize_schema
            ),
        )
    )

    label_repository = (
        SQLiteGovernanceIntegrityAuditLabelRepository(
            database,
            initialize_schema=(
                config.initialize_schema
            ),
        )
    )

    saved_query_repository = (
        SQLiteGovernanceIntegritySavedAuditQueryRepository(
            database,
            initialize_schema=(
                config.initialize_schema
            ),
        )
    )

    collection_repository = (
        SQLiteGovernanceIntegrityAuditCollectionRepository(
            database,
            initialize_schema=(
                config.initialize_schema
            ),
        )
    )

    report_template_repository = (
        SQLiteGovernanceIntegrityAuditReportTemplateRepository(
            database,
            initialize_schema=(
                config.initialize_schema
            ),
        )
    )

    report_schedule_repository = (
        SQLiteGovernanceIntegrityAuditReportScheduleRepository(
            database,
            initialize_schema=(
                config.initialize_schema
            ),
        )
    )

    failure_policy_repository = (
        SQLiteGovernanceIntegrityFailurePolicyRepository(
            database,
            initialize_schema=(
                config.initialize_schema
            ),
        )
    )

    notification_repository = (
        SQLiteGovernanceIntegrityNotificationRepository(
            database,
            initialize_schema=(
                config.initialize_schema
            ),
        )
    )

    notification_channel_repository = (
        SQLiteGovernanceIntegrityNotificationChannelRepository(
            database,
            initialize_schema=(
                config.initialize_schema
            ),
        )
    )

    notification_dispatch_repository = (
        SQLiteGovernanceIntegrityNotificationDispatchRepository(
            database,
            initialize_schema=(
                config.initialize_schema
            ),
        )
    )

    delivery_history_repository = (
        SQLiteGovernanceIntegrityDeliveryHistoryRepository(
            database,
            initialize_schema=(
                config.initialize_schema
            ),
        )
    )

    notification_preference_repository = (
        SQLiteGovernanceIntegrityNotificationPreferenceRepository(
            database,
            initialize_schema=(
                config.initialize_schema
            ),
        )
    )

    delivery_policy_repository = (
        SQLiteGovernanceIntegrityDeliveryPolicyRepository(
            database,
            initialize_schema=(
                config.initialize_schema
            ),
        )
    )

    provider_configuration_repository = (
        SQLiteGovernanceIntegrityProviderConfigurationRepository(
            database,
            initialize_schema=(
                config.initialize_schema
            ),
        )
    )

    provider_secrets_repository = (
        SQLiteGovernanceIntegrityProviderSecretsRepository(
            database,
            initialize_schema=(
                config.initialize_schema
            ),
        )
    )

    delivery_schedule_repository = (
        SQLiteGovernanceIntegrityDeliveryScheduleRepository(
            database,
            initialize_schema=(
                config.initialize_schema
            ),
        )
    )

    metrics_repository = (
        SQLiteGovernanceIntegrityMetricsRepository(
            database,
            initialize_schema=(
                config.initialize_schema
            ),
        )
    )

    metrics_history_repository = (
        SQLiteGovernanceIntegrityMetricsHistoryRepository(
            database,
            initialize_schema=(
                config.initialize_schema
            ),
        )
    )

    log_repository = (
        SQLiteGovernanceLogRepository(
            database,
            initialize_schema=(
                config.initialize_schema
            ),
        )
    )

    # SQLite persistence for the execution queue is intentionally
    # deferred (see deployment_governance_audit_execution_queue.py):
    # it stays in-process memory regardless of the configured backend.
    execution_queue_repository = (
        InMemoryGovernanceIntegrityAuditExecutionQueueRepository()
    )

    # SQLite persistence for execution records is intentionally
    # deferred (see deployment_governance_audit_worker.py): it stays
    # in-process memory regardless of the configured backend.
    execution_repository = (
        InMemoryGovernanceIntegrityAuditExecutionRepository()
    )

    # SQLite persistence for retry records is intentionally deferred
    # (see deployment_governance_audit_retry.py): it stays in-process
    # memory regardless of the configured backend.
    retry_repository = (
        InMemoryGovernanceIntegrityRetryRepository()
    )

    # SQLite persistence for dead letter records is intentionally
    # deferred (see deployment_governance_dead_letter_queue.py): it
    # stays in-process memory regardless of the configured backend.
    dead_letter_repository = (
        InMemoryGovernanceIntegrityDeadLetterRepository()
    )

    trace_engine = DeploymentGovernanceTraceEngine()

    registry = DeploymentGovernanceTraceRegistry(
        trace_engine,
        repository=repository,
    )

    metrics_service = GovernanceIntegrityMetricsService(
        metrics_repository,
        history_repository=metrics_history_repository,
        history_retention=DEFAULT_METRICS_HISTORY_RETENTION,
    )

    return DeploymentGovernancePersistenceRuntime(
        config=config,
        repository=repository,
        registry=registry,
        audit_history_repository=audit_history_repository,
        bookmark_repository=bookmark_repository,
        label_repository=label_repository,
        saved_query_repository=saved_query_repository,
        collection_repository=collection_repository,
        report_template_repository=report_template_repository,
        report_schedule_repository=report_schedule_repository,
        execution_queue_repository=execution_queue_repository,
        execution_repository=execution_repository,
        retry_repository=retry_repository,
        dead_letter_repository=dead_letter_repository,
        failure_policy_repository=failure_policy_repository,
        notification_repository=notification_repository,
        notification_channel_repository=notification_channel_repository,
        notification_dispatch_repository=(
            notification_dispatch_repository
        ),
        delivery_history_repository=delivery_history_repository,
        notification_preference_repository=(
            notification_preference_repository
        ),
        delivery_policy_repository=delivery_policy_repository,
        provider_configuration_repository=(
            provider_configuration_repository
        ),
        provider_secrets_repository=provider_secrets_repository,
        delivery_schedule_repository=delivery_schedule_repository,
        database=database,
        automatic_audit_retention=automatic_audit_retention,
        metrics_repository=metrics_repository,
        metrics_history_repository=metrics_history_repository,
        metrics_service=metrics_service,
        log_repository=log_repository,
    )


def deployment_governance_persistence_config_from_env(
    *,
    environ: Mapping[str, str] | None = None,
) -> DeploymentGovernancePersistenceConfig:
    """
    Build governance persistence configuration from environment variables.

    Supported variables:

    NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND
    NOTEBOOK2API_GOVERNANCE_DATABASE_PATH
    NOTEBOOK2API_GOVERNANCE_SQLITE_TIMEOUT_SECONDS
    NOTEBOOK2API_GOVERNANCE_SQLITE_WAL
    NOTEBOOK2API_GOVERNANCE_SQLITE_FOREIGN_KEYS
    """

    if environ is None:
        environ = os.environ

    backend = (
        DeploymentGovernancePersistenceBackend.parse(
            environ.get(
                "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND",
                DeploymentGovernancePersistenceBackend.MEMORY.value,
            )
        )
    )

    database_path = Path(
        environ.get(
            "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
            str(
                DEFAULT_GOVERNANCE_DATABASE_PATH
            ),
        )
    )

    timeout_seconds = float(
        environ.get(
            "NOTEBOOK2API_GOVERNANCE_SQLITE_TIMEOUT_SECONDS",
            "30.0",
        )
    )

    enable_wal = _parse_boolean_environment_value(
        environ.get(
            "NOTEBOOK2API_GOVERNANCE_SQLITE_WAL",
            "true",
        ),
        variable_name=(
            "NOTEBOOK2API_GOVERNANCE_SQLITE_WAL"
        ),
    )

    enforce_foreign_keys = (
        _parse_boolean_environment_value(
            environ.get(
                "NOTEBOOK2API_GOVERNANCE_SQLITE_FOREIGN_KEYS",
                "true",
            ),
            variable_name=(
                "NOTEBOOK2API_GOVERNANCE_SQLITE_FOREIGN_KEYS"
            ),
        )
    )

    return DeploymentGovernancePersistenceConfig(
        backend=backend,
        database_path=database_path,
        sqlite_timeout_seconds=timeout_seconds,
        sqlite_enable_wal=enable_wal,
        sqlite_enforce_foreign_keys=(
            enforce_foreign_keys
        ),
    )


def _parse_boolean_environment_value(
    value: str,
    *,
    variable_name: str,
) -> bool:
    normalized = (
        value
        .strip()
        .lower()
    )

    if normalized in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return True

    if normalized in {
        "0",
        "false",
        "no",
        "off",
    }:
        return False

    raise ValueError(
        f"{variable_name} must be one of "
        "true, false, 1, 0, yes, no, on, or off"
    )
