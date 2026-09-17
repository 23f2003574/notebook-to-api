from __future__ import annotations

import json
from io import StringIO

from backend.observability.deployment_governance_audit_execution_queue_cli import (
    run_deployment_governance_audit_queue_clear,
    run_deployment_governance_audit_queue_delete,
    run_deployment_governance_audit_queue_enqueue,
    run_deployment_governance_audit_queue_enqueue_due,
    run_deployment_governance_audit_queue_list,
    run_deployment_governance_audit_queue_show,
)

# NOTE: the execution queue has no SQLite persistence (intentionally
# deferred, see deployment_governance_audit_execution_queue.py), so each
# run_deployment_governance_audit_queue_* call bootstraps its own fresh,
# empty in-memory queue. These tests exercise each command's
# self-contained behavior rather than assuming queue state survives
# across separate calls -- schedules/templates/collections still persist
# via SQLite and are set up per test as needed.


def setup_env(monkeypatch, tmp_path, name: str) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / name),
    )


def create_schedule(tmp_path, name: str, schedule_name: str = "nightly"):
    from backend.observability.deployment_governance_persistence import (
        DeploymentGovernancePersistenceConfig,
        build_deployment_governance_persistence,
    )
    from backend.observability.deployment_governance_audit_report_templates import (
        GovernanceIntegrityAuditReportSource,
    )
    from backend.observability.deployment_governance_audit_report_schedule import (
        GovernanceIntegrityReportScheduleFrequency,
    )

    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(tmp_path / name)
    )

    collection_service = runtime.build_integrity_audit_collection_service()
    collection_service.create(f"{schedule_name}-collection")

    template_service = runtime.build_integrity_audit_report_template_service()
    template_service.create(
        schedule_name, f"{schedule_name} Report",
        GovernanceIntegrityAuditReportSource.COLLECTION,
        f"{schedule_name}-collection", "json",
    )

    schedule_service = runtime.build_integrity_audit_report_schedule_service()
    schedule_service.create(
        schedule_name, schedule_name,
        GovernanceIntegrityReportScheduleFrequency.DAILY,
    )

    return schedule_name


def test_enqueue_job(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "queue-enqueue.db")

    create_schedule(tmp_path, "queue-enqueue.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_queue_enqueue(
        schedule_name="nightly", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "Job queued" in output
    assert "Schedule: nightly" in output
    assert "Status: pending" in output


def test_enqueue_rejects_missing_schedule(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "queue-enqueue-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_queue_enqueue(
        schedule_name="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2


def test_enqueue_rejects_disabled_schedule(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "queue-enqueue-disabled.db")

    from backend.observability.deployment_governance_persistence import (
        DeploymentGovernancePersistenceConfig,
        build_deployment_governance_persistence,
    )

    create_schedule(tmp_path, "queue-enqueue-disabled.db")

    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "queue-enqueue-disabled.db"
        )
    )
    runtime.build_integrity_audit_report_schedule_service().disable(
        "nightly"
    )

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_queue_enqueue(
        schedule_name="nightly", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2


def test_enqueue_json_output(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "queue-enqueue-json.db")

    create_schedule(tmp_path, "queue-enqueue-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_queue_enqueue(
        schedule_name="nightly",
        json_output=True,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["schedule_name"] == "nightly"
    assert payload["status"] == "pending"


def test_enqueue_due(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "queue-enqueue-due.db")

    create_schedule(tmp_path, "queue-enqueue-due.db", "nightly")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_queue_enqueue_due(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert len(payload) == 1
    assert payload[0]["schedule_name"] == "nightly"


def test_enqueue_due_empty_when_no_schedules(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "queue-enqueue-due-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_queue_enqueue_due(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue()) == []


def test_list_empty(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "queue-list-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_queue_list(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "Execution Queue" in stdout.getvalue()
    assert "No governance audit execution jobs" in stdout.getvalue()


def test_show_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "queue-show-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_queue_show(
        job_id="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2
    assert "could not be completed" in stderr.getvalue()


def test_delete_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "queue-delete-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_queue_delete(
        job_id="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2


def test_clear_queue(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "queue-clear.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_queue_clear(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "cleared" in stdout.getvalue()


def test_clear_json_output(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "queue-clear-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_queue_clear(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue())["status"] == "cleared"
