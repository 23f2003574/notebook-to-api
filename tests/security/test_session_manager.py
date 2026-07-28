from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.security.authentication import AuthenticationManager, UnknownUserError
from backend.security.authentication import router as authentication_router
from backend.security.jwt_service import JWTTokenService
from backend.security.session_manager import (
    Session,
    SessionExpiredError,
    SessionManager,
    SessionMetadata,
    SessionTerminatedError,
    UnknownSessionError,
    router as session_router,
)

BASE_TIME = datetime(2026, 7, 27, 18, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def authentication_manager() -> AuthenticationManager:
    return AuthenticationManager()


@pytest.fixture
def user_id(authentication_manager: AuthenticationManager) -> str:
    return authentication_manager.register("alice", "hunter2", timestamp=BASE_TIME).user_id


@pytest.fixture
def jwt_service(authentication_manager: AuthenticationManager) -> JWTTokenService:
    return JWTTokenService(authentication_manager=authentication_manager, secret_key="test-secret")


@pytest.fixture
def manager(jwt_service: JWTTokenService) -> SessionManager:
    return SessionManager(jwt_service=jwt_service, idle_timeout=timedelta(minutes=30))


def test_create_returns_session_with_tokens(manager: SessionManager, user_id: str):
    session = manager.create(user_id, timestamp=BASE_TIME)

    assert isinstance(session, Session)
    assert session.metadata.user_id == user_id
    assert session.access_token.count(".") == 2
    assert session.metadata.terminated is False


def test_create_requires_known_user(manager: SessionManager):
    with pytest.raises(UnknownUserError):
        manager.create("does-not-exist")


def test_validate_touches_last_active(manager: SessionManager, user_id: str):
    session = manager.create(user_id, timestamp=BASE_TIME)

    metadata = manager.validate(session.metadata.session_id, timestamp=BASE_TIME + timedelta(minutes=5))

    assert metadata.last_active_at == BASE_TIME + timedelta(minutes=5)


def test_validate_unknown_session_raises(manager: SessionManager):
    with pytest.raises(UnknownSessionError):
        manager.validate("does-not-exist")


def test_validate_rejects_idle_timeout(manager: SessionManager, user_id: str):
    session = manager.create(user_id, timestamp=BASE_TIME)

    with pytest.raises(SessionExpiredError):
        manager.validate(session.metadata.session_id, timestamp=BASE_TIME + timedelta(hours=1))


def test_validate_accepts_within_idle_window(manager: SessionManager, user_id: str):
    session = manager.create(user_id, timestamp=BASE_TIME)

    metadata = manager.validate(session.metadata.session_id, timestamp=BASE_TIME + timedelta(minutes=10))

    assert metadata.session_id == session.metadata.session_id


def test_validate_rejects_terminated_session(manager: SessionManager, user_id: str):
    session = manager.create(user_id, timestamp=BASE_TIME)
    manager.terminate(session.metadata.session_id, timestamp=BASE_TIME)

    with pytest.raises(SessionTerminatedError):
        manager.validate(session.metadata.session_id, timestamp=BASE_TIME)


def test_refresh_issues_new_tokens_and_extends_session(manager: SessionManager, user_id: str):
    session = manager.create(user_id, timestamp=BASE_TIME)

    refreshed = manager.refresh(session.metadata.session_id, timestamp=BASE_TIME + timedelta(minutes=5))

    assert refreshed.access_token != session.access_token
    assert refreshed.refresh_token != session.refresh_token


def test_refresh_unknown_session_raises(manager: SessionManager):
    with pytest.raises(UnknownSessionError):
        manager.refresh("does-not-exist")


def test_refresh_expired_session_raises(manager: SessionManager, user_id: str):
    session = manager.create(user_id, timestamp=BASE_TIME)

    with pytest.raises(SessionExpiredError):
        manager.refresh(session.metadata.session_id, timestamp=BASE_TIME + timedelta(hours=1))


def test_terminate_marks_session_terminated(manager: SessionManager, user_id: str):
    session = manager.create(user_id, timestamp=BASE_TIME)

    manager.terminate(session.metadata.session_id, timestamp=BASE_TIME)

    assert manager.sessions_for_user(user_id) == []


def test_terminate_unknown_session_raises(manager: SessionManager):
    with pytest.raises(UnknownSessionError):
        manager.terminate("does-not-exist")


def test_terminate_revokes_underlying_jwt(
    manager: SessionManager, jwt_service: JWTTokenService, user_id: str
):
    session = manager.create(user_id, timestamp=BASE_TIME)

    manager.terminate(session.metadata.session_id, timestamp=BASE_TIME)

    from backend.security.jwt_service import TokenRevokedError

    with pytest.raises(TokenRevokedError):
        jwt_service.validate(session.access_token, timestamp=BASE_TIME)


def test_sessions_for_user_tracks_concurrent_sessions(manager: SessionManager, user_id: str):
    manager.create(user_id, timestamp=BASE_TIME)
    manager.create(user_id, timestamp=BASE_TIME)

    sessions = manager.sessions_for_user(user_id)

    assert len(sessions) == 2


def test_sessions_for_user_empty_when_none(manager: SessionManager):
    assert manager.sessions_for_user("does-not-exist") == []


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(authentication_router)
    app.include_router(session_router)
    return TestClient(app)


def _register_user(client: TestClient, username: str) -> str:
    response = client.post("/security/register", json={"username": username, "password": "hunter2"})
    return response.json()["user_id"]


def test_api_create_session(client: TestClient):
    user_id = _register_user(client, "session-create")

    response = client.post("/security/sessions", json={"user_id": user_id})

    assert response.status_code == 200
    assert response.json()["access_token"].count(".") == 2


def test_api_create_session_unknown_user_returns_404(client: TestClient):
    response = client.post("/security/sessions", json={"user_id": "does-not-exist"})

    assert response.status_code == 404


def test_api_list_sessions(client: TestClient):
    user_id = _register_user(client, "session-list")
    client.post("/security/sessions", json={"user_id": user_id})
    client.post("/security/sessions", json={"user_id": user_id})

    response = client.get("/security/sessions", params={"user_id": user_id})

    assert response.status_code == 200
    assert len(response.json()) == 2


def test_api_refresh_session(client: TestClient):
    user_id = _register_user(client, "session-refresh")
    create_response = client.post("/security/sessions", json={"user_id": user_id})
    session_id = create_response.json()["session_id"]

    response = client.post(f"/security/sessions/{session_id}/refresh")

    assert response.status_code == 200
    assert response.json()["access_token"] != create_response.json()["access_token"]


def test_api_refresh_unknown_session_returns_404(client: TestClient):
    response = client.post("/security/sessions/does-not-exist/refresh")

    assert response.status_code == 404


def test_api_terminate_session(client: TestClient):
    user_id = _register_user(client, "session-terminate")
    create_response = client.post("/security/sessions", json={"user_id": user_id})
    session_id = create_response.json()["session_id"]

    response = client.delete(f"/security/sessions/{session_id}")

    assert response.status_code == 200
    assert response.json()["success"] is True


def test_api_terminate_unknown_session_returns_404(client: TestClient):
    response = client.delete("/security/sessions/does-not-exist")

    assert response.status_code == 404


def test_api_refresh_after_terminate_returns_410(client: TestClient):
    user_id = _register_user(client, "session-terminate-refresh")
    create_response = client.post("/security/sessions", json={"user_id": user_id})
    session_id = create_response.json()["session_id"]
    client.delete(f"/security/sessions/{session_id}")

    response = client.post(f"/security/sessions/{session_id}/refresh")

    assert response.status_code == 410
