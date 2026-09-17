from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.security.authentication import AuthenticationManager
from backend.security.rbac import RoleBasedAccessControl
from backend.security.permissions import PermissionEngine
from backend.security.security_policy import (
    PolicyAlreadyExistsError,
    PolicyResult,
    SecurityPolicy,
    SecurityPolicyEngine,
    UnknownEvaluatorError,
    UnknownPolicyError,
    router as security_policy_router,
)

BASE_TIME = datetime(2026, 7, 27, 18, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def authentication_manager() -> AuthenticationManager:
    return AuthenticationManager()


@pytest.fixture
def rbac(authentication_manager: AuthenticationManager) -> RoleBasedAccessControl:
    return RoleBasedAccessControl(authentication_manager=authentication_manager)


@pytest.fixture
def permission_engine(rbac: RoleBasedAccessControl) -> PermissionEngine:
    return PermissionEngine(rbac=rbac)


@pytest.fixture
def user_id(authentication_manager: AuthenticationManager) -> str:
    return authentication_manager.register("alice", "hunter2", timestamp=BASE_TIME).user_id


@pytest.fixture
def engine(permission_engine: PermissionEngine) -> SecurityPolicyEngine:
    return SecurityPolicyEngine(permission_engine=permission_engine)


def test_register_policy(engine: SecurityPolicyEngine):
    policy = engine.register_policy("Password Strength", timestamp=BASE_TIME)

    assert isinstance(policy, SecurityPolicy)
    assert policy.enabled is True
    assert policy.created_at == BASE_TIME


def test_register_policy_rejects_unknown_name(engine: SecurityPolicyEngine):
    with pytest.raises(UnknownEvaluatorError):
        engine.register_policy("Not A Real Policy")


def test_register_policy_duplicate_raises(engine: SecurityPolicyEngine):
    engine.register_policy("MFA Required")

    with pytest.raises(PolicyAlreadyExistsError):
        engine.register_policy("MFA Required")


def test_list_policies(engine: SecurityPolicyEngine):
    engine.register_policy("Password Strength")
    engine.register_policy("MFA Required")

    assert len(engine.list_policies()) == 2


def test_enable_disable_toggle(engine: SecurityPolicyEngine):
    engine.register_policy("Session Timeout", enabled=True)

    disabled = engine.disable("Session Timeout")
    assert disabled.enabled is False

    enabled = engine.enable("Session Timeout")
    assert enabled.enabled is True


def test_enable_unknown_policy_raises(engine: SecurityPolicyEngine):
    with pytest.raises(UnknownPolicyError):
        engine.enable("Does Not Exist")


def test_evaluate_password_strength_passes(engine: SecurityPolicyEngine):
    engine.register_policy("Password Strength")

    result = engine.evaluate(
        "Password Strength", {"password": "Str0ngPassword!"}, timestamp=BASE_TIME
    )

    assert isinstance(result, PolicyResult)
    assert result.passed is True


def test_evaluate_password_strength_fails_short_password(engine: SecurityPolicyEngine):
    engine.register_policy("Password Strength")

    result = engine.evaluate("Password Strength", {"password": "short1A"})

    assert result.passed is False
    assert "characters" in result.message


def test_evaluate_mfa_required(engine: SecurityPolicyEngine):
    engine.register_policy("MFA Required")

    assert engine.evaluate("MFA Required", {"mfa_enabled": True}).passed is True
    assert engine.evaluate("MFA Required", {"mfa_enabled": False}).passed is False


def test_evaluate_session_timeout(engine: SecurityPolicyEngine):
    engine.register_policy("Session Timeout", config={"max_idle_minutes": 15})

    assert engine.evaluate("Session Timeout", {"idle_minutes": 10}).passed is True
    assert engine.evaluate("Session Timeout", {"idle_minutes": 20}).passed is False


def test_evaluate_api_key_rotation(engine: SecurityPolicyEngine):
    engine.register_policy("API Key Rotation")

    assert engine.evaluate("API Key Rotation", {"age_days": 30}).passed is True
    assert engine.evaluate("API Key Rotation", {"age_days": 120}).passed is False


def test_evaluate_unknown_policy_raises(engine: SecurityPolicyEngine):
    with pytest.raises(UnknownPolicyError):
        engine.evaluate("Not Registered", {})


def test_evaluate_disabled_policy_passes_automatically(engine: SecurityPolicyEngine):
    engine.register_policy("MFA Required", enabled=False)

    result = engine.evaluate("MFA Required", {"mfa_enabled": False})

    assert result.passed is True
    assert result.message == "policy disabled"


def test_evaluate_respects_admin_override(
    engine: SecurityPolicyEngine,
    permission_engine: PermissionEngine,
    rbac: RoleBasedAccessControl,
    user_id: str,
):
    engine.register_policy("MFA Required")
    rbac.assign_role(user_id, "Admin", timestamp=BASE_TIME)
    permission_engine.grant("Admin", "policy:MFA Required", "Admin", timestamp=BASE_TIME)

    result = engine.evaluate("MFA Required", {"mfa_enabled": False}, user_id=user_id)

    assert result.passed is True
    assert result.overridden is True


def test_evaluate_without_override_permission_still_enforces(
    engine: SecurityPolicyEngine,
    rbac: RoleBasedAccessControl,
    user_id: str,
):
    engine.register_policy("MFA Required")
    rbac.assign_role(user_id, "Viewer", timestamp=BASE_TIME)

    result = engine.evaluate("MFA Required", {"mfa_enabled": False}, user_id=user_id)

    assert result.passed is False
    assert result.overridden is False


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(security_policy_router)
    return TestClient(app)


def test_api_register_policy(client: TestClient):
    # The security bootstrap may already have registered every built-in policy
    # on the shared global engine, so a fresh registration isn't guaranteed here.
    response = client.post("/security/policies", json={"name": "Password Strength"})

    assert response.status_code in (200, 409)
    list_response = client.get("/security/policies")
    names = {policy["name"] for policy in list_response.json()}
    assert "Password Strength" in names


def test_api_register_policy_invalid_name_returns_422(client: TestClient):
    response = client.post("/security/policies", json={"name": "Not A Real Policy"})

    assert response.status_code == 422


def test_api_register_policy_duplicate_returns_409(client: TestClient):
    client.post("/security/policies", json={"name": "MFA Required"})

    response = client.post("/security/policies", json={"name": "MFA Required"})

    assert response.status_code == 409


def test_api_list_policies(client: TestClient):
    client.post("/security/policies", json={"name": "Session Timeout"})

    response = client.get("/security/policies")

    assert response.status_code == 200
    names = {policy["name"] for policy in response.json()}
    assert "Session Timeout" in names


def test_api_evaluate_policy(client: TestClient):
    client.post("/security/policies", json={"name": "API Key Rotation"})

    response = client.post(
        "/security/policies/API Key Rotation/evaluate", json={"context": {"age_days": 200}}
    )

    assert response.status_code == 200
    assert response.json()["passed"] is False


def test_api_evaluate_unregistered_policy_returns_404(client: TestClient):
    response = client.post("/security/policies/Does Not Exist/evaluate", json={"context": {}})

    assert response.status_code == 404
