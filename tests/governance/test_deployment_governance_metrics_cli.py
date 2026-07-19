from __future__ import annotations

import json
from io import StringIO

from backend.observability.deployment_governance_metrics_cli import (
    run_deployment_governance_metrics,
    run_deployment_governance_metrics_aggregate,
    run_deployment_governance_metrics_bootstrap,
    run_deployment_governance_metrics_health,
    run_deployment_governance_metrics_alerts,
    run_deployment_governance_metrics_alerts_clear,
    run_deployment_governance_metrics_collector_collect,
    run_deployment_governance_metrics_collector_status,
    run_deployment_governance_metrics_config_reload,
    run_deployment_governance_metrics_config_show,
    run_deployment_governance_metrics_dashboard,
    run_deployment_governance_metrics_export,
    run_deployment_governance_metrics_export_csv,
    run_deployment_governance_metrics_export_json,
    run_deployment_governance_metrics_history,
    run_deployment_governance_metrics_latest,
    run_deployment_governance_metrics_reload,
    run_deployment_governance_metrics_requests,
    run_deployment_governance_metrics_reset,
    run_deployment_governance_metrics_retention_run,
    run_deployment_governance_metrics_retention_status,
)
from backend.observability.deployment_governance_metrics_history import (
    GovernanceIntegrityMetricsSnapshot,
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


def test_latest_with_no_history_is_null(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-latest-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_latest(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue()) is None


def test_latest_returns_most_recent_snapshot(
    monkeypatch, tmp_path
) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-latest.db")

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    service = runtime.build_integrity_metrics_service()
    service.record_success(100.0)
    service.record_failure(50.0)

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_latest(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["metrics"]["total_dispatches"] == 2
    assert "captured_at" in payload


def test_history_with_no_snapshots_is_empty(
    monkeypatch, tmp_path
) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-history-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_history(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue()) == []


def test_history_returns_newest_first(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-history.db")

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    service = runtime.build_integrity_metrics_service()
    service.record_success(100.0)
    service.record_success(200.0)
    service.record_success(300.0)

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_history(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert len(payload) == 3
    assert (
        payload[0]["metrics"]["successful_dispatches"] == 3
    )
    assert (
        payload[-1]["metrics"]["successful_dispatches"] == 1
    )


def test_history_respects_limit(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-history-limit.db")

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    service = runtime.build_integrity_metrics_service()

    for _ in range(5):
        service.record_success(10.0)

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_history(
        limit=2, json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert len(payload) == 2


def test_export_json_to_stdout(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-export-json.db")

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    runtime.build_integrity_metrics_service().record_success(100.0)

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_export_json(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["metrics"]["successful_dispatches"] == 1
    assert "history" not in payload


def test_export_json_with_history_flag(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-export-json-history.db")

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    runtime.build_integrity_metrics_service().record_success(100.0)

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_export_json(
        include_history=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert len(payload["history"]) == 1


def test_export_json_to_output_file(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-export-json-file.db")

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    runtime.build_integrity_metrics_service().record_success(100.0)

    output_path = tmp_path / "export.json"

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_export_json(
        output_path=str(output_path), stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "exported" in stdout.getvalue().lower()

    payload = json.loads(output_path.read_text())

    assert payload["metrics"]["successful_dispatches"] == 1


def test_export_csv_to_stdout(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-export-csv.db")

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    runtime.build_integrity_metrics_service().record_success(100.0)

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_export_csv(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    lines = stdout.getvalue().strip().splitlines()

    assert lines[0].startswith("row_type,captured_at,")
    assert len(lines) == 2


def test_export_csv_with_history_flag(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-export-csv-history.db")

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    runtime.build_integrity_metrics_service().record_success(100.0)

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_export_csv(
        include_history=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    lines = stdout.getvalue().strip().splitlines()

    assert len(lines) == 3


def test_export_csv_to_output_file(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-export-csv-file.db")

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    runtime.build_integrity_metrics_service().record_success(100.0)

    output_path = tmp_path / "export.csv"

    exit_code = run_deployment_governance_metrics_export_csv(
        output_path=str(output_path),
        stdout=StringIO(),
        stderr=StringIO(),
    )

    assert exit_code == 0

    content = output_path.read_text()

    assert content.startswith("row_type,captured_at,")


def test_export_with_empty_history(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-export-empty-history.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_export_json(
        include_history=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["history"] == []


def test_aggregate_requires_range_without_bucket_flag(
    monkeypatch, tmp_path
) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-aggregate-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_metrics_aggregate(
        stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2
    assert "--from and --to" in stderr.getvalue()


def test_aggregate_rejects_hourly_and_daily_together(
    monkeypatch, tmp_path
) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-aggregate-conflict.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_metrics_aggregate(
        hourly=True, daily=True, stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2
    assert "mutually exclusive" in stderr.getvalue()


def test_aggregate_rejects_naive_timestamps(
    monkeypatch, tmp_path
) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-aggregate-naive.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_metrics_aggregate(
        start="2026-01-01T00:00:00",
        end="2026-01-02T00:00:00",
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2
    assert "timezone-aware" in stderr.getvalue()


def test_aggregate_between_explicit_range(
    monkeypatch, tmp_path
) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-aggregate-between.db")

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    runtime.build_integrity_metrics_service().record_success(100.0)

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_aggregate(
        start="2020-01-01T00:00:00+00:00",
        end="2030-01-01T00:00:00+00:00",
        json_output=True,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert len(payload) == 1
    assert payload[0]["dispatches"] == 1


def test_aggregate_hourly_defaults_to_full_history_range(
    monkeypatch, tmp_path
) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-aggregate-hourly.db")

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    service = runtime.build_integrity_metrics_service()

    # Two captures are needed: with only one snapshot ever recorded,
    # the default range collapses to that single instant and it
    # becomes its own baseline (zero-length, empty window).
    service.record_success(100.0)
    service.record_success(200.0)

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_aggregate(
        hourly=True,
        json_output=True,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert len(payload) == 1
    assert payload[0]["dispatches"] == 1


def test_aggregate_daily_with_empty_history(
    monkeypatch, tmp_path
) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-aggregate-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_aggregate(
        daily=True,
        json_output=True,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue()) == []


def test_alerts_with_no_activity_is_empty(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-alerts-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_alerts(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue()) == []


def test_alerts_shows_active_alerts(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-alerts-active.db")

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    service = runtime.build_integrity_metrics_service()

    for _ in range(9):
        service.record_failure(10.0)

    service.record_success(10.0)

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_alerts(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    names = {alert["name"] for alert in payload}

    assert "failure_rate" in names


def test_alerts_clear_confirms_success(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-alerts-clear.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_alerts_clear(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue()) == {"status": "cleared"}


def test_dashboard_on_empty_history(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-dashboard-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_dashboard(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["summary"]["total_dispatches"] == 0
    assert payload["success_rate"] == 0.0
    assert payload["active_alerts"] == 0


def test_dashboard_reflects_persisted_metrics(
    monkeypatch, tmp_path
) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-dashboard.db")

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    service = runtime.build_integrity_metrics_service()

    for _ in range(7):
        service.record_success(10.0)

    for _ in range(3):
        service.record_failure(10.0)

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_dashboard(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["summary"]["total_dispatches"] == 10
    assert payload["success_rate"] == 70.0
    assert payload["failure_rate"] == 30.0


def test_dashboard_reflects_active_alerts(
    monkeypatch, tmp_path
) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-dashboard-alerts.db")

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    service = runtime.build_integrity_metrics_service()

    for _ in range(9):
        service.record_failure(10.0)

    service.record_success(10.0)

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_dashboard(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["active_alerts"] >= 1


def test_requests_empty_state() -> None:
    from backend.observability.deployment_governance_api import (
        get_request_metrics_collector,
    )

    get_request_metrics_collector().reset()

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_requests(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue()) == {
        "total_requests": 0,
        "successful_requests": 0,
        "failed_requests": 0,
        "exceptions": 0,
        "average_latency_ms": 0.0,
    }


def test_requests_reflects_same_process_traffic() -> None:
    from fastapi.testclient import TestClient

    from backend.dashboard import app
    from backend.observability.deployment_governance_api import (
        get_request_metrics_collector,
    )

    get_request_metrics_collector().reset()

    client = TestClient(app)
    client.get("/governance/metrics")

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_requests(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["total_requests"] >= 1


def test_collector_status_fresh_process_is_not_running(
    monkeypatch, tmp_path
) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-collector-status.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_collector_status(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue()) == {"running": False}


def test_collector_collect_with_no_activity(
    monkeypatch, tmp_path
) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-collector-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_collector_collect(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue()) is None


def test_collector_collect_captures_snapshot_with_activity(
    monkeypatch, tmp_path
) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-collector-activity.db")

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    runtime.build_integrity_metrics_service().record_success(50.0)

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_collector_collect(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["metrics"]["successful_dispatches"] == 1


def _seed_history_repository(runtime, count: int) -> None:
    from datetime import datetime, timezone

    from backend.observability.deployment_governance_metrics import (
        GovernanceIntegrityMetrics,
    )

    repository = runtime.build_integrity_metrics_history_repository()

    for i in range(count):
        repository.append(
            GovernanceIntegrityMetricsSnapshot(
                captured_at=datetime.now(timezone.utc),
                metrics=GovernanceIntegrityMetrics(
                    total_dispatches=i + 1,
                    successful_dispatches=i + 1,
                    failed_dispatches=0,
                    retry_dispatches=0,
                    average_duration_ms=0.0,
                ),
            )
        )


def test_retention_status_on_empty_history(
    monkeypatch, tmp_path
) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-retention-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_retention_status(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["expired_count"] == 0
    assert payload["policy"]["max_entries"] == 500


def test_retention_status_reports_expired_count(
    monkeypatch, tmp_path
) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-retention-status.db")

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    _seed_history_repository(runtime, 600)

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_retention_status(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["expired_count"] == 100


def test_retention_run_prunes_expired_snapshots(
    monkeypatch, tmp_path
) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-retention-run.db")

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    _seed_history_repository(runtime, 600)

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_retention_run(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue()) == {"discarded": 100}

    repository = runtime.build_integrity_metrics_history_repository()

    assert len(repository.list()) == 500


def test_retention_run_on_empty_history_prunes_nothing(
    monkeypatch, tmp_path
) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-retention-run-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_retention_run(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue()) == {"discarded": 0}


def test_config_show_reports_defaults(monkeypatch) -> None:
    monkeypatch.delenv(
        "NOTEBOOK2API_GOVERNANCE_METRICS_COLLECTION_INTERVAL_SECONDS",
        raising=False,
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_config_show(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["collection_interval_seconds"] == 60
    assert payload["max_history_entries"] == 500
    assert payload["max_history_age_days"] == 30
    assert payload["auto_flush"] is True


def test_config_show_reflects_environment(monkeypatch) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_METRICS_COLLECTION_INTERVAL_SECONDS",
        "15",
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_config_show(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["collection_interval_seconds"] == 15


def test_config_reload_reflects_environment(monkeypatch) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_METRICS_MAX_HISTORY_ENTRIES", "77"
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_config_reload(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["max_history_entries"] == 77


def test_config_show_invalid_env_value_fails(monkeypatch) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_METRICS_AUTO_FLUSH", "not-a-bool"
    )

    stderr = StringIO()

    exit_code = run_deployment_governance_metrics_config_show(
        json_output=True, stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2


def test_health_reports_successful_initialization(
    monkeypatch, tmp_path
) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-health.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_health(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["built"] is True
    assert payload["initialized"] is True
    assert payload["collector_running"] is True


def test_health_reflects_active_alerts(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-health-alerts.db")

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    service = runtime.build_integrity_metrics_service()

    for _ in range(9):
        service.record_failure(10.0)

    service.record_success(10.0)

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_health(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["active_alerts"] >= 1


def test_bootstrap_reports_success(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-bootstrap.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_bootstrap(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["status"] == "bootstrap succeeded"
    assert payload["built"] is True
    assert payload["initialized"] is True


def test_bootstrap_human_output(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-bootstrap-human.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_metrics_bootstrap(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "bootstrapped successfully" in stdout.getvalue()
