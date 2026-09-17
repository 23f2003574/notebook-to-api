from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.security.authentication import AuthenticationManager, UnknownUserError
from backend.security.authentication import router as authentication_router
from backend.security.jwt_service import (
    InvalidTokenError,
    JWTToken,
    JWTTokenService,
    TokenClaims,
    TokenExpiredError,
    TokenRevokedError,
    UnknownRefreshTokenError,
    router as jwt_router,
)

BASE_TIME = datetime(2026, 7, 27, 18, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def authentication_manager() -> AuthenticationManager:
    return AuthenticationManager()


@pytest.fixture
def user_id(authentication_manager: AuthenticationManager) -> str:
    return authentication_manager.register("alice", "hunter2", timestamp=BASE_TIME).user_id


@pytest.fixture
def service(authentication_manager: AuthenticationManager) -> JWTTokenService:
    return JWTTokenService(
        authentication_manager=authentication_manager,
        secret_key="test-secret",
        access_ttl=timedelta(minutes=15),
        refresh_ttl=timedelta(days=7),
    )


def test_issue_returns_token_pair(service: JWTTokenService, user_id: str):
    token = service.issue(user_id, timestamp=BASE_TIME)

    assert isinstance(token, JWTToken)
    assert token.access_token.count(".") == 2
    assert token.refresh_token is not None
    assert token.expires_at == BASE_TIME + timedelta(minutes=15)


def test_issue_requires_known_user(service: JWTTokenService):
    with pytest.raises(UnknownUserError):
        service.issue("does-not-exist")


def test_validate_accepts_freshly_issued_token(service: JWTTokenService, user_id: str):
    token = service.issue(user_id, timestamp=BASE_TIME)

    claims = service.validate(token.access_token, timestamp=BASE_TIME)

    assert isinstance(claims, TokenClaims)
    assert claims.subject == user_id


def test_validate_rejects_tampered_signature(service: JWTTokenService, user_id: str):
    token = service.issue(user_id, timestamp=BASE_TIME)
    header, payload, signature = token.access_token.split(".")
    tampered = f"{header}.{payload}.{signature[:-4]}aaaa"

    with pytest.raises(InvalidTokenError):
        service.validate(tampered, timestamp=BASE_TIME)


def test_validate_rejects_malformed_token(service: JWTTokenService):
    with pytest.raises(InvalidTokenError):
        service.validate("not-a-jwt")


def test_validate_rejects_token_signed_with_different_secret(
    authentication_manager: AuthenticationManager, user_id: str
):
    service_a = JWTTokenService(authentication_manager=authentication_manager, secret_key="secret-a")
    service_b = JWTTokenService(authentication_manager=authentication_manager, secret_key="secret-b")
    token = service_a.issue(user_id, timestamp=BASE_TIME)

    with pytest.raises(InvalidTokenError):
        service_b.validate(token.access_token, timestamp=BASE_TIME)


def test_validate_rejects_expired_token(service: JWTTokenService, user_id: str):
    token = service.issue(user_id, timestamp=BASE_TIME)

    with pytest.raises(TokenExpiredError):
        service.validate(token.access_token, timestamp=BASE_TIME + timedelta(hours=1))


def test_refresh_issues_new_access_token(service: JWTTokenService, user_id: str):
    token = service.issue(user_id, timestamp=BASE_TIME)

    refreshed = service.refresh(token.refresh_token, timestamp=BASE_TIME + timedelta(minutes=5))

    assert refreshed.access_token != token.access_token
    assert refreshed.refresh_token != token.refresh_token
    claims = service.validate(refreshed.access_token, timestamp=BASE_TIME + timedelta(minutes=5))
    assert claims.subject == user_id


def test_refresh_invalidates_old_refresh_token(service: JWTTokenService, user_id: str):
    token = service.issue(user_id, timestamp=BASE_TIME)
    service.refresh(token.refresh_token, timestamp=BASE_TIME)

    with pytest.raises(UnknownRefreshTokenError):
        service.refresh(token.refresh_token, timestamp=BASE_TIME)


def test_refresh_unknown_token_raises(service: JWTTokenService):
    with pytest.raises(UnknownRefreshTokenError):
        service.refresh("does-not-exist")


def test_refresh_expired_token_raises(service: JWTTokenService, user_id: str):
    token = service.issue(user_id, timestamp=BASE_TIME)

    with pytest.raises(TokenExpiredError):
        service.refresh(token.refresh_token, timestamp=BASE_TIME + timedelta(days=8))


def test_revoke_causes_validate_to_reject_token(service: JWTTokenService, user_id: str):
    token = service.issue(user_id, timestamp=BASE_TIME)

    service.revoke(token.access_token)

    with pytest.raises(TokenRevokedError):
        service.validate(token.access_token, timestamp=BASE_TIME)


def test_revoke_unknown_token_raises_invalid(service: JWTTokenService):
    with pytest.raises(InvalidTokenError):
        service.revoke("not-a-jwt")


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(authentication_router)
    app.include_router(jwt_router)
    return TestClient(app)


def _register_user(client: TestClient, username: str) -> None:
    client.post("/security/register", json={"username": username, "password": "hunter2"})


def test_api_issue_token(client: TestClient):
    _register_user(client, "jwt-issue")

    response = client.post(
        "/security/token", json={"username": "jwt-issue", "password": "hunter2"}
    )

    assert response.status_code == 200
    assert response.json()["access_token"].count(".") == 2


def test_api_issue_token_invalid_credentials_returns_401(client: TestClient):
    _register_user(client, "jwt-bad-creds")

    response = client.post(
        "/security/token", json={"username": "jwt-bad-creds", "password": "wrong-pass"}
    )

    assert response.status_code == 401


def test_api_refresh_token(client: TestClient):
    _register_user(client, "jwt-refresh")
    issue_response = client.post(
        "/security/token", json={"username": "jwt-refresh", "password": "hunter2"}
    )
    refresh_token = issue_response.json()["refresh_token"]

    response = client.post("/security/token/refresh", json={"refresh_token": refresh_token})

    assert response.status_code == 200
    assert response.json()["access_token"] != issue_response.json()["access_token"]


def test_api_refresh_unknown_token_returns_404(client: TestClient):
    response = client.post("/security/token/refresh", json={"refresh_token": "does-not-exist"})

    assert response.status_code == 404


def test_api_revoke_token(client: TestClient):
    _register_user(client, "jwt-revoke")
    issue_response = client.post(
        "/security/token", json={"username": "jwt-revoke", "password": "hunter2"}
    )
    access_token = issue_response.json()["access_token"]

    response = client.post("/security/token/revoke", json={"access_token": access_token})

    assert response.status_code == 200
    assert response.json()["success"] is True
