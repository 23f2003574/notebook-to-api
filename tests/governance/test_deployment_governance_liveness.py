from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_liveness import (
    GovernanceLivenessService,
    GovernanceLivenessStatus,
    get_liveness_service,
)

BASE_TIME = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


@pytest.fixture(autouse=True)
def _reset_singleton():
    """
    The liveness service is a process-wide singleton, so tests that
    touch get_liveness_service() (directly or via the API endpoint)
    must not leak started state into other tests.
    """

    get_liveness_service().reset()

    yield

    get_liveness_service().reset()


# --- Model -------------------------------------------------------------


def test_status_rejects_naive_checked_at():
    with pytest.raises(
        ValueError, match="checked_at must be timezone-aware"
    ):
        GovernanceLivenessStatus(
            alive=True,
            checked_at=datetime(2026, 7, 21, 12, 0, 0),
            uptime_seconds=0,
        )


def test_status_rejects_negative_uptime():
    with pytest.raises(ValueError, match="uptime_seconds must be >= 0"):
        GovernanceLivenessStatus(
            alive=True,
            checked_at=BASE_TIME,
            uptime_seconds=-1,
        )


def test_status_to_dict():
    status = GovernanceLivenessStatus(
        alive=True,
        checked_at=BASE_TIME,
        uptime_seconds=42,
    )

    assert status.to_dict() == {
        "alive": True,
        "checked_at": BASE_TIME.isoformat(),
        "uptime_seconds": 42,
    }


# --- Startup -------------------------------------------------------------


class TestGovernanceLivenessServiceStartup:

    def test_not_alive_before_start(self):
        service = GovernanceLivenessService(clock=_clock)

        status = service.check()

        assert status.alive is False
        assert status.uptime_seconds == 0
        assert status.checked_at == BASE_TIME

    def test_alive_after_start(self):
        service = GovernanceLivenessService(clock=_clock)
        service.start()

        status = service.check()

        assert status.alive is True

    def test_start_is_idempotent(self):
        monotonic_values = iter([100.0, 200.0])

        service = GovernanceLivenessService(
            clock=_clock,
            monotonic=lambda: next(monotonic_values),
        )

        service.start()  # epoch = 100.0
        service.start()  # no-op: does not consume a monotonic value

        assert service.uptime() == 100  # 200.0 - 100.0


# --- Shutdown --------------------------------------------------------------


class TestGovernanceLivenessServiceShutdown:

    def test_reset_clears_alive_state(self):
        service = GovernanceLivenessService(clock=_clock)
        service.start()
        service.reset()

        status = service.check()

        assert status.alive is False
        assert status.uptime_seconds == 0

    def test_reset_before_start_is_safe(self):
        service = GovernanceLivenessService(clock=_clock)

        service.reset()

        assert service.check().alive is False

    def test_start_after_reset_re_arms(self):
        monotonic_values = iter([10.0, 10.0, 50.0])

        service = GovernanceLivenessService(
            clock=_clock,
            monotonic=lambda: next(monotonic_values),
        )

        service.start()
        service.reset()
        service.start()

        assert service.uptime() == 40


# --- Uptime progression ----------------------------------------------------


class TestGovernanceLivenessServiceUptime:

    def test_uptime_progresses_monotonically(self):
        monotonic_values = iter([1000.0, 1010.0, 1025.0])

        service = GovernanceLivenessService(
            clock=_clock,
            monotonic=lambda: next(monotonic_values),
        )

        service.start()

        assert service.uptime() == 10
        assert service.uptime() == 25

    def test_uptime_is_zero_when_never_started(self):
        service = GovernanceLivenessService(clock=_clock)

        assert service.uptime() == 0

    def test_check_uptime_matches_uptime_method(self):
        monotonic_values = iter([0.0, 5.0, 5.0])

        service = GovernanceLivenessService(
            clock=_clock,
            monotonic=lambda: next(monotonic_values),
        )

        service.start()

        status = service.check()

        assert status.uptime_seconds == 5


# --- Singleton -------------------------------------------------------------


class TestGovernanceLivenessSingleton:

    def test_get_liveness_service_returns_same_instance(self):
        assert get_liveness_service() is get_liveness_service()

    def test_get_liveness_service_auto_starts(self):
        assert get_liveness_service().check().alive is True

    def test_get_liveness_service_does_not_reset_existing_uptime(self):
        service = get_liveness_service()
        service.start()

        uptime_before = service.uptime()

        get_liveness_service()

        assert service.uptime() >= uptime_before


# --- Delivery runtime wiring ------------------------------------------


class TestGovernanceIntegrityDeliveryRuntimeLiveness:

    def _runtime(self, **overrides):
        from backend.observability.deployment_governance_delivery_runtime import (
            build_integrity_delivery_runtime,
        )

        provider_registry = Mock(spec=["list_providers"])
        provider_registry.list_providers.return_value = []

        defaults = dict(
            worker=Mock(),
            scheduler=Mock(),
            provider_registry=provider_registry,
            liveness_service=GovernanceLivenessService(),
        )
        defaults.update(overrides)

        return build_integrity_delivery_runtime(**defaults)

    def test_start_marks_liveness_service_alive(self):
        runtime = self._runtime()

        runtime.start()

        try:
            assert runtime.liveness_service.check().alive is True

        finally:
            runtime.stop()

    def test_stop_resets_liveness_service(self):
        runtime = self._runtime()

        runtime.start()
        runtime.stop()

        assert runtime.liveness_service.check().alive is False

    def test_defaults_to_singleton_when_not_supplied(self):
        runtime = self._runtime(liveness_service=None)

        assert runtime.liveness_service is get_liveness_service()

    def test_liveness_is_registered_on_health_service(self):
        runtime = self._runtime()

        statuses = {
            status.component: status
            for status in runtime.build_health_service().check_all()
        }

        assert "liveness" in statuses
        assert statuses["liveness"].healthy is False

        runtime.start()

        try:
            statuses = {
                status.component: status
                for status in runtime.build_health_service().check_all()
            }

            assert statuses["liveness"].healthy is True

        finally:
            runtime.stop()


# --- Persistence runtime wiring -----------------------------------------


class TestPersistenceRuntimeLivenessService:

    def test_returns_the_process_wide_singleton(self):
        from backend.observability.deployment_governance_persistence import (
            build_deployment_governance_persistence,
        )

        runtime = build_deployment_governance_persistence()

        assert (
            runtime.build_integrity_liveness_service()
            is get_liveness_service()
        )


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


class TestGovernanceLivenessApi:

    def test_live_endpoint_reports_alive_with_uptime(
        self, client, monkeypatch, tmp_path
    ) -> None:
        _setup_sqlite_env(monkeypatch, tmp_path, "api-live.db")

        response = client.get("/governance/live")

        assert response.status_code == 200

        payload = response.json()

        assert payload["alive"] is True
        assert payload["uptime_seconds"] >= 0
        assert "checked_at" in payload
