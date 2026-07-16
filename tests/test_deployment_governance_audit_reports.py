from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.observability.deployment_governance_audit_collections import (
    GovernanceIntegrityAuditCollectionService,
    InMemoryGovernanceIntegrityAuditCollectionRepository,
)
from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
    InMemoryGovernanceIntegrityAuditHistoryRepository,
)
from backend.observability.deployment_governance_audit_reports import (
    GovernanceIntegrityAuditReport,
    GovernanceIntegrityAuditReportService,
)
from backend.observability.deployment_governance_audit_statistics import (
    GovernanceIntegrityAuditStatisticsService,
)
from backend.observability.deployment_governance_persistence import (
    DeploymentGovernancePersistenceConfig,
    build_deployment_governance_persistence,
)


BASE_TIME = datetime(2026, 7, 15, 23, 0, 0, tzinfo=timezone.utc)


def make_record(
    *,
    audit_id: str,
    offset_minutes: int = 0,
    healthy: bool = True,
) -> GovernanceIntegrityAuditRecord:
    invalid_records = 0 if healthy else 1

    started_at = BASE_TIME + timedelta(minutes=offset_minutes)

    return GovernanceIntegrityAuditRecord(
        audit_id=audit_id,
        backend="sqlite",
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=2),
        outcome=(
            GovernanceIntegrityAuditOutcome.HEALTHY
            if healthy
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


class Harness:
    def __init__(self, *, clock=None) -> None:
        self.history_repository = (
            InMemoryGovernanceIntegrityAuditHistoryRepository()
        )
        self.collection_repository = (
            InMemoryGovernanceIntegrityAuditCollectionRepository()
        )

        self.collection_service = GovernanceIntegrityAuditCollectionService(
            self.collection_repository, self.history_repository
        )

        self.statistics_service = GovernanceIntegrityAuditStatisticsService(
            self.history_repository
        )

        self.report_service = GovernanceIntegrityAuditReportService(
            self.history_repository,
            self.collection_repository,
            self.statistics_service,
            clock=clock,
        )


# --- Model -------------------------------------------------------------


def test_report_rejects_empty_title() -> None:
    harness = Harness()

    harness.history_repository.save(make_record(audit_id="A"))

    statistics = harness.statistics_service.calculate()

    with pytest.raises(ValueError, match="title must not be empty"):
        GovernanceIntegrityAuditReport(
            title="  ",
            generated_at=BASE_TIME,
            audits=(),
            statistics=statistics,
        )


def test_report_rejects_naive_generated_at() -> None:
    statistics = GovernanceIntegrityAuditStatisticsService(
        InMemoryGovernanceIntegrityAuditHistoryRepository()
    ).calculate()

    with pytest.raises(
        ValueError, match="generated_at must be timezone-aware"
    ):
        GovernanceIntegrityAuditReport(
            title="Release v1",
            generated_at=datetime(2026, 7, 15, 23, 0, 0),
            audits=(),
            statistics=statistics,
        )


# --- report_from_audits ----------------------------------------------------


def test_report_from_audits_preserves_requested_order() -> None:
    harness = Harness()

    harness.history_repository.save(make_record(audit_id="A", offset_minutes=0))
    harness.history_repository.save(make_record(audit_id="B", offset_minutes=10))
    harness.history_repository.save(make_record(audit_id="C", offset_minutes=20))

    report = harness.report_service.report_from_audits(
        "Selected", ["A", "C"]
    )

    assert len(report.audits) == 2
    assert [record.audit_id for record in report.audits] == ["A", "C"]


def test_report_from_audits_raises_for_missing_audit() -> None:
    harness = Harness()

    harness.history_repository.save(make_record(audit_id="A"))

    with pytest.raises(LookupError):
        harness.report_service.report_from_audits(
            "Selected", ["A", "missing"]
        )


def test_report_from_audits_statistics_reflect_selection_only() -> None:
    harness = Harness()

    harness.history_repository.save(
        make_record(audit_id="A", offset_minutes=0, healthy=True)
    )
    harness.history_repository.save(
        make_record(audit_id="B", offset_minutes=10, healthy=False)
    )
    harness.history_repository.save(
        make_record(audit_id="C", offset_minutes=20, healthy=True)
    )

    report = harness.report_service.report_from_audits(
        "Healthy only", ["A", "C"]
    )

    assert report.statistics.total_audits == 2
    assert report.statistics.healthy_audits == 2
    assert report.statistics.health_rate == 1.0


# --- report_from_collection ------------------------------------------------


def test_report_from_collection_returns_expected_audits() -> None:
    harness = Harness()

    harness.history_repository.save(make_record(audit_id="A", offset_minutes=0))
    harness.history_repository.save(make_record(audit_id="B", offset_minutes=10))

    harness.collection_service.create("release-v1")
    harness.collection_service.add("release-v1", "A")
    harness.collection_service.add("release-v1", "B")

    report = harness.report_service.report_from_collection("release-v1")

    assert {record.audit_id for record in report.audits} == {"A", "B"}
    assert report.title == "release-v1"


def test_report_from_collection_raises_for_missing_collection() -> None:
    harness = Harness()

    with pytest.raises(LookupError):
        harness.report_service.report_from_collection("missing")


def test_report_from_collection_accepts_title_override() -> None:
    harness = Harness()

    harness.history_repository.save(make_record(audit_id="A"))

    harness.collection_service.create("release-v1")
    harness.collection_service.add("release-v1", "A")

    report = harness.report_service.report_from_collection(
        "release-v1", title="Custom Title"
    )

    assert report.title == "Custom Title"


def test_report_from_empty_collection() -> None:
    harness = Harness()

    harness.collection_service.create("empty")

    report = harness.report_service.report_from_collection("empty")

    assert report.audits == ()
    assert report.statistics.total_audits == 0


# --- Serialization ---------------------------------------------------------


def test_report_to_json_round_trips() -> None:
    harness = Harness()

    harness.history_repository.save(make_record(audit_id="A"))

    report = harness.report_service.report_from_audits(
        "Release v1", ["A"]
    )

    payload = json.loads(report.to_json())

    assert payload["title"] == report.title
    assert len(payload["audits"]) == 1
    assert payload["audits"][0]["audit_id"] == "A"
    assert "statistics" in payload


def test_report_to_json_compact() -> None:
    harness = Harness()

    harness.history_repository.save(make_record(audit_id="A"))

    report = harness.report_service.report_from_audits(
        "Release v1", ["A"]
    )

    compact = report.to_json(pretty=False)

    assert "\n" not in compact
    assert json.loads(compact)["title"] == "Release v1"


def test_report_to_markdown_contains_expected_sections() -> None:
    harness = Harness()

    harness.history_repository.save(make_record(audit_id="A"))
    harness.history_repository.save(
        make_record(audit_id="B", offset_minutes=10)
    )

    report = harness.report_service.report_from_audits(
        "Release v1", ["A", "B"]
    )

    markdown = report.to_markdown()

    assert "# " in markdown
    assert markdown.startswith("# Release v1")
    assert "## Statistics" in markdown
    assert "## Audits" in markdown
    assert "- A" in markdown
    assert "- B" in markdown


def test_report_to_markdown_handles_empty_audits() -> None:
    harness = Harness()

    harness.collection_service.create("empty")

    report = harness.report_service.report_from_collection("empty")

    markdown = report.to_markdown()

    assert "## Statistics" in markdown
    assert "## Audits" in markdown


# --- Runtime ---------------------------------------------------------------


def test_runtime_builds_working_report_service(tmp_path) -> None:
    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "report-runtime.db"
        )
    )

    runtime.audit_history_repository.save(make_record(audit_id="A"))

    service = runtime.build_integrity_audit_report_service()

    report = service.report_from_audits("Release v1", ["A"])

    assert len(report.audits) == 1
