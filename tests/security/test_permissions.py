from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.security.authentication import AuthenticationManager
from backend.security.authentication import router as authentication_router
from backend.security.rbac import RoleBasedAccessControl, UnknownRoleError
from backend.security.rbac import router as rbac_router
from backend.security.permissions import (
    InvalidPermissionTypeError,
    Permission,
    PermissionAssignment,
    PermissionEngine,
    PermissionNotGrantedError,
    router as permissions_router,
)

BASE_TIME = datetime(2026, 7, 27, 18, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def authentication_manager() -> AuthenticationManager:
    return AuthenticationManager()


@pytest.fixture
def rbac(authentication_manager: AuthenticationManager) -> RoleBasedAccessControl:
    return RoleBasedAccessControl(authentication_manager=authentication_manager)


@pytest.fixture
def user_id(authentication_manager: AuthenticationManager) -> str:
    return authentication_manager.register("alice", "hunter2", timestamp=BASE_TIME).user_id


@pytest.fixture
def engine(rbac: RoleBasedAccessControl) -> PermissionEngine:
    return PermissionEngine(rbac=rbac)


def test_define_creates_permission(engine: PermissionEngine):
    permission = engine.define("notebooks", "Read", timestamp=BASE_TIME)

    assert isinstance(permission, Permission)
    assert permission.resource == "notebooks"
    assert permission.action == "Read"
    assert permission.permission_id == "notebooks:Read"


def test_define_rejects_unknown_action(engine: PermissionEngine):
    with pytest.raises(InvalidPermissionTypeError):
        engine.define("notebooks", "Delete")


def test_define_is_idempotent(engine: PermissionEngine):
    first = engine.define("notebooks", "Read", timestamp=BASE_TIME)
    second = engine.define("notebooks", "Read")

    assert first.permission_id == second.permission_id


def test_grant_creates_assignment(engine: PermissionEngine):
    assignment = engine.grant("Viewer", "notebooks", "Read", timestamp=BASE_TIME)

    assert isinstance(assignment, PermissionAssignment)
    assert assignment.role == "Viewer"
    assert assignment.permission_id == "notebooks:Read"


def test_grant_requires_known_role(engine: PermissionEngine):
    with pytest.raises(UnknownRoleError):
        engine.grant("DoesNotExist", "notebooks", "Read")


def test_grant_rejects_unknown_action(engine: PermissionEngine):
    with pytest.raises(InvalidPermissionTypeError):
        engine.grant("Viewer", "notebooks", "Delete")


def test_revoke_removes_assignment(engine: PermissionEngine):
    engine.grant("Viewer", "notebooks", "Read", timestamp=BASE_TIME)

    engine.revoke("Viewer", "notebooks:Read")

    assert engine.permissions_for_role("Viewer", include_inherited=False) == []


def test_revoke_not_granted_raises(engine: PermissionEngine):
    with pytest.raises(PermissionNotGrantedError):
        engine.revoke("Viewer", "notebooks:Read")


def test_permissions_for_role_direct(engine: PermissionEngine):
    engine.grant("Viewer", "notebooks", "Read", timestamp=BASE_TIME)

    permissions = engine.permissions_for_role("Viewer", include_inherited=False)

    assert [p.permission_id for p in permissions] == ["notebooks:Read"]


def test_permissions_for_role_resolves_inheritance(engine: PermissionEngine):
    engine.grant("Viewer", "notebooks", "Read", timestamp=BASE_TIME)
    engine.grant("Developer", "notebooks", "Write", timestamp=BASE_TIME)

    permissions = engine.permissions_for_role("Developer")

    ids = {p.permission_id for p in permissions}
    assert ids == {"notebooks:Read", "notebooks:Write"}


def test_permissions_for_role_unknown_role_raises(engine: PermissionEngine):
    with pytest.raises(UnknownRoleError):
        engine.permissions_for_role("DoesNotExist")


def test_check_grants_access_for_matching_permission(
    engine: PermissionEngine, rbac: RoleBasedAccessControl, user_id: str
):
    rbac.assign_role(user_id, "Viewer", timestamp=BASE_TIME)
    engine.grant("Viewer", "notebooks", "Read", timestamp=BASE_TIME)

    assert engine.check(user_id, "notebooks", "Read") is True


def test_check_denies_without_permission(
    engine: PermissionEngine, rbac: RoleBasedAccessControl, user_id: str
):
    rbac.assign_role(user_id, "Viewer", timestamp=BASE_TIME)

    assert engine.check(user_id, "notebooks", "Write") is False


def test_check_higher_action_implies_lower(
    engine: PermissionEngine, rbac: RoleBasedAccessControl, user_id: str
):
    rbac.assign_role(user_id, "Viewer", timestamp=BASE_TIME)
    engine.grant("Viewer", "notebooks", "Manage", timestamp=BASE_TIME)

    assert engine.check(user_id, "notebooks", "Read") is True
    assert engine.check(user_id, "notebooks", "Write") is True


def test_check_respects_inherited_roles(
    engine: PermissionEngine, rbac: RoleBasedAccessControl, user_id: str
):
    rbac.assign_role(user_id, "Admin", timestamp=BASE_TIME)
    engine.grant("Viewer", "notebooks", "Read", timestamp=BASE_TIME)

    assert engine.check(user_id, "notebooks", "Read") is True


def test_check_wildcard_resource_grants_any_resource(
    engine: PermissionEngine, rbac: RoleBasedAccessControl, user_id: str
):
    rbac.assign_role(user_id, "Admin", timestamp=BASE_TIME)
    engine.grant("Admin", "*", "Admin", timestamp=BASE_TIME)

    assert engine.check(user_id, "notebooks", "Manage") is True
    assert engine.check(user_id, "billing", "Read") is True


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(authentication_router)
    app.include_router(rbac_router)
    app.include_router(permissions_router)
    return TestClient(app)


def test_api_define_permission(client: TestClient):
    response = client.post("/security/permissions", json={"resource": "notebooks-api", "action": "Read"})

    assert response.status_code == 200
    assert response.json()["permission_id"] == "notebooks-api:Read"


def test_api_define_permission_invalid_action_returns_422(client: TestClient):
    response = client.post("/security/permissions", json={"resource": "notebooks-api", "action": "Delete"})

    assert response.status_code == 422


def test_api_grant_permission(client: TestClient):
    response = client.post(
        "/security/roles/Viewer/permissions", json={"resource": "notebooks-grant", "action": "Read"}
    )

    assert response.status_code == 200
    assert response.json()["permission_id"] == "notebooks-grant:Read"


def test_api_grant_permission_unknown_role_returns_404(client: TestClient):
    response = client.post(
        "/security/roles/DoesNotExist/permissions", json={"resource": "notebooks-grant", "action": "Read"}
    )

    assert response.status_code == 404


def test_api_list_permissions(client: TestClient):
    client.post(
        "/security/roles/Viewer/permissions", json={"resource": "notebooks-list", "action": "Read"}
    )

    response = client.get("/security/roles/Viewer/permissions", params={"include_inherited": "false"})

    assert response.status_code == 200
    ids = {p["permission_id"] for p in response.json()}
    assert "notebooks-list:Read" in ids


def test_api_revoke_permission(client: TestClient):
    client.post(
        "/security/roles/Viewer/permissions", json={"resource": "notebooks-revoke", "action": "Read"}
    )

    response = client.delete("/security/roles/Viewer/permissions/notebooks-revoke:Read")

    assert response.status_code == 200
    assert response.json()["success"] is True


def test_api_revoke_permission_not_granted_returns_404(client: TestClient):
    response = client.delete("/security/roles/Viewer/permissions/does-not-exist:Read")

    assert response.status_code == 404
