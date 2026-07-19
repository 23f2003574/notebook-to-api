from __future__ import annotations

import json
from io import StringIO

from backend.observability.deployment_governance_metrics_cli import (
    run_deployment_governance_metrics,
    run_deployment_governance_metrics_export,
    run_deployment_governance_metrics_reload,
    run_deployment_governance_metrics_reset,
)
from backend.observability.deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


def setup_env(monkeypatch, tmp_path, name: str) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / name),
    )


def test_metrics_on_empty_history(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "Governance Delivery Metrics" in output
    assert "Total Dispatches: 0" in output


def test_metrics_json_output(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-empty-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["total_dispatches"] == 0
    assert payload["successful_dispatches"] == 0
    assert payload["failed_dispatches"] == 0
    assert payload["retry_dispatches"] == 0
    assert payload["average_duration_ms"] == 0.0


def test_metrics_reflects_persisted_snapshot(
    monkeypatch, tmp_path
) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-persisted.db")

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    service = runtime.build_integrity_metrics_service()

    service.record_success(100.0)
    service.record_failure(50.0)
    service.record_retry()

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["total_dispatches"] == 2
    assert payload["successful_dispatches"] == 1
    assert payload["failed_dispatches"] == 1
    assert payload["retry_dispatches"] == 1
    assert payload["average_duration_ms"] == 75.0


def test_export_reads_persisted_snapshot(
    monkeypatch, tmp_path
) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-export.db")

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    runtime.build_integrity_metrics_service().record_success(100.0)

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_export(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["successful_dispatches"] == 1


def test_export_does_not_clobber_persisted_data(
    monkeypatch, tmp_path
) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-export-safe.db")

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    runtime.build_integrity_metrics_service().record_success(100.0)

    run_deployment_governance_metrics_export(
        stdout=StringIO(), stderr=StringIO()
    )

    reloaded_runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    persisted = (
        reloaded_runtime.build_integrity_metrics_repository().load()
    )

    assert persisted is not None
    assert persisted.successful_dispatches == 1


def test_reload_reads_persisted_snapshot(
    monkeypatch, tmp_path
) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-reload.db")

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    runtime.build_integrity_metrics_service().record_failure(30.0)

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_reload(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["failed_dispatches"] == 1


def test_reset_clears_persisted_snapshot(
    monkeypatch, tmp_path
) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-reset.db")

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    runtime.build_integrity_metrics_service().record_success(100.0)

    exit_code = run_deployment_governance_metrics_reset(
        stdout=StringIO(), stderr=StringIO()
    )

    assert exit_code == 0

    reloaded_runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    assert (
        reloaded_runtime.build_integrity_metrics_repository().load()
        is None
    )
