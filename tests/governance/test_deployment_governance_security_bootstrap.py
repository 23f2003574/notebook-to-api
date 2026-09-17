from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_event_bus import (
    GovernanceEventBus,
)
from backend.observability.deployment_governance_security_bootstrap import (
    BOOTSTRAP_VERSION,
    DeploymentSecurityBootstrap,
    DeploymentSecurityBootstrapError,
    SecurityBootstrapReport,
    SecurityBootstrapStatus,
    get_security_bootstrap,
)

BASE_TIME = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


def _bootstrap(**kwargs) -> DeploymentSecurityBootstrap:
    return DeploymentSecurityBootstrap(clock=_clock, **kwargs)


def _fully_wired_bootstrap(**overrides) -> DeploymentSecurityBootstrap:
    """
    Construct a bootstrap with every one of the twelve security
    subsystem components wired to a fresh, isolated instance (not the
    process-wide singletons) — the shape most tests below need to
    exercise register_services()/health_check() meaningfully.
    """

    from backend.observability.deployment_governance_approval import (
        DeploymentApprovalEngine,
    )
    from backend.observability.deployment_governance_artifact_integrity import (  # noqa: E501
        DeploymentIntegrityVerifier,
    )
    from backend.observability.deployment_governance_audit import (
        GovernanceAuditService,
    )
    from backend.observability.deployment_governance_audit_trail import (
        DeploymentAuditService,
    )
    from backend.observability.deployment_governance_authentication import (
        DeploymentAuthenticationManager,
    )
    from backend.observability.deployment_governance_compliance import (
        DeploymentComplianceEngine,
    )
    from backend.observability.deployment_governance_incident_response import (  # noqa: E501
        DeploymentIncidentResponseEngine,
    )
    from backend.observability.deployment_governance_rbac import (
        DeploymentRBACEngine,
    )
    from backend.observability.deployment_governance_reporting import (
        DeploymentReportingService,
    )
    from backend.observability.deployment_governance_risk import (
        DeploymentRiskEngine,
    )
    from backend.observability.deployment_governance_secret_vault import (
        DeploymentSecretVault,
    )
    from backend.observability.deployment_governance_security_dashboard import (  # noqa: E501
        DeploymentSecurityDashboard,
    )
    from backend.observability.deployment_governance_security_scanner import (  # noqa: E501
        DeploymentSecurityScanner,
    )

    components = {
        "authentication_manager": DeploymentAuthenticationManager(
            clock=_clock
        ),
        "rbac_engine": DeploymentRBACEngine(clock=_clock),
        "secret_vault": DeploymentSecretVault(
            clock=_clock, environment={},
        ),
        "approval_engine": DeploymentApprovalEngine(clock=_clock),
        "audit_service": DeploymentAuditService(
            audit_service=GovernanceAuditService(clock=_clock)
        ),
        "compliance_engine": DeploymentComplianceEngine(clock=_clock),
        "risk_engine": DeploymentRiskEngine(clock=_clock),
        "security_scanner": DeploymentSecurityScanner(clock=_clock),
        "integrity_verifier": DeploymentIntegrityVerifier(clock=_clock),
        "incident_response_engine": DeploymentIncidentResponseEngine(
            clock=_clock
        ),
        "reporting_service": DeploymentReportingService(clock=_clock),
        "security_dashboard": DeploymentSecurityDashboard(clock=_clock),
    }

    components.update(overrides)

    return DeploymentSecurityBootstrap(clock=_clock, **components)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """
    The security bootstrap is a process-wide singleton wired to every
    other governance singleton; most tests below construct their own
    fresh bootstrap instead (see _bootstrap/_fully_wired_bootstrap),
    and only the singleton and API tests touch the shared instance.
    Unlike other singletons in this series, this one has real
    initialized/subscribed state of its own to reset.
    """

    def _reset():
        bootstrap = get_security_bootstrap()

        if bootstrap._initialized:
            bootstrap.shutdown()

    _reset()
    yield
    _reset()


# --- Models --------------------------------------------------------------


class TestSecurityBootstrapReport:

    def test_rejects_naive_completed_at(self):
        with pytest.raises(
            ValueError, match="completed_at must be timezone-aware"
        ):
            SecurityBootstrapReport(
                started=True, initialized_components=(),
                registered_routes=True, subscribed_events=(),
                completed_at=datetime(2026, 7, 24, 12, 0, 0),
            )

    def test_to_dict(self):
        report = SecurityBootstrapReport(
            started=True, initialized_components=("rbac_engine",),
            registered_routes=True,
            subscribed_events=("authentication_succeeded",),
            completed_at=BASE_TIME,
        )

        assert report.to_dict() == {
            "started": True,
            "initialized_components": ["rbac_engine"],
            "registered_routes": True,
            "subscribed_events": ["authentication_succeeded"],
            "completed_at": BASE_TIME.isoformat(),
        }


class TestSecurityBootstrapStatus:

    def test_rejects_naive_started_at(self):
        with pytest.raises(
            ValueError, match="started_at must be timezone-aware"
        ):
            SecurityBootstrapStatus(
                initialized=True, version="1",
                started_at=datetime(2026, 7, 24, 12, 0, 0),
            )

    def test_to_dict_with_no_start_time(self):
        status = SecurityBootstrapStatus(
            initialized=False, version="1", started_at=None,
        )

        assert status.to_dict() == {
            "initialized": False, "version": "1", "started_at": None,
        }


# --- Bootstrap initialization ----------------------------------------


class TestBootstrapInitialization:

    def test_initialize_returns_report(self):
        bootstrap = _fully_wired_bootstrap()

        report = bootstrap.initialize()

        assert report.started is True
        assert report.completed_at == BASE_TIME

    def test_initialize_is_idempotent(self):
        bootstrap = _fully_wired_bootstrap()

        first = bootstrap.initialize()
        second = bootstrap.initialize()

        assert first is second

    def test_initialize_sets_status_initialized(self):
        bootstrap = _fully_wired_bootstrap()

        bootstrap.initialize()

        status = bootstrap.status()

        assert status.initialized is True
        assert status.version == BOOTSTRAP_VERSION
        assert status.started_at == BASE_TIME

    def test_publishes_bootstrap_lifecycle_events(self):
        bus = GovernanceEventBus()
        events = []

        for event_type in (
            "security_bootstrap_started", "security_bootstrap_completed",
            "security_runtime_ready",
        ):
            bus.subscribe(event_type, events.append)

        bootstrap = _fully_wired_bootstrap(event_bus=bus)

        bootstrap.initialize()

        assert [e.event_type for e in events] == [
            "security_bootstrap_started", "security_bootstrap_completed",
            "security_runtime_ready",
        ]


# --- Dependency validation ------------------------------------------------


class TestDependencyValidation:

    def test_empty_bootstrap_validates_trivially(self):
        bootstrap = _bootstrap()

        result = bootstrap.validate()

        assert result.valid is True

    def test_fully_wired_bootstrap_validates(self):
        bootstrap = _fully_wired_bootstrap()

        result = bootstrap.validate()

        assert result.valid is True

    def test_register_services_returns_wired_components_in_order(self):
        bootstrap = _fully_wired_bootstrap()

        components = bootstrap.register_services()

        assert components == (
            "authentication_manager", "rbac_engine", "secret_vault",
            "approval_engine", "audit_service", "compliance_engine",
            "risk_engine", "security_scanner", "integrity_verifier",
            "incident_response_engine", "reporting_service",
            "security_dashboard",
        )

    def test_leading_subset_wired_bootstrap_omits_the_rest(self):
        from backend.observability.deployment_governance_authentication import (  # noqa: E501
            DeploymentAuthenticationManager,
        )

        bootstrap = _bootstrap(
            authentication_manager=DeploymentAuthenticationManager(
                clock=_clock
            )
        )

        components = bootstrap.register_services()

        assert components == ("authentication_manager",)

    def test_middle_component_without_predecessor_fails_validation(self):
        """
        The fixed linear chain means a present component's dependency
        points at the immediately preceding name in
        _SECURITY_COMPONENT_ORDER, whether or not that predecessor is
        also wired — the same DeploymentRolloutBootstrap.validate
        behavior this bootstrap mirrors. Wiring "rbac_engine" (second
        in the chain) without "authentication_manager" (first) is a
        genuine gap, not a partial-but-valid state.
        """

        from backend.observability.deployment_governance_rbac import (
            DeploymentRBACEngine,
        )

        bootstrap = _bootstrap(rbac_engine=DeploymentRBACEngine(clock=_clock))

        with pytest.raises(DeploymentSecurityBootstrapError):
            bootstrap.register_services()

    def test_initialize_raises_on_invalid_graph(self, monkeypatch):
        import backend.observability.deployment_governance_security_bootstrap as bootstrap_module  # noqa: E501
        from backend.observability.deployment_governance_dependency_graph import (  # noqa: E501
            GovernanceDependencyGraph,
        )

        bootstrap = _fully_wired_bootstrap()

        def _broken_validate():
            graph = GovernanceDependencyGraph()
            graph.register("a", dependencies=("missing",))
            return graph.validate()

        monkeypatch.setattr(bootstrap, "validate", _broken_validate)

        with pytest.raises(
            bootstrap_module.DeploymentSecurityBootstrapError
        ):
            bootstrap.initialize()

    def test_failed_initialize_publishes_bootstrap_failed(
        self, monkeypatch
    ):
        from backend.observability.deployment_governance_dependency_graph import (  # noqa: E501
            GovernanceDependencyGraph,
        )

        bus = GovernanceEventBus()
        events = []
        bus.subscribe("security_bootstrap_failed", events.append)

        bootstrap = _fully_wired_bootstrap(event_bus=bus)

        def _broken_validate():
            graph = GovernanceDependencyGraph()
            graph.register("a", dependencies=("missing",))
            return graph.validate()

        monkeypatch.setattr(bootstrap, "validate", _broken_validate)

        with pytest.raises(DeploymentSecurityBootstrapError):
            bootstrap.initialize()

        assert len(events) == 1


# --- Service registration --------------------------------------------


class TestServiceRegistration:

    def test_service_registration_order_matches_runtime_integration(self):
        assert (
            list(_fully_wired_bootstrap().register_services())
            == [
                "authentication_manager", "rbac_engine", "secret_vault",
                "approval_engine", "audit_service", "compliance_engine",
                "risk_engine", "security_scanner", "integrity_verifier",
                "incident_response_engine", "reporting_service",
                "security_dashboard",
            ]
        )

    def test_no_new_features_every_component_is_pre_existing_singleton(
        self,
    ):
        """
        Confirms this bootstrap only ever references already-built
        components — it does not construct any security logic of its
        own ("no new security features should be introduced").
        """

        bootstrap = get_security_bootstrap()

        for name in (
            "authentication_manager", "rbac_engine", "secret_vault",
            "approval_engine", "audit_service", "compliance_engine",
            "risk_engine", "security_scanner", "integrity_verifier",
            "incident_response_engine", "reporting_service",
            "security_dashboard",
        ):
            assert bootstrap._component(name) is not None


# --- Route registration ------------------------------------------------


class TestRouteRegistration:

    def test_register_routes_confirms_prefix(self):
        bootstrap = _fully_wired_bootstrap()

        assert bootstrap.register_routes() is True

    def test_security_endpoints_are_mounted(self):
        from backend.observability.deployment_governance_api import (
            health_router,
        )

        paths = {route.path for route in health_router.routes}

        assert "/governance/security/roles" in paths
        assert "/governance/security/dashboard" in paths
        assert "/governance/security/bootstrap" in paths


# --- Health check -------------------------------------------------------


class TestHealthCheck:

    def test_health_check_false_before_initialize(self):
        bootstrap = _fully_wired_bootstrap()

        healthy, reason = bootstrap.health_check()

        assert healthy is False
        assert "not been initialized" in reason

    def test_health_check_true_after_initialize(self):
        bootstrap = _fully_wired_bootstrap()
        bootstrap.initialize()

        healthy, reason = bootstrap.health_check()

        assert healthy is True
        assert reason is None

    def test_health_check_reports_missing_components(self):
        """
        Once initialized, health_check() re-checks every component in
        _SECURITY_COMPONENT_ORDER directly (not through validate()'s
        chain), so a bootstrap missing a later component still
        reports it as missing even though initialize() itself only
        requires the *wired* subset to form a valid leading chain.
        """

        from backend.observability.deployment_governance_authentication import (  # noqa: E501
            DeploymentAuthenticationManager,
        )

        bootstrap = _bootstrap(
            authentication_manager=DeploymentAuthenticationManager(
                clock=_clock
            )
        )
        bootstrap.initialize()

        healthy, reason = bootstrap.health_check()

        assert healthy is False
        assert "rbac_engine" in reason

    def test_register_event_handlers_subscribes_tracked_events(self):
        bus = GovernanceEventBus()
        bootstrap = _fully_wired_bootstrap(event_bus=bus)

        subscribed = bootstrap.register_event_handlers()

        assert "authentication_succeeded" in subscribed
        assert "security_dashboard_generated" in subscribed

    def test_register_event_handlers_idempotent(self):
        bus = GovernanceEventBus()
        bootstrap = _fully_wired_bootstrap(event_bus=bus)

        first = bootstrap.register_event_handlers()
        second = bootstrap.register_event_handlers()

        assert first == second

    def test_register_event_handlers_empty_without_event_bus(self):
        bootstrap = _fully_wired_bootstrap()

        assert bootstrap.register_event_handlers() == ()

    def test_last_event_at_tracks_tracked_events(self):
        bus = GovernanceEventBus()
        bootstrap = _fully_wired_bootstrap(event_bus=bus)
        bootstrap.register_event_handlers()

        bus.publish("authentication_succeeded", source="i1", payload={})

        assert bootstrap.last_event_at("authentication_succeeded") == (
            BASE_TIME
        )

    def test_last_event_at_none_for_unseen_event(self):
        bootstrap = _fully_wired_bootstrap()

        assert bootstrap.last_event_at("authentication_succeeded") is None


# --- Graceful shutdown ---------------------------------------------------


class TestGracefulShutdown:

    def test_shutdown_resets_status(self):
        bootstrap = _fully_wired_bootstrap()
        bootstrap.initialize()

        bootstrap.shutdown()

        status = bootstrap.status()

        assert status.initialized is False
        assert status.started_at is None

    def test_shutdown_is_a_no_op_when_not_initialized(self):
        bootstrap = _fully_wired_bootstrap()

        bootstrap.shutdown()

        assert bootstrap.status().initialized is False

    def test_shutdown_unsubscribes_events(self):
        bus = GovernanceEventBus()
        bootstrap = _fully_wired_bootstrap(event_bus=bus)
        bootstrap.initialize()

        bootstrap.shutdown()

        bus.publish("authentication_succeeded", source="i1", payload={})

        assert bootstrap.last_event_at("authentication_succeeded") is None

    def test_shutdown_publishes_runtime_shutdown(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("security_runtime_shutdown", events.append)
        bootstrap = _fully_wired_bootstrap(event_bus=bus)
        bootstrap.initialize()

        bootstrap.shutdown()

        assert len(events) == 1

    def test_shutdown_refreshes_dashboard(self):
        from backend.observability.deployment_governance_security_dashboard import (  # noqa: E501
            DeploymentSecurityDashboard,
        )

        refreshed = {"called": False}

        class _TrackingDashboard(DeploymentSecurityDashboard):
            def refresh(self):
                refreshed["called"] = True
                return super().refresh()

        bootstrap = _fully_wired_bootstrap(
            security_dashboard=_TrackingDashboard(clock=_clock)
        )
        bootstrap.initialize()

        bootstrap.shutdown()

        assert refreshed["called"] is True

    def test_restart_shuts_down_then_reinitializes(self):
        bootstrap = _fully_wired_bootstrap()

        first = bootstrap.initialize()
        second = bootstrap.restart()

        assert first is not second
        assert bootstrap.status().initialized is True


# --- Singleton ---------------------------------------------------------


class TestSingleton:

    def test_get_security_bootstrap_returns_same_instance(self):
        assert get_security_bootstrap() is get_security_bootstrap()

    def test_singleton_is_wired_to_every_component(self):
        bootstrap = get_security_bootstrap()

        for name in (
            "authentication_manager", "rbac_engine", "secret_vault",
            "approval_engine", "audit_service", "compliance_engine",
            "risk_engine", "security_scanner", "integrity_verifier",
            "incident_response_engine", "reporting_service",
            "security_dashboard",
        ):
            assert bootstrap._component(name) is not None


# --- Top-level governance dependency graph integration ------------------


class TestTopLevelDependencyGraphIntegration:

    def test_security_bootstrap_is_in_top_level_graph(self):
        from backend.observability.deployment_governance_bootstrap import (
            build_governance_dependency_graph,
        )

        names = {
            c.name
            for c in build_governance_dependency_graph().components()
        }

        assert "security_bootstrap" in names

    def test_top_level_graph_still_validates(self):
        from backend.observability.deployment_governance_bootstrap import (
            build_governance_dependency_graph,
        )

        result = build_governance_dependency_graph().validate()

        assert result.valid is True


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceSecurityBootstrapApi:

    def test_get_status(self, client):
        response = client.get("/governance/security/bootstrap")

        assert response.status_code == 200
        assert "initialized" in response.json()

    def test_post_initializes(self, client):
        response = client.post("/governance/security/bootstrap")

        assert response.status_code == 200
        assert response.json()["started"] is True

    def test_post_restart(self, client):
        client.post("/governance/security/bootstrap")

        response = client.post(
            "/governance/security/bootstrap/restart"
        )

        assert response.status_code == 200
        assert response.json()["started"] is True
