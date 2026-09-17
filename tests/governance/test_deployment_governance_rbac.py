from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_event_bus import (
    GovernanceEventBus,
)
from backend.observability.deployment_governance_rbac import (
    BUILT_IN_DEPLOYMENT_PERMISSIONS,
    BUILT_IN_DEPLOYMENT_ROLES,
    AuthorizationDecision,
    DeploymentPrincipal,
    DeploymentRBACEngine,
    DeploymentRole,
    get_rbac_engine,
)

BASE_TIME = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


def _engine(**kwargs) -> DeploymentRBACEngine:
    return DeploymentRBACEngine(clock=_clock, **kwargs)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """
    The RBAC engine is a process-wide singleton; most tests below
    construct their own fresh engine instead (see _engine), and only
    the singleton and API tests touch the shared instance, matching
    test_deployment_rollout_policy.py's own fixture.
    """

    def _reset():
        get_rbac_engine().clear()

    _reset()
    yield
    _reset()


# --- Models --------------------------------------------------------------


class TestDeploymentRole:

    def test_rejects_empty_name(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            DeploymentRole(name="", permissions=frozenset())

    def test_permissions_are_frozen(self):
        role = DeploymentRole(
            name="r", permissions={"deployment.read", "deployment.read"}
        )

        assert role.permissions == frozenset({"deployment.read"})

    def test_to_dict(self):
        role = DeploymentRole(
            name="r",
            permissions={"deployment.deploy", "deployment.read"},
        )

        assert role.to_dict() == {
            "name": "r",
            "permissions": ["deployment.deploy", "deployment.read"],
        }


class TestDeploymentPrincipal:

    def test_rejects_empty_principal_id(self):
        with pytest.raises(
            ValueError, match="principal_id must not be empty"
        ):
            DeploymentPrincipal(principal_id="", roles=())

    def test_to_dict(self):
        principal = DeploymentPrincipal(
            principal_id="p1", roles=("Developer", "Auditor")
        )

        assert principal.to_dict() == {
            "principal_id": "p1",
            "roles": ["Developer", "Auditor"],
        }


class TestAuthorizationDecision:

    def test_rejects_empty_principal_id(self):
        with pytest.raises(
            ValueError, match="principal_id must not be empty"
        ):
            AuthorizationDecision(
                principal_id="", permission="deployment.read",
                allowed=True, roles=(), evaluated_at=BASE_TIME,
            )

    def test_rejects_empty_permission(self):
        with pytest.raises(
            ValueError, match="permission must not be empty"
        ):
            AuthorizationDecision(
                principal_id="p1", permission="", allowed=True,
                roles=(), evaluated_at=BASE_TIME,
            )

    def test_rejects_naive_evaluated_at(self):
        with pytest.raises(
            ValueError, match="evaluated_at must be timezone-aware"
        ):
            AuthorizationDecision(
                principal_id="p1", permission="deployment.read",
                allowed=True, roles=(),
                evaluated_at=datetime(2026, 7, 24, 12, 0, 0),
            )

    def test_to_dict(self):
        decision = AuthorizationDecision(
            principal_id="p1", permission="deployment.read",
            allowed=True, roles=("Read Only",), evaluated_at=BASE_TIME,
        )

        assert decision.to_dict() == {
            "principal_id": "p1",
            "permission": "deployment.read",
            "allowed": True,
            "roles": ["Read Only"],
            "evaluated_at": BASE_TIME.isoformat(),
        }


# --- Built-in roles and permissions ---------------------------------------


class TestBuiltInRoles:

    def test_every_built_in_role_is_pre_registered(self):
        engine = _engine()

        names = {role.name for role in engine.roles()}

        assert names == set(BUILT_IN_DEPLOYMENT_ROLES)

    def test_administrator_holds_every_built_in_permission(self):
        engine = _engine()
        engine.assign_role("admin-1", "Administrator")

        assert engine.permissions("admin-1") == frozenset(
            BUILT_IN_DEPLOYMENT_PERMISSIONS
        )

    def test_read_only_holds_only_deployment_read(self):
        engine = _engine()
        engine.assign_role("viewer-1", "Read Only")

        assert engine.permissions("viewer-1") == frozenset(
            {"deployment.read"}
        )


# --- Role registration -----------------------------------------------------


class TestRoleRegistration:

    def test_register_role(self):
        engine = _engine()

        role = engine.register_role(
            "Custom", ["deployment.read", "deployment.deploy"]
        )

        assert role.name == "Custom"
        assert role.permissions == frozenset(
            {"deployment.read", "deployment.deploy"}
        )
        assert role in engine.roles()

    def test_rejects_empty_name(self):
        engine = _engine()

        with pytest.raises(ValueError, match="name must not be empty"):
            engine.register_role("", [])

    def test_publishes_role_registered(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("role_registered", events.append)
        engine = _engine(event_bus=bus)

        engine.register_role("Custom", ["deployment.read"])

        assert len(events) == 1
        assert events[0].source == "Custom"

    def test_remove_role(self):
        engine = _engine()
        engine.register_role("Custom", ["deployment.read"])

        engine.remove_role("Custom")

        assert "Custom" not in {role.name for role in engine.roles()}

    def test_remove_unknown_role_raises(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.remove_role("does-not-exist")

    def test_remove_role_revokes_it_from_every_principal(self):
        engine = _engine()
        engine.register_role("Custom", ["deployment.read"])
        engine.assign_role("p1", "Custom")

        engine.remove_role("Custom")

        assert engine.principal_roles("p1") == ()

    def test_update_role_permissions(self):
        engine = _engine()
        engine.register_role("Custom", ["deployment.read"])

        updated = engine.update_role_permissions(
            "Custom", ["deployment.deploy"]
        )

        assert updated.permissions == frozenset({"deployment.deploy"})

    def test_update_unknown_role_raises(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.update_role_permissions("does-not-exist", [])


class TestDuplicateRejection:

    def test_duplicate_name_raises(self):
        engine = _engine()
        engine.register_role("Custom", [])

        with pytest.raises(
            ValueError, match="already registered"
        ):
            engine.register_role("Custom", [])

    def test_built_in_name_is_rejected_as_duplicate(self):
        engine = _engine()

        with pytest.raises(
            ValueError, match="already registered"
        ):
            engine.register_role("Administrator", [])


# --- Role assignment --------------------------------------------------------


class TestRoleAssignment:

    def test_assign_role(self):
        engine = _engine()

        principal = engine.assign_role("p1", "Developer")

        assert principal.principal_id == "p1"
        assert principal.roles == ("Developer",)

    def test_assign_unknown_role_raises(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.assign_role("p1", "does-not-exist")

    def test_assign_is_idempotent(self):
        engine = _engine()
        engine.assign_role("p1", "Developer")

        principal = engine.assign_role("p1", "Developer")

        assert principal.roles == ("Developer",)

    def test_assign_multiple_roles(self):
        engine = _engine()
        engine.assign_role("p1", "Developer")

        principal = engine.assign_role("p1", "Auditor")

        assert principal.roles == ("Auditor", "Developer")

    def test_revoke_role(self):
        engine = _engine()
        engine.assign_role("p1", "Developer")

        principal = engine.revoke_role("p1", "Developer")

        assert principal.roles == ()

    def test_revoke_from_unregistered_principal_raises(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.revoke_role("does-not-exist", "Developer")

    def test_revoke_is_idempotent(self):
        engine = _engine()
        engine.assign_role("p1", "Developer")
        engine.revoke_role("p1", "Developer")

        principal = engine.revoke_role("p1", "Developer")

        assert principal.roles == ()


# --- Permission inheritance --------------------------------------------------


class TestPermissionInheritance:

    def test_permissions_union_across_multiple_roles(self):
        engine = _engine()
        engine.assign_role("p1", "Developer")
        engine.assign_role("p1", "Auditor")

        assert engine.permissions("p1") == frozenset(
            {"deployment.read", "deployment.deploy", "audit.read"}
        )

    def test_permissions_empty_for_unknown_principal(self):
        engine = _engine()

        assert engine.permissions("does-not-exist") == frozenset()


# --- Authorization -----------------------------------------------------------


class TestAuthorizationSuccess:

    def test_allowed_when_role_grants_permission(self):
        engine = _engine()
        engine.assign_role("p1", "Developer")

        decision = engine.authorize("p1", "deployment.deploy")

        assert decision.allowed is True
        assert decision.roles == ("Developer",)

    def test_publishes_authorization_granted(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("authorization_granted", events.append)
        engine = _engine(event_bus=bus)
        engine.assign_role("p1", "Developer")

        engine.authorize("p1", "deployment.deploy")

        assert len(events) == 1
        assert events[0].payload["allowed"] is True


class TestAuthorizationFailure:

    def test_denied_when_no_role_grants_permission(self):
        engine = _engine()
        engine.assign_role("p1", "Read Only")

        decision = engine.authorize("p1", "deployment.deploy")

        assert decision.allowed is False

    def test_denied_for_unknown_principal(self):
        engine = _engine()

        decision = engine.authorize("does-not-exist", "deployment.read")

        assert decision.allowed is False
        assert decision.roles == ()

    def test_publishes_authorization_denied(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("authorization_denied", events.append)
        engine = _engine(event_bus=bus)

        engine.authorize("p1", "deployment.deploy")

        assert len(events) == 1
        assert events[0].payload["allowed"] is False


class TestMultiRoleEvaluation:

    def test_allowed_when_only_one_of_several_roles_grants_it(self):
        engine = _engine()
        engine.assign_role("p1", "Read Only")
        engine.assign_role("p1", "Auditor")

        decision = engine.authorize("p1", "audit.read")

        assert decision.allowed is True
        assert set(decision.roles) == {"Read Only", "Auditor"}

    def test_denied_when_no_assigned_role_grants_it(self):
        engine = _engine()
        engine.assign_role("p1", "Read Only")
        engine.assign_role("p1", "Developer")

        decision = engine.authorize("p1", "security.manage")

        assert decision.allowed is False


class TestAuthorizationValidation:

    def test_rejects_empty_principal_id(self):
        engine = _engine()

        with pytest.raises(
            ValueError, match="principal_id must not be empty"
        ):
            engine.authorize("", "deployment.read")

    def test_rejects_empty_permission(self):
        engine = _engine()

        with pytest.raises(
            ValueError, match="permission must not be empty"
        ):
            engine.authorize("p1", "")

    def test_is_deterministic(self):
        engine = _engine()
        engine.assign_role("p1", "Developer")

        first = engine.authorize("p1", "deployment.deploy")
        second = engine.authorize("p1", "deployment.deploy")

        assert first.allowed is True
        assert second.allowed is True


# --- Audit integration -------------------------------------------------------


class TestAuditIntegration:

    def test_authorize_records_audit_entry(self):
        from backend.observability.deployment_governance_audit import (
            GovernanceAuditService,
        )

        audit_service = GovernanceAuditService(clock=_clock)
        engine = _engine(audit_service=audit_service)
        engine.assign_role("p1", "Developer")

        engine.authorize("p1", "deployment.deploy")

        records = audit_service.latest()

        # 2, not 1: assign_role() itself now also records a
        # "role_assigned" audit entry (see TestRoleAssignmentAuditing
        # below) — records[0] (newest first) is still authorize()'s
        # own entry.
        assert len(records) == 2
        assert records[0].action == "authorization_granted"
        assert records[0].outcome == "success"


# --- Clear -------------------------------------------------------------------


class TestClear:

    def test_clear_restores_built_in_roles(self):
        engine = _engine()
        engine.register_role("Custom", [])

        engine.clear()

        names = {role.name for role in engine.roles()}

        assert names == set(BUILT_IN_DEPLOYMENT_ROLES)

    def test_clear_removes_principal_assignments(self):
        engine = _engine()
        engine.assign_role("p1", "Developer")

        engine.clear()

        assert engine.permissions("p1") == frozenset()


# --- Runtime integration (rollout manager / policy / dashboard) -------------


class TestRuntimeIntegration:

    def test_rollout_manager_create_denied_without_permission(self):
        from backend.observability.deployment_governance_rollout_manager import (  # noqa: E501
            DeploymentRolloutManager,
        )

        rbac_engine = _engine()
        rbac_engine.assign_role("p1", "Read Only")

        manager = DeploymentRolloutManager(
            clock=_clock, rbac_engine=rbac_engine,
        )

        with pytest.raises(PermissionError):
            manager.create("dep-1", "CANARY", principal_id="p1")

    def test_rollout_manager_create_allowed_with_permission(self):
        from backend.observability.deployment_governance_rollout_manager import (  # noqa: E501
            DeploymentRolloutManager,
        )

        rbac_engine = _engine()
        rbac_engine.assign_role("p1", "Developer")

        manager = DeploymentRolloutManager(
            clock=_clock, rbac_engine=rbac_engine,
        )

        rollout = manager.create(
            "dep-1", "CANARY", principal_id="p1"
        )

        assert rollout.deployment_id == "dep-1"

    def test_rollout_manager_create_skips_check_without_principal_id(
        self,
    ):
        from backend.observability.deployment_governance_rollout_manager import (  # noqa: E501
            DeploymentRolloutManager,
        )

        rbac_engine = _engine()

        manager = DeploymentRolloutManager(
            clock=_clock, rbac_engine=rbac_engine,
        )

        rollout = manager.create("dep-1", "CANARY")

        assert rollout.deployment_id == "dep-1"

    def test_rollout_policy_register_denied_without_permission(self):
        from backend.observability.deployment_governance_rollout_policy import (  # noqa: E501
            DeploymentRolloutPolicyEngine,
        )

        rbac_engine = _engine()
        rbac_engine.assign_role("p1", "Read Only")

        engine = DeploymentRolloutPolicyEngine(
            clock=_clock, rbac_engine=rbac_engine,
        )

        with pytest.raises(PermissionError):
            engine.register("deny-all", principal_id="p1")

    def test_rollout_dashboard_refresh_denied_without_permission(self):
        from backend.observability.deployment_governance_rollout_dashboard import (  # noqa: E501
            DeploymentRolloutDashboard,
        )

        rbac_engine = _engine()
        rbac_engine.assign_role("p1", "Read Only")

        dashboard = DeploymentRolloutDashboard(
            clock=_clock, rbac_engine=rbac_engine,
        )

        with pytest.raises(PermissionError):
            dashboard.refresh(principal_id="p1")

    def test_singleton_is_wired_into_rollout_manager_policy_dashboard(
        self,
    ):
        from backend.observability.deployment_governance_rollout_dashboard import (  # noqa: E501
            get_rollout_dashboard,
        )
        from backend.observability.deployment_governance_rollout_manager import (  # noqa: E501
            get_rollout_manager,
        )
        from backend.observability.deployment_governance_rollout_policy import (  # noqa: E501
            get_rollout_policy_engine,
        )

        engine = get_rbac_engine()

        assert get_rollout_manager()._rbac_engine is engine
        assert get_rollout_policy_engine()._rbac_engine is engine
        assert get_rollout_dashboard()._rbac_engine is engine


# --- Singleton ---------------------------------------------------------


class TestSingleton:

    def test_get_rbac_engine_returns_same_instance(self):
        assert get_rbac_engine() is get_rbac_engine()


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceSecurityApi:

    def test_get_roles_includes_built_ins(self, client):
        response = client.get("/governance/security/roles")

        assert response.status_code == 200
        names = {role["name"] for role in response.json()}
        assert "Administrator" in names
        assert "Read Only" in names

    def test_post_registers_role(self, client):
        response = client.post(
            "/governance/security/roles",
            params={
                "name": "api-role-1",
                "permissions": '["deployment.read"]',
            },
        )

        assert response.status_code == 200
        assert response.json()["name"] == "api-role-1"
        assert response.json()["permissions"] == ["deployment.read"]

    def test_post_duplicate_returns_409(self, client):
        client.post(
            "/governance/security/roles",
            params={"name": "api-role-2", "permissions": "[]"},
        )

        response = client.post(
            "/governance/security/roles",
            params={"name": "api-role-2", "permissions": "[]"},
        )

        assert response.status_code == 409

    def test_patch_updates_permissions(self, client):
        client.post(
            "/governance/security/roles",
            params={"name": "api-role-3", "permissions": "[]"},
        )

        response = client.patch(
            "/governance/security/roles/api-role-3",
            params={"permissions": '["deployment.deploy"]'},
        )

        assert response.status_code == 200
        assert response.json()["permissions"] == ["deployment.deploy"]

    def test_patch_unknown_returns_404(self, client):
        response = client.patch(
            "/governance/security/roles/does-not-exist",
            params={"permissions": "[]"},
        )

        assert response.status_code == 404

    def test_delete_removes_role(self, client):
        client.post(
            "/governance/security/roles",
            params={"name": "api-role-4", "permissions": "[]"},
        )

        response = client.delete(
            "/governance/security/roles/api-role-4"
        )

        assert response.status_code == 200
        assert response.json() == {"removed": "api-role-4"}

    def test_delete_unknown_returns_404(self, client):
        response = client.delete(
            "/governance/security/roles/does-not-exist"
        )

        assert response.status_code == 404

    def test_post_authorize_denies_unknown_principal(self, client):
        response = client.post(
            "/governance/security/authorize",
            params={
                "principal_id": "api-p-1",
                "permission": "deployment.deploy",
            },
        )

        assert response.status_code == 200
        assert response.json()["allowed"] is False

    def test_rollout_create_denied_returns_403(self, client):
        rbac_engine = get_rbac_engine()
        rbac_engine.assign_role("api-p-2", "Read Only")

        response = client.post(
            "/governance/rollouts",
            params={
                "deployment_id": "api-dep-1",
                "strategy": "CANARY",
                "principal_id": "api-p-2",
            },
        )

        assert response.status_code == 403
