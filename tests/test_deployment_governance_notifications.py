from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.observability.deployment_governance_audit_worker import (
    GovernanceIntegrityAuditExecutionRecord,
    GovernanceIntegrityExecutionResult,
    InMemoryGovernanceIntegrityAuditExecutionRepository,
)
from backend.observability.deployment_governance_audit_reports import (
    GovernanceIntegrityAuditReport,
)
from backend.observability.deployment_governance_audit_statistics import (
    GovernanceIntegrityAuditCurrentState,
    GovernanceIntegrityAuditStatisticsSnapshot,
)
from backend.observability.deployment_governance_execution_alerts import (
    GovernanceIntegrityAlertPolicy,
    GovernanceIntegrityAlertSeverity,
    GovernanceIntegrityExecutionAlertService,
)
from backend.observability.deployment_governance_execution_metrics import (
    GovernanceIntegrityExecutionMetricsService,
)
from backend.observability.deployment_governance_notifications import (
    GovernanceIntegrityNotification,
    GovernanceIntegrityNotificationService,
    GovernanceIntegrityNotificationStatus,
    InMemoryGovernanceIntegrityNotificationRepository,
)
from backend.observability.deployment_governance_persistence import (
    DeploymentGovernancePersistenceConfig,
    build_deployment_governance_persistence,
)
from backend.observability.sqlite_deployment_governance_notifications import (
    SQLiteGovernanceIntegrityNotificationRepository,
)

BASE_TIME = datetime(2026, 7, 15, 23, 0, 0, tzinfo=timezone.utc)


def make_empty_statistics() -> GovernanceIntegrityAuditStatisticsSnapshot:
    return GovernanceIntegrityAuditStatisticsSnapshot(
        total_audits=0,
        healthy_audits=0,
        unhealthy_audits=0,
        health_rate=None,
        current_state=GovernanceIntegrityAuditCurrentState.NO_HISTORY,
        current_streak=0,
        longest_healthy_streak=0,
        longest_unhealthy_streak=0,
        first_audit_started_at=None,
        latest_audit_started_at=None,
        total_records_checked=0,
        total_invalid_records=0,
        total_integrity_mismatches=0,
        total_missing_integrity_metadata=0,
        total_invalid_integrity_metadata=0,
    )


def make_report(title: str) -> GovernanceIntegrityAuditReport:
    return GovernanceIntegrityAuditReport(
        title=title,
        generated_at=BASE_TIME,
        audits=(),
        statistics=make_empty_statistics(),
    )


def make_failed_record(
    job_id: str,
    *,
    template_name: str = "nightly",
    offset_minutes: int = 0,
) -> GovernanceIntegrityAuditExecutionRecord:
    started_at = BASE_TIME + timedelta(minutes=offset_minutes)

    return GovernanceIntegrityAuditExecutionRecord(
        job_id=job_id,
        schedule_name=template_name,
        template_name=template_name,
        result=GovernanceIntegrityExecutionResult.FAILED,
        report=None,
        error="boom",
        started_at=started_at,
        finished_at=started_at + timedelta(milliseconds=100),
    )


def make_success_record(
    job_id: str,
    *,
    template_name: str = "nightly",
    offset_minutes: int = 0,
) -> GovernanceIntegrityAuditExecutionRecord:
    started_at = BASE_TIME + timedelta(minutes=offset_minutes)

    return GovernanceIntegrityAuditExecutionRecord(
        job_id=job_id,
        schedule_name=template_name,
        template_name=template_name,
        result=GovernanceIntegrityExecutionResult.SUCCESS,
        report=make_report(template_name),
        error=None,
        started_at=started_at,
        finished_at=started_at + timedelta(milliseconds=100),
    )


STRICT_POLICY = GovernanceIntegrityAlertPolicy(
    minimum_success_rate=90.0,
    maximum_failure_rate=0.0,
    maximum_average_duration_ms=1_000_000.0,
)


class Harness:
    def __init__(
        self,
        *,
        alert_uuid_factory=None,
        clock=None,
        uuid_factory=None,
    ) -> None:
        self.execution_repository = (
            InMemoryGovernanceIntegrityAuditExecutionRepository()
        )

        self.metrics_service = GovernanceIntegrityExecutionMetricsService(
            self.execution_repository
        )

        self.alert_service = GovernanceIntegrityExecutionAlertService(
            self.metrics_service,
            uuid_factory=alert_uuid_factory,
        )

        self.repository = (
            InMemoryGovernanceIntegrityNotificationRepository()
        )

        self.service = GovernanceIntegrityNotificationService(
            self.alert_service,
            self.repository,
            clock=clock,
            uuid_factory=uuid_factory,
        )


# --- Model -------------------------------------------------------------


def test_notification_rejects_empty_notification_id() -> None:
    with pytest.raises(
        ValueError, match="notification_id must not be empty"
    ):
        GovernanceIntegrityNotification(
            notification_id="  ",
            alert_id="alert-1",
            severity=GovernanceIntegrityAlertSeverity.WARNING,
            message="boom",
            status=GovernanceIntegrityNotificationStatus.PENDING,
            created_at=BASE_TIME,
        )


def test_notification_rejects_naive_created_at() -> None:
    with pytest.raises(
        ValueError, match="created_at must be timezone-aware"
    ):
        GovernanceIntegrityNotification(
            notification_id="notification-1",
            alert_id="alert-1",
            severity=GovernanceIntegrityAlertSeverity.WARNING,
            message="boom",
            status=GovernanceIntegrityNotificationStatus.PENDING,
            created_at=datetime(2026, 7, 15, 23, 0, 0),
        )


# --- Repository ----------------------------------------------------------


def test_repository_save_and_get() -> None:
    repository = InMemoryGovernanceIntegrityNotificationRepository()

    notification = GovernanceIntegrityNotification(
        notification_id="notification-1",
        alert_id="alert-1",
        severity=GovernanceIntegrityAlertSeverity.WARNING,
        message="boom",
        status=GovernanceIntegrityNotificationStatus.PENDING,
        created_at=BASE_TIME,
    )

    repository.save(notification)

    assert repository.get("notification-1") == notification


def test_repository_delete_missing_raises_key_error() -> None:
    repository = InMemoryGovernanceIntegrityNotificationRepository()

    with pytest.raises(KeyError):
        repository.delete("missing")


def test_repository_clear_empties_store() -> None:
    repository = InMemoryGovernanceIntegrityNotificationRepository()

    repository.save(
        GovernanceIntegrityNotification(
            notification_id="notification-1",
            alert_id="alert-1",
            severity=GovernanceIntegrityAlertSeverity.WARNING,
            message="boom",
            status=GovernanceIntegrityNotificationStatus.PENDING,
            created_at=BASE_TIME,
        )
    )

    repository.clear()

    assert repository.list() == ()


# --- Service: queue --------------------------------------------------------


def test_queue_with_no_alerts_returns_empty_tuple() -> None:
    harness = Harness()

    for index in range(5):
        harness.execution_repository.save(
            make_success_record(f"job-{index}", offset_minutes=index)
        )

    notifications = harness.service.queue(STRICT_POLICY)

    assert notifications == ()


def test_queue_creates_one_notification_per_alert() -> None:
    harness = Harness()

    harness.execution_repository.save(make_failed_record("job-1"))

    notifications = harness.service.queue(STRICT_POLICY)

    assert len(notifications) == 2
    assert set(harness.repository.list()) == set(notifications)


def test_queue_ignores_duplicate_alert_ids() -> None:
    # A fixed alert uuid_factory makes every generated alert (within
    # one call, and across separate calls) share the same alert_id,
    # simulating the same underlying violation being seen repeatedly.
    harness = Harness(alert_uuid_factory=lambda: "fixed-alert-id")

    harness.execution_repository.save(make_failed_record("job-1"))

    first = harness.service.queue(STRICT_POLICY)

    second = harness.service.queue(STRICT_POLICY)

    assert len(first) == 1
    assert second == ()
    assert len(harness.repository.list()) == 1


def test_delete_removes_notification() -> None:
    harness = Harness()

    harness.execution_repository.save(make_failed_record("job-1"))

    notifications = harness.service.queue(STRICT_POLICY)

    target = notifications[0]

    harness.service.delete(target.notification_id)

    assert harness.service.get(target.notification_id) is None


def test_delete_missing_raises_key_error() -> None:
    harness = Harness()

    with pytest.raises(KeyError):
        harness.service.delete("missing")


def test_clear_empties_repository() -> None:
    harness = Harness()

    harness.execution_repository.save(make_failed_record("job-1"))

    harness.service.queue(STRICT_POLICY)

    harness.service.clear()

    assert harness.service.list() == ()


def test_get_returns_none_for_missing_notification() -> None:
    harness = Harness()

    assert harness.service.get("missing") is None


# --- SQLite repository -----------------------------------------------------


def test_sqlite_repository_persists_and_survives_reload(tmp_path) -> None:
    from backend.persistence.sqlite_database import (
        SQLiteDatabase,
        SQLiteDatabaseConfig,
    )

    database_path = tmp_path / "notifications.db"

    database = SQLiteDatabase(
        SQLiteDatabaseConfig(database_path=database_path)
    )

    repository = SQLiteGovernanceIntegrityNotificationRepository(
        database
    )

    repository.save(
        GovernanceIntegrityNotification(
            notification_id="notification-1",
            alert_id="alert-1",
            severity=GovernanceIntegrityAlertSeverity.CRITICAL,
            message="boom",
            status=GovernanceIntegrityNotificationStatus.PENDING,
            created_at=BASE_TIME,
        )
    )

    reloaded_database = SQLiteDatabase(
        SQLiteDatabaseConfig(database_path=database_path)
    )

    reloaded_repository = SQLiteGovernanceIntegrityNotificationRepository(
        reloaded_database
    )

    notification = reloaded_repository.get("notification-1")

    assert notification is not None
    assert notification.alert_id == "alert-1"
    assert (
        notification.severity
        is GovernanceIntegrityAlertSeverity.CRITICAL
    )


# --- Runtime ---------------------------------------------------------------


def test_runtime_builds_working_notification_service_over_sqlite(
    tmp_path,
) -> None:
    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "notifications-runtime.db"
        )
    )

    collection_service = runtime.build_integrity_audit_collection_service()
    collection_service.create("release-v1")

    service = runtime.build_integrity_notification_service()

    notifications = service.queue(STRICT_POLICY)

    assert len(notifications) >= 1

    reloaded_runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "notifications-runtime.db"
        )
    )

    reloaded_service = (
        reloaded_runtime.build_integrity_notification_service()
    )

    assert len(reloaded_service.list()) == len(notifications)
