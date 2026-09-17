from __future__ import annotations

import json
from io import StringIO

from backend.observability.deployment_governance_audit_report_schedule import (
    GovernanceIntegrityReportScheduleFrequency,
)
from backend.observability.deployment_governance_audit_report_schedule_cli import (
    run_deployment_governance_audit_report_schedule_create,
    run_deployment_governance_audit_report_schedule_delete,
    run_deployment_governance_audit_report_schedule_disable,
    run_deployment_governance_audit_report_schedule_enable,
    run_deployment_governance_audit_report_schedule_list,
    run_deployment_governance_audit_report_schedule_show,
)


def setup_env(monkeypatch, tmp_path, name: str) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / name),
    )


def create_template(tmp_path, name: str, template_name: str = "release"):
    from backend.observability.deployment_governance_persistence import (
        DeploymentGovernancePersistenceConfig,
        build_deployment_governance_persistence,
    )
    from backend.observability.deployment_governance_audit_report_templates import (
        GovernanceIntegrityAuditReportSource,
    )

    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(tmp_path / name)
    )

    collection_service = runtime.build_integrity_audit_collection_service()
    collection_service.create(f"{template_name}-collection")

    template_service = runtime.build_integrity_audit_report_template_service()
    template_service.create(
        template_name, f"{template_name} Report",
        GovernanceIntegrityAuditReportSource.COLLECTION,
        f"{template_name}-collection", "json",
    )

    return template_name


def test_create_schedule(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "schedule-create.db")

    create_template(tmp_path, "schedule-create.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_report_schedule_create(
        name="nightly",
        template_name="release",
        frequency=GovernanceIntegrityReportScheduleFrequency.DAILY,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "Schedule created" in output
    assert "Name: nightly" in output
    assert "Template: release" in output
    assert "Frequency: daily" in output
    assert "Status: enabled" in output


def test_create_rejects_missing_template(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "schedule-create-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_report_schedule_create(
        name="nightly",
        template_name="missing",
        frequency=GovernanceIntegrityReportScheduleFrequency.DAILY,
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2


def test_create_json_output(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "schedule-create-json.db")

    create_template(tmp_path, "schedule-create-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_report_schedule_create(
        name="nightly",
        template_name="release",
        frequency=GovernanceIntegrityReportScheduleFrequency.DAILY,
        json_output=True,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["name"] == "nightly"
    assert payload["template_name"] == "release"
    assert payload["frequency"] == "daily"
    assert payload["enabled"] is True


def test_enable_disable(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "schedule-enable-disable.db")

    create_template(tmp_path, "schedule-enable-disable.db")

    run_deployment_governance_audit_report_schedule_create(
        name="nightly",
        template_name="release",
        frequency=GovernanceIntegrityReportScheduleFrequency.DAILY,
        stdout=StringIO(),
        stderr=StringIO(),
    )

    disable_stdout = StringIO()

    exit_code = run_deployment_governance_audit_report_schedule_disable(
        name="nightly", stdout=disable_stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "disabled" in disable_stdout.getvalue()

    show_stdout = StringIO()

    run_deployment_governance_audit_report_schedule_show(
        name="nightly", stdout=show_stdout, stderr=StringIO()
    )

    assert "Status: disabled" in show_stdout.getvalue()

    enable_stdout = StringIO()

    run_deployment_governance_audit_report_schedule_enable(
        name="nightly", stdout=enable_stdout, stderr=StringIO()
    )

    assert "enabled" in enable_stdout.getvalue()


def test_enable_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "schedule-enable-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_report_schedule_enable(
        name="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2


def test_list_schedules(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "schedule-list.db")

    create_template(tmp_path, "schedule-list.db")

    run_deployment_governance_audit_report_schedule_create(
        name="nightly",
        template_name="release",
        frequency=GovernanceIntegrityReportScheduleFrequency.DAILY,
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_report_schedule_list(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "Schedules" in output
    assert "nightly" in output


def test_list_empty(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "schedule-list-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_report_schedule_list(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "No governance audit report schedules" in stdout.getvalue()


def test_delete_schedule(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "schedule-delete.db")

    create_template(tmp_path, "schedule-delete.db")

    run_deployment_governance_audit_report_schedule_create(
        name="nightly",
        template_name="release",
        frequency=GovernanceIntegrityReportScheduleFrequency.DAILY,
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_report_schedule_delete(
        name="nightly", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "deleted" in stdout.getvalue()

    show_exit_code = run_deployment_governance_audit_report_schedule_show(
        name="nightly", stdout=StringIO(), stderr=StringIO()
    )

    assert show_exit_code == 2


def test_delete_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "schedule-delete-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_report_schedule_delete(
        name="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2
