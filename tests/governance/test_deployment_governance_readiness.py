from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_readiness import (
    GovernanceReadinessService,
    GovernanceReadinessStatus,
)

BASE_TIME = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


# --- Model -------------------------------------------------------------


def test_status_rejects_naive_checked_at():
    with pytest.raises(
        ValueError, match="checked_at must be timezone-aware"
    ):
        GovernanceReadinessStatus(
            component="delivery_worker",
            ready=True,
            reason=None,
            checked_at=datetime(2026, 7, 21, 12, 0, 0),
        )


def test_status_rejects_ready_with_reason():
    with pytest.raises(
        ValueError, match="reason must not be set when ready is True"
    ):
        GovernanceReadinessStatus(
            component="delivery_worker",
            ready=True,
            reason="boom",
            checked_at=BASE_TIME,
        )


def test_status_rejects_not_ready_without_reason():
    with pytest.raises(
        ValueError, match="reason must be set when ready is False"
    ):
        GovernanceReadinessStatus(
            component="delivery_worker",
            ready=False,
            reason=None,
            checked_at=BASE_TIME,
        )


def test_status_to_dict():
    status = GovernanceReadinessStatus(
        component="delivery_worker",
        ready=False,
        reason="not initialized",
        checked_at=BASE_TIME,
    )

    assert status.to_dict() == {
        "component": "delivery_worker",
        "ready": False,
        "reason": "not initialized",
        "checked_at": BASE_TIME.isoformat(),
    }


# --- Registration --------------------------------------------------------


class TestGovernanceReadinessServiceRegistration:

    def test_register_and_check(self):
        service = GovernanceReadinessService(clock=_clock)
        service.register("delivery_worker", lambda: True)

        status = service.check("delivery_worker")

        assert status.ready is True
        assert status.reason is None
        assert status.checked_at == BASE_TIME

    def test_duplicate_registration_raises(self):
        service = GovernanceReadinessService()
        service.register("delivery_worker", lambda: True)

        with pytest.raises(ValueError, match="already registered"):
            service.register("delivery_worker", lambda: True)

    def test_check_unregistered_component_raises(self):
        service = GovernanceReadinessService()

        with pytest.raises(LookupError):
            service.check("unknown")


# --- Checks --------------------------------------------------------------


class TestGovernanceReadinessServiceCheck:

    def test_ready_component_bool(self):
        service = GovernanceReadinessService(clock=_clock)
        service.register("scheduler", lambda: True)

        status = service.check("scheduler")

        assert status.ready is True
        assert status.reason is None

    def test_ready_component_tuple_with_no_reason(self):
        service = GovernanceReadinessService(clock=_clock)
        service.register("scheduler", lambda: (True, None))

        status = service.check("scheduler")

        assert status.ready is True

    def test_not_ready_component_bool_gets_default_reason(self):
        service = GovernanceReadinessService(clock=_clock)
        service.register("provider_registry", lambda: False)

        status = service.check("provider_registry")

        assert status.ready is False
        assert status.reason == "provider_registry is not ready"

    def test_not_ready_component_tuple_reason(self):
        service = GovernanceReadinessService(clock=_clock)
        service.register(
            "provider_registry", lambda: (False, "empty registry")
        )

        status = service.check("provider_registry")

        assert status.ready is False
        assert status.reason == "empty registry"

    def test_raising_check_is_treated_as_not_ready(self):
        def _boom():
            raise RuntimeError("connection refused")

        service = GovernanceReadinessService(clock=_clock)
        service.register("delivery_runtime", _boom)

        status = service.check("delivery_runtime")

        assert status.ready is False
        assert status.reason == "connection refused"

    def test_one_not_ready_check_does_not_stop_others(self):
        def _boom():
            raise RuntimeError("boom")

        service = GovernanceReadinessService(clock=_clock)
        service.register("a", _boom)
        service.register("b", lambda: True)

        statuses = service.check_all()

        assert len(statuses) == 2
        assert {s.component: s.ready for s in statuses} == {
            "a": False,
            "b": True,
        }


# --- Ordering --------------------------------------------------------------


def test_check_all_is_ordered_by_component_name():
    service = GovernanceReadinessService(clock=_clock)
    service.register("provider_registry", lambda: True)
    service.register("delivery_runtime", lambda: True)
    service.register("scheduler", lambda: True)
    service.register("delivery_worker", lambda: True)

    statuses = service.check_all()

    assert [s.component for s in statuses] == [
        "delivery_runtime",
        "delivery_worker",
        "provider_registry",
        "scheduler",
    ]


def test_check_all_order_is_independent_of_registration_order():
    service_a = GovernanceReadinessService(clock=_clock)
    service_a.register("b", lambda: True)
    service_a.register("a", lambda: True)

    service_b = GovernanceReadinessService(clock=_clock)
    service_b.register("a", lambda: True)
    service_b.register("b", lambda: True)

    assert [s.component for s in service_a.check_all()] == [
        s.component for s in service_b.check_all()
    ]


# --- Summary ---------------------------------------------------------------


class TestGovernanceReadinessServiceSummary:

    def test_summary_ready_when_all_components_ready(self):
        service = GovernanceReadinessService(clock=_clock)
        service.register("a", lambda: True)
        service.register("b", lambda: True)

        summary = service.summary()

        assert summary.ready is True
        assert len(summary.components) == 2
        assert summary.checked_at == BASE_TIME

    def test_summary_not_ready_when_any_component_not_ready(self):
        service = GovernanceReadinessService(clock=_clock)
        service.register("a", lambda: True)
        service.register("b", lambda: (False, "down"))

        summary = service.summary()

        assert summary.ready is False

    def test_summary_ready_with_no_registered_components(self):
        service = GovernanceReadinessService(clock=_clock)

        summary = service.summary()

        assert summary.ready is True
        assert summary.components == ()

    def test_summary_to_dict(self):
        service = GovernanceReadinessService(clock=_clock)
        service.register("a", lambda: True)
        service.register("b", lambda: (False, "down"))

        payload = service.summary().to_dict()

        assert payload["ready"] is False
        assert payload["checked_at"] == BASE_TIME.isoformat()
        assert [c["component"] for c in payload["components"]] == [
            "a",
            "b",
        ]


# --- Delivery runtime wiring ------------------------------------------


class TestGovernanceIntegrityDeliveryRuntimeReadinessService:

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

    def test_delivery_worker_and_scheduler_ready_when_wired(self):
        runtime = self._runtime()

        statuses = {
            status.component: status
            for status in runtime.build_readiness_service().check_all()
        }

        assert statuses["delivery_worker"].ready is True
        assert statuses["scheduler"].ready is True

    def test_delivery_runtime_not_ready_until_started(self):
        runtime = self._runtime()

        status = runtime.build_readiness_service().check(
            "delivery_runtime"
        )

        assert status.ready is False

    def test_delivery_runtime_ready_once_started(self):
        runtime = self._runtime()
        runtime.start()

        try:
            status = runtime.build_readiness_service().check(
                "delivery_runtime"
            )

            assert status.ready is True

        finally:
            runtime.stop()

    def test_provider_registry_not_ready_when_empty(self):
        runtime = self._runtime()

        status = runtime.build_readiness_service().check(
            "provider_registry"
        )

        assert status.ready is False

    def test_provider_registry_ready_with_real_populated_registry(self):
        from backend.observability.deployment_governance_persistence import (
            build_deployment_governance_persistence,
        )

        persistence_runtime = build_deployment_governance_persistence()

        runtime = self._runtime(
            provider_registry=(
                persistence_runtime.build_integrity_provider_registry()
            )
        )

        status = runtime.build_readiness_service().check(
            "provider_registry"
        )

        assert status.ready is True


# --- Persistence runtime wiring -----------------------------------------


class TestPersistenceRuntimeReadinessService:

    def test_default_runtime_is_ready(self):
        from backend.observability.deployment_governance_persistence import (
            build_deployment_governance_persistence,
        )

        runtime = build_deployment_governance_persistence()

        summary = runtime.build_integrity_readiness_service().summary()

        assert summary.ready is True
        assert {s.component for s in summary.components} == {
            "provider_registry",
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


class TestGovernanceReadinessApi:

    def test_ready_endpoint_returns_overall_and_component_status(
        self, client, monkeypatch, tmp_path
    ) -> None:
        _setup_sqlite_env(monkeypatch, tmp_path, "api-ready.db")

        response = client.get("/governance/ready")

        assert response.status_code == 200

        payload = response.json()

        assert payload["ready"] is True
        assert "checked_at" in payload

        components = {c["component"] for c in payload["components"]}

        assert components == {"provider_registry"}
