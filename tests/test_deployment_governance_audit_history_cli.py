from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from io import StringIO

import pytest

from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
)
from backend.observability.deployment_governance_audit_history_cli import (
    GovernanceAuditHistoryOptions,
    parse_governance_audit_timestamp,
    _render_failure,
    _render_human,
    _render_json,
)
from backend.observability.deployment_governance_audit_history_service import (
    GovernanceIntegrityAuditHistoryResult,
    GovernanceIntegrityAuditHistorySummary,
)
from backend.observability.deployment_governance_audit_regression import (
    GovernanceIntegrityRegressionSnapshot,
    GovernanceIntegrityRegressionStatus,
)
from backend.observability.deployment_governance_audit_trends import (
    GovernanceIntegrityAuditTrendDirection,
    GovernanceIntegrityAuditTrendSnapshot,
)


BASE_TIME = datetime(
    2026,
    7,
    15,
    16,
    0,
    0,
    tzinfo=timezone.utc,
)


def make_record(
    *,
    audit_id: str,
    invalid_records: int = 0,
) -> GovernanceIntegrityAuditRecord:
    return GovernanceIntegrityAuditRecord(
        audit_id=audit_id,
        backend="sqlite",
        started_at=BASE_TIME,
        completed_at=BASE_TIME + timedelta(seconds=2),
        outcome=(
            GovernanceIntegrityAuditOutcome.HEALTHY
            if invalid_records == 0
            else GovernanceIntegrityAuditOutcome.UNHEALTHY
        ),
        total_records=10,
        valid_records=10 - invalid_records,
        invalid_records=invalid_records,
        integrity_mismatches=invalid_records,
        missing_integrity_metadata=0,
        invalid_integrity_metadata=0,
        invalid_persisted_records=0,
    )


def make_history_result() -> GovernanceIntegrityAuditHistoryResult:
    record = make_record(audit_id="audit-1")

    return GovernanceIntegrityAuditHistoryResult(
        summary=GovernanceIntegrityAuditHistorySummary(
            total_audits=1,
            healthy_audits=1,
            unhealthy_audits=0,
            latest_audit=record,
        ),
        records=(record,),
    )


def test_audit_history_human_output_contains_records() -> None:
    result = make_history_result()

    stdout = StringIO()

    _render_human(result, trend=None, regression=None, stdout=stdout)

    output = stdout.getvalue()

    assert "Deployment Governance Integrity Audit History" in output
    assert "Recorded audits:" in output
    assert "Outcome: HEALTHY" in output


def test_audit_history_human_output_shows_failure_breakdown() -> None:
    record = make_record(audit_id="audit-unhealthy", invalid_records=2)

    result = GovernanceIntegrityAuditHistoryResult(
        summary=GovernanceIntegrityAuditHistorySummary(
            total_audits=1,
            healthy_audits=0,
            unhealthy_audits=1,
            latest_audit=record,
        ),
        records=(record,),
    )

    stdout = StringIO()

    _render_human(result, trend=None, regression=None, stdout=stdout)

    output = stdout.getvalue()

    assert "Outcome: UNHEALTHY" in output
    assert "Failure breakdown:" in output
    assert "Integrity mismatches: 2" in output


def test_audit_history_human_output_handles_no_matches() -> None:
    result = GovernanceIntegrityAuditHistoryResult(
        summary=GovernanceIntegrityAuditHistorySummary(
            total_audits=0,
            healthy_audits=0,
            unhealthy_audits=0,
            latest_audit=None,
        ),
        records=(),
    )

    stdout = StringIO()

    _render_human(result, trend=None, regression=None, stdout=stdout)

    assert (
        "No matching integrity audits found."
        in stdout.getvalue()
    )


def test_audit_history_json_output_is_valid_json() -> None:
    result = make_history_result()

    stdout = StringIO()

    _render_json(result, trend=None, regression=None, stdout=stdout)

    payload = json.loads(stdout.getvalue())

    assert "summary" in payload
    assert "records" in payload
    assert "trend" not in payload


def make_trend_snapshot() -> GovernanceIntegrityAuditTrendSnapshot:
    return GovernanceIntegrityAuditTrendSnapshot(
        sample_size=4,
        healthy_audits=3,
        unhealthy_audits=1,
        health_rate=0.75,
        failure_rate=0.25,
        current_outcome=GovernanceIntegrityAuditOutcome.UNHEALTHY,
        previous_outcome=GovernanceIntegrityAuditOutcome.HEALTHY,
        current_streak=1,
        direction=GovernanceIntegrityAuditTrendDirection.DEGRADING,
    )


def test_audit_history_human_output_renders_trend_section() -> None:
    result = make_history_result()

    stdout = StringIO()

    _render_human(result, trend=make_trend_snapshot(), regression=None, stdout=stdout)

    output = stdout.getvalue()

    assert "Trend Analysis" in output
    assert "Direction: DEGRADING" in output
    assert "Current streak: 1" in output
    assert "Health rate: 75.00%" in output
    assert "Failure rate: 25.00%" in output


def test_audit_history_human_output_renders_trend_for_empty_results() -> None:
    result = GovernanceIntegrityAuditHistoryResult(
        summary=GovernanceIntegrityAuditHistorySummary(
            total_audits=4,
            healthy_audits=3,
            unhealthy_audits=1,
            latest_audit=None,
        ),
        records=(),
    )

    stdout = StringIO()

    _render_human(result, trend=make_trend_snapshot(), regression=None, stdout=stdout)

    output = stdout.getvalue()

    assert "No matching integrity audits found." in output
    assert "Trend Analysis" in output


def test_audit_history_human_output_omits_trend_section_by_default() -> None:
    result = make_history_result()

    stdout = StringIO()

    _render_human(result, trend=None, regression=None, stdout=stdout)

    assert "Trend Analysis" not in stdout.getvalue()


def test_audit_history_json_output_includes_trend_when_requested() -> None:
    result = make_history_result()

    stdout = StringIO()

    _render_json(result, trend=make_trend_snapshot(), regression=None, stdout=stdout)

    payload = json.loads(stdout.getvalue())

    assert "trend" in payload
    assert payload["trend"]["direction"] == "degrading"
    assert "summary" in payload
    assert "records" in payload


def test_audit_history_human_output_insufficient_data_trend() -> None:
    result = make_history_result()

    trend = GovernanceIntegrityAuditTrendSnapshot(
        sample_size=0,
        healthy_audits=0,
        unhealthy_audits=0,
        health_rate=None,
        failure_rate=None,
        current_outcome=None,
        previous_outcome=None,
        current_streak=0,
        direction=(
            GovernanceIntegrityAuditTrendDirection.INSUFFICIENT_DATA
        ),
    )

    stdout = StringIO()

    _render_human(result, trend=trend, regression=None, stdout=stdout)

    output = stdout.getvalue()

    assert "Direction: INSUFFICIENT_DATA" in output
    assert "Current outcome: not available" in output
    assert "Health rate: not available" in output
    assert "Failure rate: not available" in output


def make_regression_snapshot() -> GovernanceIntegrityRegressionSnapshot:
    return GovernanceIntegrityRegressionSnapshot(
        status=GovernanceIntegrityRegressionStatus.REGRESSION,
        regression_detected=True,
        current_audit_id="current",
        baseline_audit_id="baseline",
        current_outcome=GovernanceIntegrityAuditOutcome.UNHEALTHY,
        baseline_outcome=GovernanceIntegrityAuditOutcome.HEALTHY,
        current_invalid_records=3,
        baseline_invalid_records=0,
        invalid_record_delta=3,
        integrity_mismatch_delta=2,
        missing_integrity_metadata_delta=0,
        invalid_integrity_metadata_delta=0,
        invalid_persisted_records_delta=1,
        newly_introduced_failure_categories=(
            "integrity_mismatches",
            "invalid_persisted_records",
        ),
    )


def test_audit_history_human_output_renders_regression_section() -> None:
    result = make_history_result()

    stdout = StringIO()

    _render_human(
        result,
        trend=None,
        regression=make_regression_snapshot(),
        stdout=stdout,
    )

    output = stdout.getvalue()

    assert "Regression Analysis" in output
    assert "Status: REGRESSION" in output
    assert "Regression detected: yes" in output
    assert "Baseline audit: baseline" in output
    assert "Current audit: current" in output
    assert "Invalid record delta: +3" in output
    assert "New failure categories:" in output
    assert "  integrity_mismatches" in output
    assert "  invalid_persisted_records" in output


def test_audit_history_human_output_omits_regression_section_by_default() -> None:
    result = make_history_result()

    stdout = StringIO()

    _render_human(result, trend=None, regression=None, stdout=stdout)

    assert "Regression Analysis" not in stdout.getvalue()


def test_audit_history_human_output_regression_persistent_failure() -> None:
    result = make_history_result()

    snapshot = GovernanceIntegrityRegressionSnapshot(
        status=GovernanceIntegrityRegressionStatus.PERSISTENT_FAILURE,
        regression_detected=False,
        current_audit_id="current",
        baseline_audit_id="baseline",
        current_outcome=GovernanceIntegrityAuditOutcome.UNHEALTHY,
        baseline_outcome=GovernanceIntegrityAuditOutcome.UNHEALTHY,
        current_invalid_records=1,
        baseline_invalid_records=3,
        invalid_record_delta=-2,
        integrity_mismatch_delta=-2,
        missing_integrity_metadata_delta=0,
        invalid_integrity_metadata_delta=0,
        invalid_persisted_records_delta=0,
        newly_introduced_failure_categories=(),
    )

    stdout = StringIO()

    _render_human(result, trend=None, regression=snapshot, stdout=stdout)

    output = stdout.getvalue()

    assert "Status: PERSISTENT_FAILURE" in output
    assert "Regression detected: no" in output
    assert "Invalid record delta: -2" in output
    assert "New failure categories:" not in output


def test_audit_history_human_output_regression_no_history() -> None:
    result = make_history_result()

    snapshot = GovernanceIntegrityRegressionSnapshot(
        status=GovernanceIntegrityRegressionStatus.NO_HISTORY,
        regression_detected=False,
        current_audit_id=None,
        baseline_audit_id=None,
        current_outcome=None,
        baseline_outcome=None,
        current_invalid_records=None,
        baseline_invalid_records=None,
        invalid_record_delta=None,
        integrity_mismatch_delta=None,
        missing_integrity_metadata_delta=None,
        invalid_integrity_metadata_delta=None,
        invalid_persisted_records_delta=None,
        newly_introduced_failure_categories=(),
    )

    stdout = StringIO()

    _render_human(result, trend=None, regression=snapshot, stdout=stdout)

    output = stdout.getvalue()

    assert "Status: NO_HISTORY" in output
    assert "Regression detected: no" in output
    assert "Baseline audit:" not in output
    assert "Current audit:" not in output


def test_audit_history_json_output_includes_regression_when_requested() -> None:
    result = make_history_result()

    stdout = StringIO()

    _render_json(
        result,
        trend=None,
        regression=make_regression_snapshot(),
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())

    assert "regression" in payload
    assert payload["regression"]["status"] == "regression"
    assert payload["regression"]["regression_detected"] is True


def test_audit_history_json_output_omits_regression_by_default() -> None:
    result = make_history_result()

    stdout = StringIO()

    _render_json(result, trend=None, regression=None, stdout=stdout)

    payload = json.loads(stdout.getvalue())

    assert "regression" not in payload


def test_audit_history_json_output_supports_trend_and_regression_together() -> None:
    result = make_history_result()

    stdout = StringIO()

    _render_json(
        result,
        trend=make_trend_snapshot(),
        regression=make_regression_snapshot(),
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())

    assert set(payload.keys()) == {
        "summary",
        "records",
        "trend",
        "regression",
    }


def test_render_failure_human() -> None:
    stderr = StringIO()

    _render_failure(
        RuntimeError("simulated failure"),
        json_output=False,
        stderr=stderr,
    )

    output = stderr.getvalue()

    assert "could not be inspected" in output
    assert "simulated failure" in output


def test_render_failure_json() -> None:
    stderr = StringIO()

    _render_failure(
        RuntimeError("simulated failure"),
        json_output=True,
        stderr=stderr,
    )

    payload = json.loads(stderr.getvalue())

    assert payload["status"] == "query_failed"
    assert payload["error"] == "simulated failure"
    assert payload["exit_code"] == 2


def test_parse_governance_audit_timestamp_none() -> None:
    assert parse_governance_audit_timestamp(None) is None


def test_parse_governance_audit_timestamp_valid() -> None:
    parsed = parse_governance_audit_timestamp(
        "2026-07-15T00:00:00+00:00"
    )

    assert parsed == datetime(
        2026, 7, 15, 0, 0, 0, tzinfo=timezone.utc
    )


def test_parse_governance_audit_timestamp_rejects_empty() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        parse_governance_audit_timestamp("   ")


def test_parse_governance_audit_timestamp_rejects_invalid() -> None:
    with pytest.raises(ValueError, match="must be valid ISO-8601"):
        parse_governance_audit_timestamp("not-a-timestamp")


def test_options_reject_started_after_later_than_started_before() -> None:
    with pytest.raises(
        ValueError,
        match="started_at_or_after must not be later",
    ):
        GovernanceAuditHistoryOptions(
            started_at_or_after=BASE_TIME + timedelta(days=1),
            started_at_or_before=BASE_TIME,
        )


def test_options_reject_non_positive_limit() -> None:
    with pytest.raises(
        ValueError, match="limit must be greater than zero"
    ):
        GovernanceAuditHistoryOptions(limit=0)


def test_options_reject_empty_backend() -> None:
    with pytest.raises(
        ValueError, match="backend must not be empty"
    ):
        GovernanceAuditHistoryOptions(backend="   ")


def test_options_reject_non_positive_trend_window() -> None:
    with pytest.raises(
        ValueError, match="trend_window must be greater than zero"
    ):
        GovernanceAuditHistoryOptions(trend_window=0)


def test_options_accept_include_regression_flag() -> None:
    options = GovernanceAuditHistoryOptions(include_regression=True)

    assert options.include_regression is True
