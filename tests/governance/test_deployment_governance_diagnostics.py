from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_diagnostics import (
    GovernanceDiagnostics,
    GovernanceDiagnosticsService,
)

BASE_TIME = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


def _service(
    *,
    runtime_state="running",
    active_dispatches=0,
    pending_dispatches=0,
    registered_providers=0,
    clock=_clock,
):
    return GovernanceDiagnosticsService(
        runtime_state=lambda: runtime_state,
        active_dispatches=lambda: active_dispatches,
        pending_dispatches=lambda: pending_dispatches,
        registered_providers=lambda: registered_providers,
        clock=clock,
    )


# --- Model -------------------------------------------------------------


def test_diagnostics_rejects_naive_generated_at():
    with pytest.raises(
        ValueError, match="generated_at must be timezone-aware"
    ):
        GovernanceDiagnostics(
            generated_at=datetime(2026, 7, 21, 12, 0, 0),
            runtime_state="running",
            active_dispatches=0,
            registered_providers=0,
            pending_dispatches=0,
        )


@pytest.mark.parametrize(
    "field", ["active_dispatches", "registered_providers", "pending_dispatches"]
)
def test_diagnostics_rejects_negative_counts(field):
    kwargs = dict(
        generated_at=BASE_TIME,
        runtime_state="running",
        active_dispatches=0,
        registered_providers=0,
        pending_dispatches=0,
    )
    kwargs[field] = -1

    with pytest.raises(ValueError, match=f"{field} must be >= 0"):
        GovernanceDiagnostics(**kwargs)


def test_diagnostics_to_dict():
    diagnostics = GovernanceDiagnostics(
        generated_at=BASE_TIME,
        runtime_state="running",
        active_dispatches=2,
        registered_providers=3,
        pending_dispatches=1,
    )

    assert diagnostics.to_dict() == {
        "generated_at": BASE_TIME.isoformat(),
        "runtime_state": "running",
        "active_dispatches": 2,
        "registered_providers": 3,
        "pending_dispatches": 1,
    }


# --- Snapshot generation -------------------------------------------------


class TestGovernanceDiagnosticsServiceSnapshot:

    def test_snapshot_generates_utc_timestamp(self):
        service = _service()

        snapshot = service.snapshot()

        assert snapshot.generated_at == BASE_TIME

    def test_snapshot_aggregates_all_readers(self):
        service = _service(
            runtime_state="running",
            active_dispatches=4,
            pending_dispatches=2,
            registered_providers=3,
        )

        snapshot = service.snapshot()

        assert snapshot.runtime_state == "running"
        assert snapshot.active_dispatches == 4
        assert snapshot.pending_dispatches == 2
        assert snapshot.registered_providers == 3

    def test_snapshot_is_deterministic_for_unchanged_state(self):
        service = _service(registered_providers=5)

        first = service.snapshot()
        second = service.snapshot()

        assert first == second

    def test_snapshot_has_no_side_effects(self):
        calls = []

        service = GovernanceDiagnosticsService(
            runtime_state=lambda: (calls.append("state"), "running")[1],
            active_dispatches=lambda: 0,
            pending_dispatches=lambda: 0,
            registered_providers=lambda: 0,
            clock=_clock,
        )

        service.snapshot()
        service.snapshot()

        # Each snapshot re-reads current state (readers are called
        # again), but nothing accumulates or mutates as a result.
        assert calls == ["state", "state"]


# --- Runtime state -------------------------------------------------------


class TestGovernanceDiagnosticsServiceRuntime:

    def test_runtime_reports_state_and_active_dispatches(self):
        service = _service(runtime_state="stopped", active_dispatches=7)

        summary = service.runtime()

        assert summary == {"state": "stopped", "active_dispatches": 7}


# --- Provider count --------------------------------------------------------


class TestGovernanceDiagnosticsServiceProviders:

    def test_providers_reports_registered_count(self):
        service = _service(registered_providers=3)

        assert service.providers() == {"registered_providers": 3}

    def test_providers_zero_when_none_registered(self):
        service = _service(registered_providers=0)

        assert service.providers() == {"registered_providers": 0}


# --- Scheduler summary -------------------------------------------------


class TestGovernanceDiagnosticsServiceScheduler:

    def test_scheduler_reports_pending_dispatches(self):
        service = _service(pending_dispatches=9)

        assert service.scheduler() == {"pending_dispatches": 9}


# --- Delivery runtime wiring ------------------------------------------


class TestGovernanceIntegrityDeliveryRuntimeDiagnostics:

    def _runtime(self, **overrides):
        from backend.observability.deployment_governance_delivery_runtime import (
            build_integrity_delivery_runtime,
        )
        from backend.observability.deployment_governance_liveness import (
            GovernanceLivenessService,
        )

        provider_registry = Mock(spec=["list_providers"])
        provider_registry.list_providers.return_value = []

        scheduler = Mock(spec=["pending_dispatches"])
        scheduler.pending_dispatches.return_value = ()

        defaults = dict(
            worker=Mock(),
            scheduler=scheduler,
            provider_registry=provider_registry,
            liveness_service=GovernanceLivenessService(),
        )
        defaults.update(overrides)

        return build_integrity_delivery_runtime(**defaults)

    def test_diagnostics_reflects_runtime_state(self):
        runtime = self._runtime()

        assert (
            runtime.build_diagnostics_service().runtime()["state"]
            == "stopped"
        )

        runtime.start()

        try:
            assert (
                runtime.build_diagnostics_service().runtime()["state"]
                == "running"
            )

        finally:
            runtime.stop()

    def test_diagnostics_reflects_provider_count(self):
        from backend.observability.deployment_governance_persistence import (
            build_deployment_governance_persistence,
        )

        persistence_runtime = build_deployment_governance_persistence()

        runtime = self._runtime(
            provider_registry=(
                persistence_runtime.build_integrity_provider_registry()
            )
        )

        assert (
            runtime.build_diagnostics_service().providers()[
                "registered_providers"
            ]
            == 3
        )

    def test_diagnostics_reflects_scheduler_pending_count(self):
        scheduler = Mock(spec=["pending_dispatches"])
        scheduler.pending_dispatches.return_value = (
            object(),
            object(),
        )

        runtime = self._runtime(scheduler=scheduler)

        assert (
            runtime.build_diagnostics_service().scheduler()[
                "pending_dispatches"
            ]
            == 2
        )

    def test_diagnostics_is_registered_on_health_service(self):
        runtime = self._runtime()

        statuses = {
            status.component: status
            for status in runtime.build_health_service().check_all()
        }

        assert statuses["diagnostics"].healthy is True

    def test_diagnostics_is_registered_on_readiness_service(self):
        runtime = self._runtime()

        statuses = {
            status.component: status
            for status in runtime.build_readiness_service().check_all()
        }

        assert statuses["diagnostics"].ready is True


# --- Persistence runtime wiring -----------------------------------------


class TestPersistenceRuntimeDiagnosticsService:

    def test_default_runtime_diagnostics(self):
        from backend.observability.deployment_governance_persistence import (
            build_deployment_governance_persistence,
        )

        runtime = build_deployment_governance_persistence()

        snapshot = runtime.build_integrity_diagnostics_service().snapshot()

        assert snapshot.runtime_state == "not_running"
        assert snapshot.active_dispatches == 0
        assert snapshot.pending_dispatches == 0
        assert snapshot.registered_providers == 3


# --- API endpoint ----------------------------------------------------------


def _setup_sqlite_env(monkeypatch, tmp_path, name: str) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / name),
    )


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceDiagnosticsApi:

    def test_diagnostics_endpoint_returns_snapshot(
        self, client, monkeypatch, tmp_path
    ) -> None:
        _setup_sqlite_env(monkeypatch, tmp_path, "api-diagnostics.db")

        response = client.get("/governance/diagnostics")

        assert response.status_code == 200

        payload = response.json()

        assert payload["runtime_state"] == "not_running"
        assert payload["registered_providers"] == 3
        assert payload["pending_dispatches"] == 0
        assert "generated_at" in payload
