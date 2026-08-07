from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.security.authentication import AuthenticationManager, UnknownUserError
from backend.security.jwt_manager import (
    AccessToken,
    InvalidJWTError,
    InvalidTokenTypeError,
    JWTExpiredError,
    JWTRevokedError,
    JWTTokenManager,
    RefreshToken,
    UnknownRefreshTokenError,
    router as jwt_manager_router,
)

BASE_TIME = datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def authentication_manager() -> AuthenticationManager:
    return AuthenticationManager()


@pytest.fixture
def user_id(authentication_manager: AuthenticationManager) -> str:
    return authentication_manager.register("alice", "hunter2", timestamp=BASE_TIME).user_id


@pytest.fixture
def manager(authentication_manager: AuthenticationManager) -> JWTTokenManager:
    return JWTTokenManager(authentication_manager=authentication_manager, secret_key="test-secret")


def test_issue_returns_access_and_refresh_pair(manager: JWTTokenManager, user_id: str):
    access, refresh = manager.issue(user_id, timestamp=BASE_TIME)

    assert isinstance(access, AccessToken)
    assert isinstance(refresh, RefreshToken)
    assert access.token.count(".") == 2
    assert access.token_type == "Access"
    assert access.expires_at == BASE_TIME + timedelta(minutes=15)


def test_issue_requires_known_user_for_access_type(manager: JWTTokenManager):
    with pytest.raises(UnknownUserError):
        manager.issue("does-not-exist")


def test_issue_service_token_does_not_require_registered_user(manager: JWTTokenManager):
    access, refresh = manager.issue("client-42", "Service", timestamp=BASE_TIME)

    assert access.token_type == "Service"
    assert refresh is None
    assert access.expires_at == BASE_TIME + timedelta(hours=1)


def test_issue_temporary_token_short_lived(manager: JWTTokenManager):
    access, refresh = manager.issue("reset-purpose", "Temporary", timestamp=BASE_TIME)

    assert access.token_type == "Temporary"
    assert refresh is None
    assert access.expires_at == BASE_TIME + timedelta(minutes=5)


def test_issue_rejects_invalid_token_type(manager: JWTTokenManager, user_id: str):
    with pytest.raises(InvalidTokenTypeError):
        manager.issue(user_id, "Refresh")


def test_validate_accepts_freshly_issued_token(manager: JWTTokenManager, user_id: str):
    access, _ = manager.issue(user_id, timestamp=BASE_TIME)

    claims = manager.validate(access.token, timestamp=BASE_TIME)

    assert claims.subject == user_id
    assert claims.token_type == "Access"


def test_validate_rejects_tampered_signature(manager: JWTTokenManager, user_id: str):
    access, _ = manager.issue(user_id, timestamp=BASE_TIME)
    tampered = access.token[:-1] + ("A" if access.token[-1] != "A" else "B")

    with pytest.raises(InvalidJWTError):
        manager.validate(tampered, timestamp=BASE_TIME)


def test_validate_rejects_malformed_token(manager: JWTTokenManager):
    with pytest.raises(InvalidJWTError):
        manager.validate("not-a-jwt")


def test_validate_rejects_expired_token(manager: JWTTokenManager, user_id: str):
    access, _ = manager.issue(user_id, timestamp=BASE_TIME)

    with pytest.raises(JWTExpiredError):
        manager.validate(access.token, timestamp=BASE_TIME + timedelta(hours=1))


def test_refresh_rotates_tokens(manager: JWTTokenManager, user_id: str):
    _, refresh = manager.issue(user_id, timestamp=BASE_TIME)

    new_access, new_refresh = manager.refresh(refresh.token, timestamp=BASE_TIME + timedelta(minutes=5))

    assert new_refresh.token != refresh.token
    assert new_access.subject == user_id


def test_refresh_invalidates_old_refresh_token(manager: JWTTokenManager, user_id: str):
    _, refresh = manager.issue(user_id, timestamp=BASE_TIME)
    manager.refresh(refresh.token, timestamp=BASE_TIME)

    with pytest.raises(UnknownRefreshTokenError):
        manager.refresh(refresh.token, timestamp=BASE_TIME)


def test_refresh_unknown_token_raises(manager: JWTTokenManager):
    with pytest.raises(UnknownRefreshTokenError):
        manager.refresh("does-not-exist")


def test_refresh_expired_token_raises(manager: JWTTokenManager, user_id: str):
    _, refresh = manager.issue(user_id, timestamp=BASE_TIME)

    with pytest.raises(JWTExpiredError):
        manager.refresh(refresh.token, timestamp=BASE_TIME + timedelta(days=8))


def test_revoke_access_token_invalidates_it(manager: JWTTokenManager, user_id: str):
    access, _ = manager.issue(user_id, timestamp=BASE_TIME)

    manager.revoke(access_token=access.token)

    with pytest.raises(JWTRevokedError):
        manager.validate(access.token, timestamp=BASE_TIME)


def test_revoke_refresh_token_invalidates_it(manager: JWTTokenManager, user_id: str):
    _, refresh = manager.issue(user_id, timestamp=BASE_TIME)

    manager.revoke(refresh_token=refresh.token)

    with pytest.raises(UnknownRefreshTokenError):
        manager.refresh(refresh.token, timestamp=BASE_TIME)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(jwt_manager_router)
    return TestClient(app)


def test_api_issue_token(client: TestClient, manager: JWTTokenManager, monkeypatch):
    from backend.security import jwt_manager as jwt_manager_module

    monkeypatch.setattr(jwt_manager_module, "_jwt_token_manager", manager)
    manager._authentication_manager.register("api-alice", "hunter2", timestamp=BASE_TIME)

    response = client.post("/security/tokens", json={"subject": manager._authentication_manager.get_user_id("api-alice")})

    assert response.status_code == 200
    assert response.json()["access_token"]["token"].count(".") == 2
    assert response.json()["refresh_token"] is not None


def test_api_issue_token_unknown_user_returns_404(client: TestClient, manager: JWTTokenManager, monkeypatch):
    from backend.security import jwt_manager as jwt_manager_module

    monkeypatch.setattr(jwt_manager_module, "_jwt_token_manager", manager)

    response = client.post("/security/tokens", json={"subject": "does-not-exist"})

    assert response.status_code == 404


def test_api_issue_service_token(client: TestClient, manager: JWTTokenManager, monkeypatch):
    from backend.security import jwt_manager as jwt_manager_module

    monkeypatch.setattr(jwt_manager_module, "_jwt_token_manager", manager)

    response = client.post(
        "/security/tokens", json={"subject": "client-42", "token_type": "Service"}
    )

    assert response.status_code == 200
    assert response.json()["access_token"]["token_type"] == "Service"
    assert response.json()["refresh_token"] is None


def test_api_refresh_token(client: TestClient, manager: JWTTokenManager, monkeypatch):
    from backend.security import jwt_manager as jwt_manager_module

    monkeypatch.setattr(jwt_manager_module, "_jwt_token_manager", manager)
    manager._authentication_manager.register("api-refresh", "hunter2", timestamp=BASE_TIME)
    user = manager._authentication_manager.get_user_id("api-refresh")
    _, refresh = manager.issue(user, timestamp=BASE_TIME)

    response = client.post("/security/tokens/refresh", json={"refresh_token": refresh.token})

    assert response.status_code == 200
    assert response.json()["refresh_token"]["token"] != refresh.token


def test_api_refresh_unknown_token_returns_404(client: TestClient, manager: JWTTokenManager, monkeypatch):
    from backend.security import jwt_manager as jwt_manager_module

    monkeypatch.setattr(jwt_manager_module, "_jwt_token_manager", manager)

    response = client.post("/security/tokens/refresh", json={"refresh_token": "does-not-exist"})

    assert response.status_code == 404


def test_api_revoke_token(client: TestClient, manager: JWTTokenManager, monkeypatch):
    from backend.security import jwt_manager as jwt_manager_module

    monkeypatch.setattr(jwt_manager_module, "_jwt_token_manager", manager)
    manager._authentication_manager.register("api-revoke", "hunter2", timestamp=BASE_TIME)
    user = manager._authentication_manager.get_user_id("api-revoke")
    access, _ = manager.issue(user, timestamp=BASE_TIME)

    response = client.post("/security/tokens/revoke", json={"access_token": access.token})

    assert response.status_code == 200
    assert response.json()["success"] is True


def test_api_validate_token(client: TestClient, manager: JWTTokenManager, monkeypatch):
    from backend.security import jwt_manager as jwt_manager_module

    monkeypatch.setattr(jwt_manager_module, "_jwt_token_manager", manager)
    manager._authentication_manager.register("api-validate", "hunter2", timestamp=BASE_TIME)
    user = manager._authentication_manager.get_user_id("api-validate")
    access, _ = manager.issue(user)

    response = client.get("/security/tokens/validate", params={"token": access.token})

    assert response.status_code == 200
    assert response.json()["subject"] == user


def test_api_validate_malformed_token_returns_422(client: TestClient, manager: JWTTokenManager, monkeypatch):
    from backend.security import jwt_manager as jwt_manager_module

    monkeypatch.setattr(jwt_manager_module, "_jwt_token_manager", manager)

    response = client.get("/security/tokens/validate", params={"token": "not-a-jwt"})

    assert response.status_code == 422
