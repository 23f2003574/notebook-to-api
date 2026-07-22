from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_lifecycle import (
    GovernanceLifecycleManager,
    LifecycleComponent,
    LifecycleReport,
    get_lifecycle_manager,
)

BASE_TIME = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


def _recorder():
    """
    Return (calls, start, stop, reload) where calls accumulates the
    name of every callable invoked, in invocation order.
    """

    calls: "list[str]" = []

    return (
        calls,
        lambda: calls.append("start"),
        lambda: calls.append("stop"),
        lambda: calls.append("reload"),
    )


@pytest.fixture(autouse=True)
def _reset_singleton():
    """
    The lifecycle manager is a process-wide singleton, so tests that
    touch get_lifecycle_manager() (directly or via the API endpoints)
    must not leak started state into other tests.
    """

    get_lifecycle_manager().shutdown()

    yield

    get_lifecycle_manager().shutdown()


# --- Model -------------------------------------------------------------


def test_lifecycle_component_rejects_empty_name():
    with pytest.raises(ValueError, match="name must not be empty"):
        LifecycleComponent(name="", startup_priority=0, started=True)


def test_lifecycle_component_to_dict():
    component = LifecycleComponent(
        name="a", startup_priority=1, started=True
    )

    assert component.to_dict() == {
        "name": "a",
        "startup_priority": 1,
        "started": True,
    }


def test_lifecycle_report_rejects_naive_completed_at():
    with pytest.raises(
        ValueError, match="completed_at must be timezone-aware"
    ):
        LifecycleReport(
            started=(),
            stopped=(),
            failed=(),
            completed_at=datetime(2026, 7, 21, 12, 0, 0),
        )


def test_lifecycle_report_to_dict():
    report = LifecycleReport(
        started=("a",),
        stopped=("b",),
        failed=("c",),
        completed_at=BASE_TIME,
    )

    assert report.to_dict() == {
        "started": ["a"],
        "stopped": ["b"],
        "failed": ["c"],
        "completed_at": BASE_TIME.isoformat(),
    }


# --- Registration --------------------------------------------------------


class TestGovernanceLifecycleManagerRegistration:

    def test_register_and_status(self):
        manager = GovernanceLifecycleManager(clock=_clock)
        manager.register("a", start=lambda: None, stop=lambda: None)

        statuses = manager.status()

        assert len(statuses) == 1
        assert statuses[0].name == "a"
        assert statuses[0].started is False

    def test_duplicate_registration_raises(self):
        manager = GovernanceLifecycleManager()
        manager.register("a", start=lambda: None, stop=lambda: None)

        with pytest.raises(ValueError, match="already registered"):
            manager.register("a", start=lambda: None, stop=lambda: None)

    def test_status_reflects_startup_priority_from_dependency_order(
        self,
    ):
        manager = GovernanceLifecycleManager()
        manager.register("b", dependencies=("a",), start=lambda: None, stop=lambda: None)
        manager.register("a", start=lambda: None, stop=lambda: None)

        statuses = {s.name: s for s in manager.status()}

        assert statuses["a"].startup_priority < statuses["b"].startup_priority


# --- Startup order -------------------------------------------------------


class TestGovernanceLifecycleManagerStartupOrder:

    def test_startup_starts_dependencies_before_dependents(self):
        calls: "list[str]" = []

        manager = GovernanceLifecycleManager(clock=_clock)
        manager.register(
            "delivery_runtime",
            dependencies=("provider_registry",),
            start=lambda: calls.append("delivery_runtime"),
            stop=lambda: None,
        )
        manager.register(
            "provider_registry",
            start=lambda: calls.append("provider_registry"),
            stop=lambda: None,
        )

        manager.startup()

        assert calls == ["provider_registry", "delivery_runtime"]

    def test_startup_order_is_deterministic(self):
        def _build():
            manager = GovernanceLifecycleManager()
            manager.register("z", start=lambda: None, stop=lambda: None)
            manager.register("a", start=lambda: None, stop=lambda: None)
            manager.register("m", start=lambda: None, stop=lambda: None)
            return manager

        order_1 = [s.name for s in _build().status()]
        order_2 = [s.name for s in _build().status()]

        assert order_1 == order_2 == ["a", "m", "z"]

    def test_startup_report_lists_started_components(self):
        manager = GovernanceLifecycleManager(clock=_clock)
        manager.register("a", start=lambda: None, stop=lambda: None)
        manager.register("b", start=lambda: None, stop=lambda: None)

        report = manager.startup()

        assert report.started == ("a", "b")
        assert report.failed == ()
        assert report.completed_at == BASE_TIME

    def test_startup_raises_when_graph_invalid(self):
        from backend.observability.deployment_governance_bootstrap import (
            GovernanceBootstrapError,
        )

        manager = GovernanceLifecycleManager()
        manager.register(
            "a", dependencies=("ghost",), start=lambda: None, stop=lambda: None
        )

        with pytest.raises(GovernanceBootstrapError):
            manager.startup()


# --- Reverse shutdown order ------------------------------------------


class TestGovernanceLifecycleManagerShutdownOrder:

    def test_shutdown_stops_dependents_before_dependencies(self):
        calls: "list[str]" = []

        manager = GovernanceLifecycleManager(clock=_clock)
        manager.register(
            "delivery_runtime",
            dependencies=("provider_registry",),
            start=lambda: None,
            stop=lambda: calls.append("delivery_runtime"),
        )
        manager.register(
            "provider_registry",
            start=lambda: None,
            stop=lambda: calls.append("provider_registry"),
        )

        manager.startup()
        manager.shutdown()

        assert calls == ["delivery_runtime", "provider_registry"]

    def test_shutdown_report_lists_stopped_components(self):
        manager = GovernanceLifecycleManager(clock=_clock)
        manager.register("a", start=lambda: None, stop=lambda: None)

        manager.startup()
        report = manager.shutdown()

        assert report.stopped == ("a",)
        assert report.started == ()

    def test_shutdown_only_stops_started_components(self):
        calls: "list[str]" = []

        manager = GovernanceLifecycleManager()
        manager.register(
            "a", start=lambda: None, stop=lambda: calls.append("a")
        )

        manager.shutdown()

        assert calls == []


# --- Restart -------------------------------------------------------------


class TestGovernanceLifecycleManagerRestart:

    def test_restart_stops_then_starts(self):
        calls: "list[str]" = []

        manager = GovernanceLifecycleManager(clock=_clock)
        manager.register(
            "a",
            start=lambda: calls.append("start"),
            stop=lambda: calls.append("stop"),
        )

        manager.startup()
        calls.clear()

        manager.restart()

        assert calls == ["stop", "start"]

    def test_restart_reports_stopped_and_started(self):
        manager = GovernanceLifecycleManager(clock=_clock)
        manager.register("a", start=lambda: None, stop=lambda: None)

        manager.startup()
        report = manager.restart()

        assert report.stopped == ("a",)
        assert report.started == ("a",)

    def test_component_is_started_after_restart(self):
        manager = GovernanceLifecycleManager()
        manager.register("a", start=lambda: None, stop=lambda: None)

        manager.startup()
        manager.restart()

        assert manager.status()[0].started is True


# --- Idempotent operations -----------------------------------------------


class TestGovernanceLifecycleManagerIdempotency:

    def test_second_startup_call_is_a_no_op(self):
        calls: "list[str]" = []

        manager = GovernanceLifecycleManager(clock=_clock)
        manager.register(
            "a", start=lambda: calls.append("start"), stop=lambda: None
        )

        manager.startup()
        report = manager.startup()

        assert calls == ["start"]
        assert report.started == ()

    def test_second_shutdown_call_is_a_no_op(self):
        calls: "list[str]" = []

        manager = GovernanceLifecycleManager(clock=_clock)
        manager.register(
            "a", start=lambda: None, stop=lambda: calls.append("stop")
        )

        manager.startup()
        manager.shutdown()
        report = manager.shutdown()

        assert calls == ["stop"]
        assert report.stopped == ()

    def test_startup_after_partial_registration_only_starts_new_components(
        self,
    ):
        calls: "list[str]" = []

        manager = GovernanceLifecycleManager(clock=_clock)
        manager.register(
            "a", start=lambda: calls.append("a"), stop=lambda: None
        )
        manager.startup()

        manager.register(
            "b", start=lambda: calls.append("b"), stop=lambda: None
        )
        report = manager.startup()

        assert calls == ["a", "b"]
        assert report.started == ("b",)


# --- Startup failure handling ------------------------------------------


class TestGovernanceLifecycleManagerStartupFailures:

    def test_failing_component_is_reported_as_failed(self):
        manager = GovernanceLifecycleManager(clock=_clock)
        manager.register(
            "a",
            start=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
            stop=lambda: None,
        )

        report = manager.startup()

        assert report.failed == ("a",)
        assert report.started == ()

    def test_startup_stops_after_first_failure(self):
        calls: "list[str]" = []

        manager = GovernanceLifecycleManager(clock=_clock)
        manager.register(
            "a",
            start=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
            stop=lambda: None,
        )
        manager.register(
            "b",
            dependencies=("a",),
            start=lambda: calls.append("b"),
            stop=lambda: None,
        )

        report = manager.startup()

        assert calls == []
        assert report.failed == ("a",)

    def test_components_started_before_a_failure_remain_started(self):
        manager = GovernanceLifecycleManager(clock=_clock)
        manager.register("a", start=lambda: None, stop=lambda: None)
        manager.register(
            "b",
            dependencies=("a",),
            start=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
            stop=lambda: None,
        )

        report = manager.startup()

        assert report.started == ("a",)
        statuses = {s.name: s.started for s in manager.status()}
        assert statuses["a"] is True
        assert statuses["b"] is False

    def test_shutdown_continues_after_individual_failures(self):
        calls: "list[str]" = []

        manager = GovernanceLifecycleManager(clock=_clock)
        manager.register(
            "a",
            start=lambda: None,
            stop=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        manager.register(
            "b", start=lambda: None, stop=lambda: calls.append("b")
        )

        manager.startup()
        report = manager.shutdown()

        assert calls == ["b"]
        assert set(report.stopped) == {"b"}
        assert set(report.failed) == {"a"}

    def test_failed_stop_still_marks_component_not_started(self):
        manager = GovernanceLifecycleManager(clock=_clock)
        manager.register(
            "a",
            start=lambda: None,
            stop=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        manager.startup()
        manager.shutdown()

        assert manager.status()[0].started is False


# --- Reload ----------------------------------------------------------------


class TestGovernanceLifecycleManagerReload:

    def test_reload_calls_reload_on_started_components(self):
        calls, start, stop, reload = _recorder()

        manager = GovernanceLifecycleManager(clock=_clock)
        manager.register("a", start=start, stop=stop, reload=reload)

        manager.startup()
        report = manager.reload()

        assert calls == ["start", "reload"]
        assert report.started == ("a",)

    def test_reload_skips_components_without_a_reload_callable(self):
        manager = GovernanceLifecycleManager(clock=_clock)
        manager.register("a", start=lambda: None, stop=lambda: None)

        manager.startup()
        report = manager.reload()

        assert report.started == ()

    def test_reload_skips_components_that_are_not_started(self):
        calls: "list[str]" = []

        manager = GovernanceLifecycleManager(clock=_clock)
        manager.register(
            "a",
            start=lambda: None,
            stop=lambda: None,
            reload=lambda: calls.append("reload"),
        )

        manager.reload()

        assert calls == []


# --- Singleton -------------------------------------------------------------


class TestGovernanceLifecycleSingleton:

    def test_get_lifecycle_manager_returns_same_instance(self):
        assert get_lifecycle_manager() is get_lifecycle_manager()

    def test_default_manager_has_all_nine_components(self):
        names = {c.name for c in get_lifecycle_manager().status()}

        assert names == {
            "provider_registry",
            "metrics_bootstrap",
            "logging_bootstrap",
            "delivery_runtime",
            "health_service",
            "readiness_service",
            "liveness_service",
            "diagnostics_service",
            "scheduler",
        }

    def test_default_manager_startup_and_shutdown_succeed(self):
        manager = get_lifecycle_manager()

        startup_report = manager.startup()

        assert startup_report.failed == ()
        assert all(c.started for c in manager.status())

        shutdown_report = manager.shutdown()

        assert shutdown_report.failed == ()
        assert all(not c.started for c in manager.status())

    def test_default_manager_liveness_component_drives_real_singleton(
        self,
    ):
        from backend.observability.deployment_governance_liveness import (
            get_liveness_service,
        )

        # Captured once: get_liveness_service() itself auto-starts on
        # access (by design, see commit #3), so calling it again
        # after shutdown() would immediately re-arm liveness and mask
        # exactly the state this test means to observe.
        liveness_service = get_liveness_service()

        manager = get_lifecycle_manager()
        manager.startup()

        assert liveness_service.check().alive is True

        manager.shutdown()

        assert liveness_service.check().alive is False


# --- Bootstrap wiring (real delivery runtime) ---------------------------


class TestBuildGovernanceLifecycleManagerFromDeliveryRuntime:

    def _delivery_runtime(self):
        from unittest.mock import Mock

        from backend.observability.deployment_governance_delivery_runtime import (
            build_integrity_delivery_runtime,
        )
        from backend.observability.deployment_governance_liveness import (
            GovernanceLivenessService,
        )

        provider_registry = Mock(spec=["list_providers"])
        provider_registry.list_providers.return_value = []

        return build_integrity_delivery_runtime(
            worker=Mock(),
            scheduler=Mock(),
            provider_registry=provider_registry,
            liveness_service=GovernanceLivenessService(),
        )

    def test_starting_the_manager_starts_the_delivery_runtime(self):
        from backend.observability.deployment_governance_bootstrap import (
            build_governance_lifecycle_manager,
        )

        runtime = self._delivery_runtime()
        manager = build_governance_lifecycle_manager(runtime)

        manager.startup()

        try:
            assert runtime.is_running() is True

        finally:
            manager.shutdown()

    def test_stopping_the_manager_stops_the_delivery_runtime(self):
        from backend.observability.deployment_governance_bootstrap import (
            build_governance_lifecycle_manager,
        )

        runtime = self._delivery_runtime()
        manager = build_governance_lifecycle_manager(runtime)

        manager.startup()
        manager.shutdown()

        assert runtime.is_running() is False


# --- Health check adapter ------------------------------------------------


class TestLifecycleHealthCheck:

    def test_healthy_when_all_started(self):
        from backend.observability.deployment_governance_health import (
            lifecycle_health_check,
        )

        manager = GovernanceLifecycleManager()
        manager.register("a", start=lambda: None, stop=lambda: None)
        manager.startup()

        assert lifecycle_health_check(manager) is True

    def test_unhealthy_when_some_not_started(self):
        from backend.observability.deployment_governance_health import (
            lifecycle_health_check,
        )

        manager = GovernanceLifecycleManager()
        manager.register("a", start=lambda: None, stop=lambda: None)

        healthy, message = lifecycle_health_check(manager)

        assert healthy is False
        assert "a" in message


# --- API endpoints -----------------------------------------------------


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


class TestGovernanceLifecycleApi:

    def test_start_endpoint_starts_components(
        self, client, monkeypatch, tmp_path
    ) -> None:
        _setup_sqlite_env(monkeypatch, tmp_path, "api-lifecycle-start.db")

        response = client.post("/governance/lifecycle/start")

        assert response.status_code == 200

        payload = response.json()

        assert set(payload["started"]) == {
            "provider_registry",
            "metrics_bootstrap",
            "logging_bootstrap",
            "delivery_runtime",
            "health_service",
            "readiness_service",
            "liveness_service",
            "diagnostics_service",
            "scheduler",
        }
        assert payload["failed"] == []

    def test_status_endpoint_reflects_started_components(
        self, client, monkeypatch, tmp_path
    ) -> None:
        _setup_sqlite_env(monkeypatch, tmp_path, "api-lifecycle-status.db")

        client.post("/governance/lifecycle/start")

        response = client.get("/governance/lifecycle/status")

        assert response.status_code == 200

        payload = response.json()

        assert all(component["started"] for component in payload)

    def test_stop_endpoint_stops_components(
        self, client, monkeypatch, tmp_path
    ) -> None:
        _setup_sqlite_env(monkeypatch, tmp_path, "api-lifecycle-stop.db")

        client.post("/governance/lifecycle/start")

        response = client.post("/governance/lifecycle/stop")

        assert response.status_code == 200

        payload = response.json()

        assert len(payload["stopped"]) == 9
        assert payload["failed"] == []

        status_response = client.get("/governance/lifecycle/status")

        assert all(
            not component["started"]
            for component in status_response.json()
        )

    def test_restart_endpoint_leaves_everything_started(
        self, client, monkeypatch, tmp_path
    ) -> None:
        _setup_sqlite_env(monkeypatch, tmp_path, "api-lifecycle-restart.db")

        client.post("/governance/lifecycle/start")

        response = client.post("/governance/lifecycle/restart")

        assert response.status_code == 200

        payload = response.json()

        assert len(payload["stopped"]) == 9
        assert len(payload["started"]) == 9

        status_response = client.get("/governance/lifecycle/status")

        assert all(
            component["started"]
            for component in status_response.json()
        )
