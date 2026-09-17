from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.security.authentication import AuthenticationManager
from backend.security.jwt_service import JWTTokenService
from backend.security.session_manager import SessionManager
from backend.security.rbac_engine import (
    InvalidRoleTypeError,
    RBACEngine,
    Role,
    RoleAlreadyExistsError,
    RoleAssignment,
    RoleNotAssignedError,
    UnknownRoleError,
    router as rbac_engine_router,
)

BASE_TIME = datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def authentication_manager() -> AuthenticationManager:
    return AuthenticationManager()


@pytest.fixture
def jwt_service(authentication_manager: AuthenticationManager) -> JWTTokenService:
    return JWTTokenService(authentication_manager=authentication_manager, secret_key="test-secret")


@pytest.fixture
def session_manager(jwt_service: JWTTokenService) -> SessionManager:
    return SessionManager(jwt_service=jwt_service)


@pytest.fixture
def engine(session_manager: SessionManager) -> RBACEngine:
    return RBACEngine(session_manager=session_manager)


def test_seeds_default_role_types(engine: RBACEngine):
    role = engine.get_role("Administrator")

    assert role.role_type == "Administrator"
    assert "manage_roles" in role.permissions


def test_administrator_inherits_developer_and_viewer_permissions(engine: RBACEngine):
    role = engine.get_role("Administrator")

    assert "write_resource" in role.permissions
    assert "read_resource" in role.permissions
    assert "manage_roles" in role.permissions


def test_service_account_does_not_inherit_hierarchy(engine: RBACEngine):
    role = engine.get_role("Service Account")

    assert role.permissions == frozenset({"read_resource", "invoke_service"})


def test_create_role_with_custom_name(engine: RBACEngine):
    role = engine.create_role("release-manager", "Developer", timestamp=BASE_TIME)

    assert isinstance(role, Role)
    assert role.name == "release-manager"
    assert role.permissions == engine.get_role("Developer").permissions


def test_create_role_rejects_duplicate_name(engine: RBACEngine):
    with pytest.raises(RoleAlreadyExistsError):
        engine.create_role("Administrator", "Administrator")


def test_create_role_rejects_unknown_role_type(engine: RBACEngine):
    with pytest.raises(InvalidRoleTypeError):
        engine.create_role("custom", "Superuser")


def test_assign_role_returns_assignment(engine: RBACEngine):
    assignment = engine.assign_role("alice", "Developer", timestamp=BASE_TIME)

    assert isinstance(assignment, RoleAssignment)
    assert assignment.subject == "alice"
    assert assignment.role_name == "Developer"


def test_assign_role_unknown_role_raises(engine: RBACEngine):
    with pytest.raises(UnknownRoleError):
        engine.assign_role("alice", "does-not-exist")


def test_revoke_role_removes_assignment(engine: RBACEngine):
    engine.assign_role("alice", "Developer", timestamp=BASE_TIME)

    engine.revoke_role("alice", "Developer")

    assert engine.roles_for_subject("alice") == []


def test_revoke_role_not_assigned_raises(engine: RBACEngine):
    with pytest.raises(RoleNotAssignedError):
        engine.revoke_role("alice", "Developer")


def test_authorize_grants_permission_from_assigned_role(engine: RBACEngine):
    engine.assign_role("alice", "Developer", timestamp=BASE_TIME)

    assert engine.authorize("alice", "write_resource") is True
    assert engine.authorize("alice", "manage_roles") is False


def test_authorize_denies_without_assignment(engine: RBACEngine):
    assert engine.authorize("alice", "read_resource") is False


def test_authorize_after_revoke_denies(engine: RBACEngine):
    engine.assign_role("alice", "Viewer", timestamp=BASE_TIME)
    engine.revoke_role("alice", "Viewer")

    assert engine.authorize("alice", "read_resource") is False


def test_authorize_merges_permissions_from_session_roles(
    engine: RBACEngine, session_manager: SessionManager, authentication_manager: AuthenticationManager
):
    user_id = authentication_manager.register("bob", "hunter2", timestamp=BASE_TIME).user_id
    session = session_manager.create(user_id, roles=["Developer"], timestamp=BASE_TIME)

    assert (
        engine.authorize(
            user_id, "write_resource", session_id=session.metadata.session_id, timestamp=BASE_TIME
        )
        is True
    )


def test_authorize_ignores_unknown_session(engine: RBACEngine):
    assert engine.authorize("alice", "read_resource", session_id="does-not-exist") is False


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(rbac_engine_router)
    return TestClient(app)


def test_api_create_role(client: TestClient, engine: RBACEngine, monkeypatch):
    from backend.security import rbac_engine as rbac_engine_module

    monkeypatch.setattr(rbac_engine_module, "_rbac_engine", engine)

    response = client.post(
        "/security/roles", json={"name": "api-role", "role_type": "Viewer"}
    )

    assert response.status_code == 200
    assert response.json()["role_type"] == "Viewer"


def test_api_create_role_duplicate_returns_409(client: TestClient, engine: RBACEngine, monkeypatch):
    from backend.security import rbac_engine as rbac_engine_module

    monkeypatch.setattr(rbac_engine_module, "_rbac_engine", engine)

    response = client.post(
        "/security/roles", json={"name": "Administrator", "role_type": "Administrator"}
    )

    assert response.status_code == 409


def test_api_create_role_invalid_type_returns_422(client: TestClient, engine: RBACEngine, monkeypatch):
    from backend.security import rbac_engine as rbac_engine_module

    monkeypatch.setattr(rbac_engine_module, "_rbac_engine", engine)

    response = client.post(
        "/security/roles", json={"name": "custom", "role_type": "Superuser"}
    )

    assert response.status_code == 422


def test_api_assign_role(client: TestClient, engine: RBACEngine, monkeypatch):
    from backend.security import rbac_engine as rbac_engine_module

    monkeypatch.setattr(rbac_engine_module, "_rbac_engine", engine)

    response = client.post(
        "/security/roles/assign", json={"subject": "api-alice", "role_name": "Developer"}
    )

    assert response.status_code == 200
    assert response.json()["role_name"] == "Developer"


def test_api_assign_role_unknown_role_returns_404(client: TestClient, engine: RBACEngine, monkeypatch):
    from backend.security import rbac_engine as rbac_engine_module

    monkeypatch.setattr(rbac_engine_module, "_rbac_engine", engine)

    response = client.post(
        "/security/roles/assign", json={"subject": "api-alice", "role_name": "does-not-exist"}
    )

    assert response.status_code == 404


def test_api_revoke_role(client: TestClient, engine: RBACEngine, monkeypatch):
    from backend.security import rbac_engine as rbac_engine_module

    monkeypatch.setattr(rbac_engine_module, "_rbac_engine", engine)
    engine.assign_role("api-revoke-alice", "Developer", timestamp=BASE_TIME)

    response = client.request(
        "DELETE",
        "/security/roles/assign",
        json={"subject": "api-revoke-alice", "role_name": "Developer"},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True


def test_api_revoke_role_not_assigned_returns_404(client: TestClient, engine: RBACEngine, monkeypatch):
    from backend.security import rbac_engine as rbac_engine_module

    monkeypatch.setattr(rbac_engine_module, "_rbac_engine", engine)

    response = client.request(
        "DELETE",
        "/security/roles/assign",
        json={"subject": "api-alice", "role_name": "Developer"},
    )

    assert response.status_code == 404


def test_api_authorize_allowed(client: TestClient, engine: RBACEngine, monkeypatch):
    from backend.security import rbac_engine as rbac_engine_module

    monkeypatch.setattr(rbac_engine_module, "_rbac_engine", engine)
    engine.assign_role("api-authz-alice", "Administrator", timestamp=BASE_TIME)

    response = client.post(
        "/security/authorize",
        json={"subject": "api-authz-alice", "permission": "manage_roles"},
    )

    assert response.status_code == 200
    assert response.json()["allowed"] is True


def test_api_authorize_denied(client: TestClient, engine: RBACEngine, monkeypatch):
    from backend.security import rbac_engine as rbac_engine_module

    monkeypatch.setattr(rbac_engine_module, "_rbac_engine", engine)

    response = client.post(
        "/security/authorize",
        json={"subject": "nobody", "permission": "manage_roles"},
    )

    assert response.status_code == 200
    assert response.json()["allowed"] is False
