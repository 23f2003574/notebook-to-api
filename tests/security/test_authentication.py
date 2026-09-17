from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.security.audit_logger import SecurityAuditLogger
from backend.security.authentication import (
    AuthenticationManager,
    AuthenticationResult,
    InvalidCredentialsError,
    UnknownSessionError,
    UserAlreadyExistsError,
    UserCredential,
    router as authentication_router,
)

BASE_TIME = datetime(2026, 7, 27, 18, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def audit_logger() -> SecurityAuditLogger:
    return SecurityAuditLogger()


@pytest.fixture
def manager(audit_logger: SecurityAuditLogger) -> AuthenticationManager:
    return AuthenticationManager(audit_logger=audit_logger)


def test_register_creates_credential(manager: AuthenticationManager):
    credential = manager.register("alice", "hunter2", timestamp=BASE_TIME)

    assert isinstance(credential, UserCredential)
    assert credential.username == "alice"
    assert credential.password_hash != "hunter2"
    assert credential.created_at == BASE_TIME


def test_register_duplicate_username_raises(manager: AuthenticationManager):
    manager.register("alice", "hunter2")

    with pytest.raises(UserAlreadyExistsError):
        manager.register("alice", "different-pass")


def test_register_requires_username_and_password(manager: AuthenticationManager):
    with pytest.raises(InvalidCredentialsError):
        manager.register("", "hunter2")
    with pytest.raises(InvalidCredentialsError):
        manager.register("alice", "")


def test_authenticate_accepts_correct_credentials(manager: AuthenticationManager):
    manager.register("alice", "hunter2")

    assert manager.authenticate("alice", "hunter2") is True


def test_authenticate_rejects_wrong_password(manager: AuthenticationManager):
    manager.register("alice", "hunter2")

    assert manager.authenticate("alice", "wrong-pass") is False


def test_authenticate_rejects_unknown_user(manager: AuthenticationManager):
    assert manager.authenticate("nobody", "hunter2") is False


def test_login_succeeds_with_valid_credentials(manager: AuthenticationManager):
    manager.register("alice", "hunter2")

    result = manager.login("alice", "hunter2")

    assert isinstance(result, AuthenticationResult)
    assert result.success is True
    assert result.session_token is not None
    assert manager.is_authenticated(result.session_token) is True


def test_login_fails_with_invalid_credentials(manager: AuthenticationManager):
    manager.register("alice", "hunter2")

    result = manager.login("alice", "wrong-pass")

    assert result.success is False
    assert result.session_token is None


def test_logout_ends_session(manager: AuthenticationManager):
    manager.register("alice", "hunter2")
    result = manager.login("alice", "hunter2")

    manager.logout(result.session_token)

    assert manager.is_authenticated(result.session_token) is False


def test_logout_unknown_session_raises(manager: AuthenticationManager):
    with pytest.raises(UnknownSessionError):
        manager.logout("does-not-exist")


def test_register_records_audit_event(manager: AuthenticationManager, audit_logger: SecurityAuditLogger):
    credential = manager.register("alice", "hunter2", timestamp=BASE_TIME)

    events = audit_logger.query()
    assert len(events) == 1
    assert events[0].event_type == "Authentication"
    assert events[0].actor == credential.user_id
    assert events[0].action == "register"


def test_login_success_records_info_severity_event(
    manager: AuthenticationManager, audit_logger: SecurityAuditLogger
):
    manager.register("alice", "hunter2", timestamp=BASE_TIME)

    manager.login("alice", "hunter2", timestamp=BASE_TIME)

    events = audit_logger.query()
    login_events = [event for event in events if event.action == "login"]
    assert len(login_events) == 1
    assert login_events[0].outcome == "success"
    assert login_events[0].severity == "Info"


def test_login_failure_records_warning_severity_event(
    manager: AuthenticationManager, audit_logger: SecurityAuditLogger
):
    manager.register("alice", "hunter2", timestamp=BASE_TIME)

    manager.login("alice", "wrong-pass", timestamp=BASE_TIME)

    events = audit_logger.query()
    login_events = [event for event in events if event.action == "login"]
    assert len(login_events) == 1
    assert login_events[0].outcome == "failure"
    assert login_events[0].severity == "Warning"


def test_logout_records_audit_event(manager: AuthenticationManager, audit_logger: SecurityAuditLogger):
    manager.register("alice", "hunter2", timestamp=BASE_TIME)
    result = manager.login("alice", "hunter2", timestamp=BASE_TIME)

    manager.logout(result.session_token, timestamp=BASE_TIME)

    events = audit_logger.query()
    logout_events = [event for event in events if event.action == "logout"]
    assert len(logout_events) == 1
    assert logout_events[0].actor == result.user_id


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(authentication_router)
    return TestClient(app)


def test_api_register(client: TestClient):
    response = client.post(
        "/security/register", json={"username": "bob", "password": "s3cret"}
    )

    assert response.status_code == 200
    assert response.json()["username"] == "bob"


def test_api_register_duplicate_returns_409(client: TestClient):
    client.post("/security/register", json={"username": "bob", "password": "s3cret"})

    response = client.post(
        "/security/register", json={"username": "bob", "password": "other"}
    )

    assert response.status_code == 409


def test_api_login_and_logout(client: TestClient):
    client.post("/security/register", json={"username": "bob", "password": "s3cret"})

    login_response = client.post(
        "/security/login", json={"username": "bob", "password": "s3cret"}
    )
    session_token = login_response.json()["session_token"]
    logout_response = client.post(
        "/security/logout", json={"session_token": session_token}
    )

    assert login_response.status_code == 200
    assert logout_response.status_code == 200
    assert logout_response.json()["success"] is True


def test_api_login_invalid_credentials_returns_401(client: TestClient):
    client.post("/security/register", json={"username": "bob", "password": "s3cret"})

    response = client.post(
        "/security/login", json={"username": "bob", "password": "wrong-pass"}
    )

    assert response.status_code == 401


def test_api_logout_unknown_session_returns_404(client: TestClient):
    response = client.post("/security/logout", json={"session_token": "does-not-exist"})

    assert response.status_code == 404
