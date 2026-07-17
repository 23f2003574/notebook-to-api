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
    GovernanceIntegrityExecutionAlert,
    GovernanceIntegrityExecutionAlertService,
)
from backend.observability.deployment_governance_execution_metrics import (
    GovernanceIntegrityExecutionMetricsService,
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


def make_success_record(
    job_id: str,
    *,
    template_name: str = "nightly",
    duration_ms: float = 100.0,
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
        finished_at=started_at + timedelta(milliseconds=duration_ms),
    )


def make_failed_record(
    job_id: str,
    *,
    template_name: str = "nightly",
    duration_ms: float = 100.0,
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
        finished_at=started_at + timedelta(milliseconds=duration_ms),
    )


class Harness:
    def __init__(self, *, clock=None, uuid_factory=None) -> None:
        self.execution_repository = (
            InMemoryGovernanceIntegrityAuditExecutionRepository()
        )

        self.metrics_service = GovernanceIntegrityExecutionMetricsService(
            self.execution_repository
        )

        self.service = GovernanceIntegrityExecutionAlertService(
            self.metrics_service,
            clock=clock,
            uuid_factory=uuid_factory,
        )


# --- Model: GovernanceIntegrityExecutionAlert -----------------------------


def test_alert_rejects_empty_alert_id() -> None:
    with pytest.raises(ValueError, match="alert_id must not be empty"):
        GovernanceIntegrityExecutionAlert(
            alert_id="  ",
            severity=GovernanceIntegrityAlertSeverity.WARNING,
            message="boom",
            created_at=BASE_TIME,
        )


def test_alert_rejects_naive_created_at() -> None:
    with pytest.raises(
        ValueError, match="created_at must be timezone-aware"
    ):
        GovernanceIntegrityExecutionAlert(
            alert_id="alert-1",
            severity=GovernanceIntegrityAlertSeverity.WARNING,
            message="boom",
            created_at=datetime(2026, 7, 15, 23, 0, 0),
        )


# --- Model: GovernanceIntegrityAlertPolicy --------------------------------


def test_policy_rejects_out_of_range_minimum_success_rate() -> None:
    with pytest.raises(
        ValueError,
        match="minimum_success_rate must be between 0 and 100",
    ):
        GovernanceIntegrityAlertPolicy(
            minimum_success_rate=150.0,
            maximum_failure_rate=100.0,
            maximum_average_duration_ms=1000.0,
        )


def test_policy_rejects_out_of_range_maximum_failure_rate() -> None:
    with pytest.raises(
        ValueError,
        match="maximum_failure_rate must be between 0 and 100",
    ):
        GovernanceIntegrityAlertPolicy(
            minimum_success_rate=0.0,
            maximum_failure_rate=-1.0,
            maximum_average_duration_ms=1000.0,
        )


def test_policy_rejects_non_positive_duration() -> None:
    with pytest.raises(
        ValueError,
        match="maximum_average_duration_ms must be greater than zero",
    ):
        GovernanceIntegrityAlertPolicy(
            minimum_success_rate=0.0,
            maximum_failure_rate=100.0,
            maximum_average_duration_ms=0.0,
        )


# --- Service: generate -----------------------------------------------------


def test_healthy_metrics_produce_no_alerts() -> None:
    harness = Harness()

    for index in range(10):
        harness.execution_repository.save(
            make_success_record(
                f"job-{index}", offset_minutes=index
            )
        )

    policy = GovernanceIntegrityAlertPolicy(
        minimum_success_rate=90.0,
        maximum_failure_rate=10.0,
        maximum_average_duration_ms=1000.0,
    )

    alerts = harness.service.generate(policy)

    assert alerts == ()


def test_low_success_rate_generates_warning() -> None:
    harness = Harness()

    for index in range(72):
        harness.execution_repository.save(
            make_success_record(
                f"success-{index}", offset_minutes=index
            )
        )

    for index in range(28):
        harness.execution_repository.save(
            make_failed_record(
                f"failed-{index}", offset_minutes=100 + index
            )
        )

    policy = GovernanceIntegrityAlertPolicy(
        minimum_success_rate=90.0,
        maximum_failure_rate=100.0,
        maximum_average_duration_ms=1_000_000.0,
    )

    alerts = harness.service.generate(policy)

    assert len(alerts) == 1
    assert alerts[0].severity is GovernanceIntegrityAlertSeverity.WARNING


def test_slow_execution_alert_mentions_average_runtime() -> None:
    harness = Harness()

    harness.execution_repository.save(
        make_success_record("job-1", duration_ms=500.0)
    )

    policy = GovernanceIntegrityAlertPolicy(
        minimum_success_rate=0.0,
        maximum_failure_rate=100.0,
        maximum_average_duration_ms=50.0,
    )

    alerts = harness.service.generate(policy)

    assert len(alerts) == 1
    assert "average runtime" in alerts[0].message.lower()


def test_multiple_violations_generate_multiple_alerts() -> None:
    harness = Harness()

    harness.execution_repository.save(
        make_failed_record("job-1", duration_ms=500.0)
    )

    policy = GovernanceIntegrityAlertPolicy(
        minimum_success_rate=90.0,
        maximum_failure_rate=0.0,
        maximum_average_duration_ms=50.0,
    )

    alerts = harness.service.generate(policy)

    assert len(alerts) >= 2


def test_generate_uses_injected_uuid_factory() -> None:
    ids = iter(["fixed-alert-id"])

    harness = Harness(uuid_factory=lambda: next(ids))

    harness.execution_repository.save(
        make_failed_record("job-1")
    )

    policy = GovernanceIntegrityAlertPolicy(
        minimum_success_rate=90.0,
        maximum_failure_rate=100.0,
        maximum_average_duration_ms=1_000_000.0,
    )

    alerts = harness.service.generate(policy)

    assert len(alerts) == 1
    assert alerts[0].alert_id == "fixed-alert-id"


# --- Service: generate for template ---------------------------------------


def test_generate_for_template_only_considers_matching_records() -> None:
    harness = Harness()

    harness.execution_repository.save(
        make_failed_record("job-1", template_name="nightly")
    )
    harness.execution_repository.save(
        make_success_record("job-2", template_name="weekly")
    )
    harness.execution_repository.save(
        make_success_record("job-3", template_name="weekly")
    )

    policy = GovernanceIntegrityAlertPolicy(
        minimum_success_rate=90.0,
        maximum_failure_rate=0.0,
        maximum_average_duration_ms=1_000_000.0,
    )

    nightly_alerts = harness.service.generate(
        policy, template_name="nightly"
    )
    weekly_alerts = harness.service.generate(
        policy, template_name="weekly"
    )

    assert len(nightly_alerts) >= 1
    assert weekly_alerts == ()
