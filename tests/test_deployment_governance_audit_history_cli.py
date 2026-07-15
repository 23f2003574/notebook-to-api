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

    _render_human(result, stdout=stdout)

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

    _render_human(result, stdout=stdout)

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

    _render_human(result, stdout=stdout)

    assert (
        "No matching integrity audits found."
        in stdout.getvalue()
    )


def test_audit_history_json_output_is_valid_json() -> None:
    result = make_history_result()

    stdout = StringIO()

    _render_json(result, stdout=stdout)

    payload = json.loads(stdout.getvalue())

    assert "summary" in payload
    assert "records" in payload


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
