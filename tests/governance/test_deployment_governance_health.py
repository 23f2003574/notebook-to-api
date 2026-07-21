from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_health import (
    GovernanceHealthService,
    GovernanceHealthStatus,
)

BASE_TIME = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


# --- Model -------------------------------------------------------------


def test_status_rejects_naive_checked_at():
    with pytest.raises(
        ValueError, match="checked_at must be timezone-aware"
    ):
        GovernanceHealthStatus(
            component="delivery_runtime",
            healthy=True,
            message=None,
            checked_at=datetime(2026, 7, 21, 12, 0, 0),
        )


def test_status_rejects_healthy_with_message():
    with pytest.raises(
        ValueError, match="message must not be set when healthy is True"
    ):
        GovernanceHealthStatus(
            component="delivery_runtime",
            healthy=True,
            message="boom",
            checked_at=BASE_TIME,
        )


def test_status_rejects_unhealthy_without_message():
    with pytest.raises(
        ValueError, match="message must be set when healthy is False"
    ):
        GovernanceHealthStatus(
            component="delivery_runtime",
            healthy=False,
            message=None,
            checked_at=BASE_TIME,
        )


def test_status_to_dict():
    status = GovernanceHealthStatus(
        component="delivery_runtime",
        healthy=False,
        message="offline",
        checked_at=BASE_TIME,
    )

    assert status.to_dict() == {
        "component": "delivery_runtime",
        "healthy": False,
        "message": "offline",
        "checked_at": BASE_TIME.isoformat(),
    }


# --- Registration --------------------------------------------------------


class TestGovernanceHealthServiceRegistration:

    def test_register_and_check(self):
        service = GovernanceHealthService(clock=_clock)
        service.register("delivery_runtime", lambda: True)

        status = service.check("delivery_runtime")

        assert status.healthy is True
        assert status.message is None
        assert status.checked_at == BASE_TIME

    def test_duplicate_registration_raises(self):
        service = GovernanceHealthService()
        service.register("delivery_runtime", lambda: True)

        with pytest.raises(ValueError, match="already registered"):
            service.register("delivery_runtime", lambda: True)

    def test_check_unregistered_component_raises(self):
        service = GovernanceHealthService()

        with pytest.raises(LookupError):
            service.check("unknown")


# --- Checks --------------------------------------------------------------


class TestGovernanceHealthServiceCheck:

    def test_healthy_check_bool(self):
        service = GovernanceHealthService(clock=_clock)
        service.register("metrics_bootstrap", lambda: True)

        status = service.check("metrics_bootstrap")

        assert status.healthy is True
        assert status.message is None

    def test_healthy_check_tuple_with_no_message(self):
        service = GovernanceHealthService(clock=_clock)
        service.register("metrics_bootstrap", lambda: (True, None))

        status = service.check("metrics_bootstrap")

        assert status.healthy is True

    def test_failing_check_bool_gets_default_message(self):
        service = GovernanceHealthService(clock=_clock)
        service.register("logging_bootstrap", lambda: False)

        status = service.check("logging_bootstrap")

        assert status.healthy is False
        assert status.message == "logging_bootstrap reported unhealthy"

    def test_failing_check_tuple_message(self):
        service = GovernanceHealthService(clock=_clock)
        service.register(
            "provider_registry", lambda: (False, "provider offline")
        )

        status = service.check("provider_registry")

        assert status.healthy is False
        assert status.message == "provider offline"

    def test_raising_check_is_treated_as_unhealthy(self):
        def _boom():
            raise RuntimeError("connection refused")

        service = GovernanceHealthService(clock=_clock)
        service.register("delivery_runtime", _boom)

        status = service.check("delivery_runtime")

        assert status.healthy is False
        assert status.message == "connection refused"

    def test_one_failing_check_does_not_stop_others(self):
        def _boom():
            raise RuntimeError("boom")

        service = GovernanceHealthService(clock=_clock)
        service.register("a", _boom)
        service.register("b", lambda: True)

        statuses = service.check_all()

        assert len(statuses) == 2
        assert {s.component: s.healthy for s in statuses} == {
            "a": False,
            "b": True,
        }


# --- Ordering --------------------------------------------------------------


def test_check_all_is_ordered_by_component_name():
    service = GovernanceHealthService(clock=_clock)
    service.register("provider_registry", lambda: True)
    service.register("delivery_runtime", lambda: True)
    service.register("logging_bootstrap", lambda: True)
    service.register("metrics_bootstrap", lambda: True)

    statuses = service.check_all()

    assert [s.component for s in statuses] == [
        "delivery_runtime",
        "logging_bootstrap",
        "metrics_bootstrap",
        "provider_registry",
    ]


def test_check_all_order_is_independent_of_registration_order():
    service_a = GovernanceHealthService(clock=_clock)
    service_a.register("b", lambda: True)
    service_a.register("a", lambda: True)

    service_b = GovernanceHealthService(clock=_clock)
    service_b.register("a", lambda: True)
    service_b.register("b", lambda: True)

    assert [s.component for s in service_a.check_all()] == [
        s.component for s in service_b.check_all()
    ]


# --- Summary ---------------------------------------------------------------


class TestGovernanceHealthServiceSummary:

    def test_summary_healthy_when_all_components_healthy(self):
        service = GovernanceHealthService(clock=_clock)
        service.register("a", lambda: True)
        service.register("b", lambda: True)

        summary = service.summary()

        assert summary.healthy is True
        assert len(summary.components) == 2
        assert summary.checked_at == BASE_TIME

    def test_summary_unhealthy_when_any_component_unhealthy(self):
        service = GovernanceHealthService(clock=_clock)
        service.register("a", lambda: True)
        service.register("b", lambda: (False, "down"))

        summary = service.summary()

        assert summary.healthy is False

    def test_summary_healthy_with_no_registered_components(self):
        service = GovernanceHealthService(clock=_clock)

        summary = service.summary()

        assert summary.healthy is True
        assert summary.components == ()

    def test_summary_to_dict(self):
        service = GovernanceHealthService(clock=_clock)
        service.register("a", lambda: True)
        service.register("b", lambda: (False, "down"))

        payload = service.summary().to_dict()

        assert payload["healthy"] is False
        assert payload["checked_at"] == BASE_TIME.isoformat()
        assert [c["component"] for c in payload["components"]] == [
            "a",
            "b",
        ]


# --- Delivery runtime wiring ------------------------------------------


class TestGovernanceIntegrityDeliveryRuntimeHealthService:

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
        )
        defaults.update(overrides)

        return build_integrity_delivery_runtime(**defaults)

    def test_missing_bootstraps_are_reported_unhealthy(self):
        runtime = self._runtime()

        statuses = {
            status.component: status
            for status in runtime.build_health_service().check_all()
        }

        assert statuses["metrics_bootstrap"].healthy is False
        assert statuses["logging_bootstrap"].healthy is False

    def test_delivery_runtime_unhealthy_until_started(self):
        runtime = self._runtime()

        status = runtime.build_health_service().check(
            "delivery_runtime"
        )

        assert status.healthy is False

    def test_delivery_runtime_healthy_once_started(self):
        runtime = self._runtime()
        runtime.start()

        try:
            status = runtime.build_health_service().check(
                "delivery_runtime"
            )

            assert status.healthy is True

        finally:
            runtime.stop()

    def test_provider_registry_healthy_with_real_registry(self):
        from backend.observability.deployment_governance_persistence import (
            build_deployment_governance_persistence,
        )

        persistence_runtime = build_deployment_governance_persistence()

        runtime = self._runtime(
            provider_registry=(
                persistence_runtime.build_integrity_provider_registry()
            )
        )

        status = runtime.build_health_service().check(
            "provider_registry"
        )

        assert status.healthy is True


# --- Persistence runtime wiring -----------------------------------------


class TestPersistenceRuntimeHealthService:

    def test_default_runtime_is_healthy(self):
        from backend.observability.deployment_governance_persistence import (
            build_deployment_governance_persistence,
        )

        runtime = build_deployment_governance_persistence()

        summary = runtime.build_integrity_health_service().summary()

        assert summary.healthy is True
        assert {s.component for s in summary.components} == {
            "provider_registry",
            "metrics_service",
        }


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


class TestGovernanceHealthApi:

    def test_health_endpoint_returns_overall_and_component_status(
        self, client, monkeypatch, tmp_path
    ) -> None:
        _setup_sqlite_env(monkeypatch, tmp_path, "api-health.db")

        response = client.get("/governance/health")

        assert response.status_code == 200

        payload = response.json()

        assert payload["healthy"] is True
        assert "checked_at" in payload

        components = {c["component"] for c in payload["components"]}

        assert components == {"provider_registry", "metrics_service"}
