from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.security.authentication import AuthenticationManager, UnknownUserError
from backend.security.authentication import router as authentication_router
from backend.security.rbac import (
    RoleAlreadyExistsError,
    RoleAssignment,
    RoleBasedAccessControl,
    RoleNotAssignedError,
    UnknownRoleError,
    router as rbac_router,
)

BASE_TIME = datetime(2026, 7, 27, 18, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def authentication_manager() -> AuthenticationManager:
    return AuthenticationManager()


@pytest.fixture
def user_id(authentication_manager: AuthenticationManager) -> str:
    return authentication_manager.register("alice", "hunter2", timestamp=BASE_TIME).user_id


@pytest.fixture
def rbac(authentication_manager: AuthenticationManager) -> RoleBasedAccessControl:
    return RoleBasedAccessControl(authentication_manager=authentication_manager)


def test_default_roles_are_seeded(rbac: RoleBasedAccessControl):
    assert rbac.get_role("Admin").name == "Admin"
    assert rbac.get_role("Developer").inherits == ("Viewer",)
    assert rbac.get_role("Viewer").inherits == ()
    assert rbac.get_role("Service").inherits == ()


def test_create_role(rbac: RoleBasedAccessControl):
    role = rbac.create_role("Auditor", timestamp=BASE_TIME)

    assert role.name == "Auditor"
    assert role.created_at == BASE_TIME


def test_create_role_duplicate_raises(rbac: RoleBasedAccessControl):
    with pytest.raises(RoleAlreadyExistsError):
        rbac.create_role("Admin")


def test_create_role_with_unknown_parent_raises(rbac: RoleBasedAccessControl):
    with pytest.raises(UnknownRoleError):
        rbac.create_role("Auditor", inherits=("DoesNotExist",))


def test_assign_role(rbac: RoleBasedAccessControl, user_id: str):
    assignment = rbac.assign_role(user_id, "Developer", timestamp=BASE_TIME)

    assert isinstance(assignment, RoleAssignment)
    assert assignment.user_id == user_id
    assert assignment.role == "Developer"


def test_assign_role_requires_known_user(rbac: RoleBasedAccessControl):
    with pytest.raises(UnknownUserError):
        rbac.assign_role("does-not-exist", "Developer")


def test_assign_role_requires_known_role(rbac: RoleBasedAccessControl, user_id: str):
    with pytest.raises(UnknownRoleError):
        rbac.assign_role(user_id, "DoesNotExist")


def test_roles_for_user_supports_multiple_roles(rbac: RoleBasedAccessControl, user_id: str):
    rbac.assign_role(user_id, "Service", timestamp=BASE_TIME)
    rbac.assign_role(user_id, "Viewer", timestamp=BASE_TIME)

    roles = rbac.roles_for_user(user_id, include_inherited=False)

    assert roles == ["Service", "Viewer"]


def test_roles_for_user_resolves_inheritance(rbac: RoleBasedAccessControl, user_id: str):
    rbac.assign_role(user_id, "Admin", timestamp=BASE_TIME)

    roles = rbac.roles_for_user(user_id)

    assert roles == ["Admin", "Developer", "Viewer"]


def test_roles_for_user_empty_when_unassigned(rbac: RoleBasedAccessControl):
    assert rbac.roles_for_user("does-not-exist") == []


def test_revoke_role(rbac: RoleBasedAccessControl, user_id: str):
    rbac.assign_role(user_id, "Developer", timestamp=BASE_TIME)

    rbac.revoke_role(user_id, "Developer")

    assert rbac.roles_for_user(user_id, include_inherited=False) == []


def test_revoke_role_not_assigned_raises(rbac: RoleBasedAccessControl, user_id: str):
    with pytest.raises(RoleNotAssignedError):
        rbac.revoke_role(user_id, "Developer")


def test_version_increments_on_assign_and_revoke(rbac: RoleBasedAccessControl, user_id: str):
    initial = rbac.version()

    rbac.assign_role(user_id, "Developer", timestamp=BASE_TIME)
    assert rbac.version() == initial + 1

    rbac.revoke_role(user_id, "Developer")
    assert rbac.version() == initial + 2


def test_assignments_for_user(rbac: RoleBasedAccessControl, user_id: str):
    rbac.assign_role(user_id, "Viewer", timestamp=BASE_TIME)

    assignments = rbac.assignments_for_user(user_id)

    assert len(assignments) == 1
    assert assignments[0].role == "Viewer"


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(authentication_router)
    app.include_router(rbac_router)
    return TestClient(app)


def _register_user(client: TestClient, username: str) -> str:
    response = client.post("/security/register", json={"username": username, "password": "hunter2"})
    return response.json()["user_id"]


def test_api_create_role(client: TestClient):
    response = client.post("/security/roles", json={"name": "Auditor-api"})

    assert response.status_code == 200
    assert response.json()["name"] == "Auditor-api"


def test_api_create_role_duplicate_returns_409(client: TestClient):
    response = client.post("/security/roles", json={"name": "Admin"})

    assert response.status_code == 409


def test_api_assign_role(client: TestClient):
    user_id = _register_user(client, "rbac-assign")

    response = client.post(f"/security/users/{user_id}/roles", json={"role": "Developer"})

    assert response.status_code == 200
    assert response.json()["role"] == "Developer"


def test_api_assign_role_unknown_user_returns_404(client: TestClient):
    response = client.post("/security/users/does-not-exist/roles", json={"role": "Developer"})

    assert response.status_code == 404


def test_api_assign_role_unknown_role_returns_404(client: TestClient):
    user_id = _register_user(client, "rbac-unknown-role")

    response = client.post(f"/security/users/{user_id}/roles", json={"role": "DoesNotExist"})

    assert response.status_code == 404


def test_api_list_roles_resolves_inheritance(client: TestClient):
    user_id = _register_user(client, "rbac-list")
    client.post(f"/security/users/{user_id}/roles", json={"role": "Admin"})

    response = client.get(f"/security/users/{user_id}/roles")

    assert response.status_code == 200
    assert response.json() == ["Admin", "Developer", "Viewer"]


def test_api_revoke_role(client: TestClient):
    user_id = _register_user(client, "rbac-revoke")
    client.post(f"/security/users/{user_id}/roles", json={"role": "Viewer"})

    response = client.delete(f"/security/users/{user_id}/roles/Viewer")

    assert response.status_code == 200
    assert response.json()["success"] is True


def test_api_revoke_role_not_assigned_returns_404(client: TestClient):
    user_id = _register_user(client, "rbac-revoke-missing")

    response = client.delete(f"/security/users/{user_id}/roles/Viewer")

    assert response.status_code == 404
