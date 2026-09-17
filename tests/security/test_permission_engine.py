from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.security.authentication import AuthenticationManager
from backend.security.rbac import RoleBasedAccessControl
from backend.security.permission_engine import (
    InvalidPermissionTypeError,
    Permission,
    PermissionAssignment,
    PermissionEngine,
    PermissionNotGrantedError,
    router as permission_engine_router,
)

BASE_TIME = datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def authentication_manager() -> AuthenticationManager:
    return AuthenticationManager()


@pytest.fixture
def rbac(authentication_manager: AuthenticationManager) -> RoleBasedAccessControl:
    return RoleBasedAccessControl(authentication_manager=authentication_manager)


@pytest.fixture
def engine(rbac: RoleBasedAccessControl) -> PermissionEngine:
    return PermissionEngine(rbac=rbac)


def test_grant_returns_assignment(engine: PermissionEngine):
    assignment = engine.grant("alice", "orders", "Read", timestamp=BASE_TIME)

    assert isinstance(assignment, PermissionAssignment)
    assert assignment.identity == "alice"
    assert assignment.permission_id == "orders:Read"


def test_grant_rejects_unknown_action(engine: PermissionEngine):
    with pytest.raises(InvalidPermissionTypeError):
        engine.grant("alice", "orders", "Delete")


def test_check_allows_matching_grant(engine: PermissionEngine):
    engine.grant("alice", "orders", "Write", timestamp=BASE_TIME)

    assert engine.check("alice", "orders", "Write") is True
    assert engine.check("alice", "orders", "Read") is True
    assert engine.check("alice", "orders", "Admin") is False


def test_check_denies_without_grant(engine: PermissionEngine):
    assert engine.check("alice", "orders", "Read") is False


def test_check_supports_wildcard_resource(engine: PermissionEngine):
    engine.grant("alice", "*", "Read", timestamp=BASE_TIME)

    assert engine.check("alice", "anything", "Read") is True


def test_check_inherits_permission_to_child_resources(engine: PermissionEngine):
    engine.grant("alice", "orders", "Write", timestamp=BASE_TIME)

    assert engine.check("alice", "orders/123", "Write") is True
    assert engine.check("alice", "orders/123/items", "Read") is True


def test_check_does_not_leak_to_sibling_resources(engine: PermissionEngine):
    engine.grant("alice", "orders", "Write", timestamp=BASE_TIME)

    assert engine.check("alice", "invoices", "Read") is False


def test_revoke_removes_grant(engine: PermissionEngine):
    engine.grant("alice", "orders", "Read", timestamp=BASE_TIME)

    engine.revoke("alice", "orders", "Read")

    assert engine.check("alice", "orders", "Read") is False


def test_revoke_not_granted_raises(engine: PermissionEngine):
    with pytest.raises(PermissionNotGrantedError):
        engine.revoke("alice", "orders", "Read")


def test_revoke_all_clears_every_direct_grant(engine: PermissionEngine):
    engine.grant("alice", "orders", "Read", timestamp=BASE_TIME)
    engine.grant("alice", "invoices", "Write", timestamp=BASE_TIME)

    engine.revoke_all("alice")

    assert engine.list_permissions("alice", include_inherited=False) == []
    assert engine.check("alice", "orders", "Read") is False


def test_revoke_all_unknown_identity_is_a_noop(engine: PermissionEngine):
    engine.revoke_all("does-not-exist")


def test_list_permissions_returns_direct_grants(engine: PermissionEngine):
    engine.grant("alice", "orders", "Read", timestamp=BASE_TIME)
    engine.grant("alice", "invoices", "Write", timestamp=BASE_TIME)

    permissions = engine.list_permissions("alice", include_inherited=False)

    assert {p.permission_id for p in permissions} == {"orders:Read", "invoices:Write"}
    assert all(isinstance(p, Permission) for p in permissions)


def test_list_permissions_includes_role_derived_admin_override(
    engine: PermissionEngine, rbac: RoleBasedAccessControl, authentication_manager: AuthenticationManager
):
    user_id = authentication_manager.register("bob", "hunter2", timestamp=BASE_TIME).user_id
    rbac.assign_role(user_id, "Admin", timestamp=BASE_TIME)

    assert engine.check(user_id, "anything", "Admin") is True

    permissions = engine.list_permissions(user_id, include_inherited=True)
    assert any(p.resource == "*" and p.action == "Admin" for p in permissions)


def test_admin_role_revocation_invalidates_cache(
    engine: PermissionEngine, rbac: RoleBasedAccessControl, authentication_manager: AuthenticationManager
):
    user_id = authentication_manager.register("carol", "hunter2", timestamp=BASE_TIME).user_id
    rbac.assign_role(user_id, "Admin", timestamp=BASE_TIME)
    assert engine.check(user_id, "anything", "Admin") is True

    rbac.revoke_role(user_id, "Admin")

    assert engine.check(user_id, "anything", "Admin") is False


def test_cache_reused_across_calls_when_version_unchanged(
    engine: PermissionEngine, rbac: RoleBasedAccessControl
):
    engine.grant("alice", "orders", "Read", timestamp=BASE_TIME)
    version_before = rbac.version()

    engine.check("alice", "orders", "Read")
    engine.check("alice", "orders", "Read")

    assert rbac.version() == version_before
    assert len(engine._cache) == 1


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(permission_engine_router)
    return TestClient(app)


def test_api_grant_permission(client: TestClient, engine: PermissionEngine, monkeypatch):
    from backend.security import permission_engine as permission_engine_module

    monkeypatch.setattr(permission_engine_module, "_permission_engine", engine)

    response = client.post(
        "/security/permissions",
        json={"identity": "api-alice", "resource": "orders", "action": "Read"},
    )

    assert response.status_code == 200
    assert response.json()["permission_id"] == "orders:Read"


def test_api_grant_invalid_action_returns_422(client: TestClient, engine: PermissionEngine, monkeypatch):
    from backend.security import permission_engine as permission_engine_module

    monkeypatch.setattr(permission_engine_module, "_permission_engine", engine)

    response = client.post(
        "/security/permissions",
        json={"identity": "api-alice", "resource": "orders", "action": "Delete"},
    )

    assert response.status_code == 422


def test_api_check_permission(client: TestClient, engine: PermissionEngine, monkeypatch):
    from backend.security import permission_engine as permission_engine_module

    monkeypatch.setattr(permission_engine_module, "_permission_engine", engine)
    engine.grant("api-check-alice", "orders", "Read", timestamp=BASE_TIME)

    response = client.post(
        "/security/permissions/check",
        json={"identity": "api-check-alice", "resource": "orders", "action": "Read"},
    )

    assert response.status_code == 200
    assert response.json()["allowed"] is True


def test_api_revoke_permission(client: TestClient, engine: PermissionEngine, monkeypatch):
    from backend.security import permission_engine as permission_engine_module

    monkeypatch.setattr(permission_engine_module, "_permission_engine", engine)
    engine.grant("api-revoke-alice", "orders", "Read", timestamp=BASE_TIME)

    response = client.request(
        "DELETE",
        "/security/permissions",
        json={"identity": "api-revoke-alice", "resource": "orders", "action": "Read"},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True


def test_api_revoke_permission_not_granted_returns_404(
    client: TestClient, engine: PermissionEngine, monkeypatch
):
    from backend.security import permission_engine as permission_engine_module

    monkeypatch.setattr(permission_engine_module, "_permission_engine", engine)

    response = client.request(
        "DELETE",
        "/security/permissions",
        json={"identity": "api-alice", "resource": "orders", "action": "Read"},
    )

    assert response.status_code == 404


def test_api_list_permissions_for_identity(client: TestClient, engine: PermissionEngine, monkeypatch):
    from backend.security import permission_engine as permission_engine_module

    monkeypatch.setattr(permission_engine_module, "_permission_engine", engine)
    engine.grant("api-list-alice", "orders", "Read", timestamp=BASE_TIME)

    response = client.get("/security/permissions/api-list-alice")

    assert response.status_code == 200
    assert len(response.json()) == 1
