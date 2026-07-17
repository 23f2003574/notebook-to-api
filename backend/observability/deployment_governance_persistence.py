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
from .deployment_governance_audit_history import (
    GovernanceIntegrityAuditHistoryRepository,
    InMemoryGovernanceIntegrityAuditHistoryRepository,
)
from .deployment_governance_audit_retention import (
    GovernanceIntegrityAuditAutomaticRetentionConfig,
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
    from .deployment_governance_check import (
        GovernanceIntegrityCheckService,
    )
    from .deployment_governance_persistence_diagnostics import (
        DeploymentGovernancePersistenceDiagnosticsService,
    )


DEFAULT_GOVERNANCE_DATABASE_PATH: Final[
    Path
] = Path(
    "data/notebook2api.db"
)


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

    database: SQLiteDatabase | None = None

    automatic_audit_retention: (
        GovernanceIntegrityAuditAutomaticRetentionConfig
    ) = field(
        default_factory=(
            GovernanceIntegrityAuditAutomaticRetentionConfig.disabled
        )
    )

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
        database=None,
        automatic_audit_retention=automatic_audit_retention,
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
        database=database,
        automatic_audit_retention=automatic_audit_retention,
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
