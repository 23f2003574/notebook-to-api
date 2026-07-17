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
from backend.observability.deployment_governance_execution_metrics import (
    GovernanceIntegrityExecutionMetrics,
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


# --- Model -------------------------------------------------------------


def test_metrics_rejects_negative_total_runs() -> None:
    with pytest.raises(
        ValueError, match="execution metrics counts must not be negative"
    ):
        GovernanceIntegrityExecutionMetrics(
            total_runs=-1,
            successful_runs=0,
            failed_runs=0,
            average_duration_ms=0.0,
            success_rate=0.0,
        )


def test_metrics_rejects_mismatched_counts() -> None:
    with pytest.raises(
        ValueError,
        match="successful_runs \\+ failed_runs must equal total_runs",
    ):
        GovernanceIntegrityExecutionMetrics(
            total_runs=5,
            successful_runs=3,
            failed_runs=1,
            average_duration_ms=0.0,
            success_rate=0.6,
        )


def test_metrics_rejects_negative_average_duration() -> None:
    with pytest.raises(
        ValueError, match="average_duration_ms must not be negative"
    ):
        GovernanceIntegrityExecutionMetrics(
            total_runs=1,
            successful_runs=1,
            failed_runs=0,
            average_duration_ms=-1.0,
            success_rate=1.0,
        )


def test_metrics_rejects_out_of_range_success_rate() -> None:
    with pytest.raises(
        ValueError, match="success_rate must be between zero and one"
    ):
        GovernanceIntegrityExecutionMetrics(
            total_runs=1,
            successful_runs=1,
            failed_runs=0,
            average_duration_ms=0.0,
            success_rate=1.5,
        )


# --- Service: compute ----------------------------------------------------


def test_compute_on_empty_repository() -> None:
    repository = InMemoryGovernanceIntegrityAuditExecutionRepository()

    service = GovernanceIntegrityExecutionMetricsService(repository)

    metrics = service.compute()

    assert metrics.total_runs == 0
    assert metrics.successful_runs == 0
    assert metrics.failed_runs == 0
    assert metrics.average_duration_ms == 0.0
    assert metrics.success_rate == 0.0


def test_compute_mixed_executions() -> None:
    repository = InMemoryGovernanceIntegrityAuditExecutionRepository()

    repository.save(make_success_record("job-1", offset_minutes=0))
    repository.save(make_success_record("job-2", offset_minutes=1))
    repository.save(make_success_record("job-3", offset_minutes=2))
    repository.save(make_failed_record("job-4", offset_minutes=3))
    repository.save(make_failed_record("job-5", offset_minutes=4))

    service = GovernanceIntegrityExecutionMetricsService(repository)

    metrics = service.compute()

    assert metrics.total_runs == 5
    assert metrics.successful_runs == 3
    assert metrics.failed_runs == 2
    assert metrics.success_rate == pytest.approx(3 / 5)


def test_compute_average_duration() -> None:
    repository = InMemoryGovernanceIntegrityAuditExecutionRepository()

    repository.save(
        make_success_record(
            "job-1", duration_ms=100.0, offset_minutes=0
        )
    )
    repository.save(
        make_failed_record(
            "job-2", duration_ms=300.0, offset_minutes=1
        )
    )

    service = GovernanceIntegrityExecutionMetricsService(repository)

    metrics = service.compute()

    assert metrics.average_duration_ms == pytest.approx(200.0)


# --- Service: compute_for_template ----------------------------------------


def test_compute_for_template_only_includes_matching_records() -> None:
    repository = InMemoryGovernanceIntegrityAuditExecutionRepository()

    repository.save(
        make_success_record(
            "job-1", template_name="nightly", offset_minutes=0
        )
    )
    repository.save(
        make_failed_record(
            "job-2", template_name="nightly", offset_minutes=1
        )
    )
    repository.save(
        make_success_record(
            "job-3", template_name="weekly", offset_minutes=2
        )
    )

    service = GovernanceIntegrityExecutionMetricsService(repository)

    metrics = service.compute_for_template("nightly")

    assert metrics.total_runs == 2
    assert metrics.successful_runs == 1
    assert metrics.failed_runs == 1


def test_compute_for_template_with_no_matches_is_empty() -> None:
    repository = InMemoryGovernanceIntegrityAuditExecutionRepository()

    repository.save(
        make_success_record(
            "job-1", template_name="weekly", offset_minutes=0
        )
    )

    service = GovernanceIntegrityExecutionMetricsService(repository)

    metrics = service.compute_for_template("nightly")

    assert metrics.total_runs == 0
    assert metrics.average_duration_ms == 0.0
    assert metrics.success_rate == 0.0
