from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.security.authentication import AuthenticationManager, UnknownUserError
from backend.security.jwt_manager import JWTTokenManager
from backend.security.session_lifecycle import (
    Session,
    SessionExpiredError,
    SessionManager,
    SessionMetadata,
    SessionRevokedError,
    UnknownSessionError,
    router as session_lifecycle_router,
)

BASE_TIME = datetime(2026, 8, 7, 18, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def authentication_manager() -> AuthenticationManager:
    return AuthenticationManager()


@pytest.fixture
def user_id(authentication_manager: AuthenticationManager) -> str:
    return authentication_manager.register("alice", "hunter2", timestamp=BASE_TIME).user_id


@pytest.fixture
def jwt_manager(authentication_manager: AuthenticationManager) -> JWTTokenManager:
    return JWTTokenManager(authentication_manager=authentication_manager, secret_key="test-secret")


@pytest.fixture
def manager(jwt_manager: JWTTokenManager) -> SessionManager:
    return SessionManager(
        jwt_manager=jwt_manager,
        idle_after=timedelta(minutes=5),
        idle_timeout=timedelta(minutes=30),
    )


def test_create_returns_session_with_tokens(manager: SessionManager, user_id: str):
    session = manager.create(user_id, timestamp=BASE_TIME)

    assert isinstance(session, Session)
    assert session.metadata.subject == user_id
    assert session.access_token.count(".") == 2
    assert session.metadata.state == "Active"


def test_create_requires_known_user(manager: SessionManager):
    with pytest.raises(UnknownUserError):
        manager.create("does-not-exist")


def test_validate_touches_last_active_and_stays_active(manager: SessionManager, user_id: str):
    session = manager.create(user_id, timestamp=BASE_TIME)

    metadata = manager.validate(session.metadata.session_id, timestamp=BASE_TIME + timedelta(minutes=2))

    assert metadata.last_active_at == BASE_TIME + timedelta(minutes=2)
    assert metadata.state == "Active"


def test_validate_marks_idle_after_idle_after_window(manager: SessionManager, user_id: str):
    session = manager.create(user_id, timestamp=BASE_TIME)

    metadata = manager.validate(session.metadata.session_id, timestamp=BASE_TIME + timedelta(minutes=10))

    assert metadata.state == "Idle"


def test_validate_unknown_session_raises(manager: SessionManager):
    with pytest.raises(UnknownSessionError):
        manager.validate("does-not-exist")


def test_validate_rejects_after_idle_timeout(manager: SessionManager, user_id: str):
    session = manager.create(user_id, timestamp=BASE_TIME)

    with pytest.raises(SessionExpiredError):
        manager.validate(session.metadata.session_id, timestamp=BASE_TIME + timedelta(hours=1))


def test_validate_rejects_revoked_session(manager: SessionManager, user_id: str):
    session = manager.create(user_id, timestamp=BASE_TIME)
    manager.terminate(session.metadata.session_id, timestamp=BASE_TIME)

    with pytest.raises(SessionRevokedError):
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


def test_terminate_marks_session_revoked(manager: SessionManager, user_id: str):
    session = manager.create(user_id, timestamp=BASE_TIME)

    manager.terminate(session.metadata.session_id, timestamp=BASE_TIME)

    assert manager.sessions_for_subject(user_id) == []


def test_terminate_unknown_session_raises(manager: SessionManager):
    with pytest.raises(UnknownSessionError):
        manager.terminate("does-not-exist")


def test_terminate_revokes_underlying_jwt(manager: SessionManager, jwt_manager: JWTTokenManager, user_id: str):
    session = manager.create(user_id, timestamp=BASE_TIME)

    manager.terminate(session.metadata.session_id, timestamp=BASE_TIME)

    from backend.security.jwt_manager import JWTRevokedError

    with pytest.raises(JWTRevokedError):
        jwt_manager.validate(session.access_token, timestamp=BASE_TIME)


def test_sessions_for_subject_tracks_concurrent_sessions(manager: SessionManager, user_id: str):
    manager.create(user_id, timestamp=BASE_TIME)
    manager.create(user_id, timestamp=BASE_TIME)

    sessions = manager.sessions_for_subject(user_id)

    assert len(sessions) == 2


def test_sessions_for_subject_empty_when_none(manager: SessionManager):
    assert manager.sessions_for_subject("does-not-exist") == []


def test_idle_timeout_capped_by_refresh_ttl(jwt_manager: JWTTokenManager):
    manager = SessionManager(jwt_manager=jwt_manager, idle_timeout=timedelta(days=365))

    assert manager._idle_timeout == jwt_manager.ttl_for("Refresh")


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(session_lifecycle_router)
    return TestClient(app)


def _register_user(authentication_manager: AuthenticationManager, username: str) -> str:
    return authentication_manager.register(username, "hunter2", timestamp=BASE_TIME).user_id


def test_api_create_session(client: TestClient, manager: SessionManager, monkeypatch):
    from backend.security import session_lifecycle as session_lifecycle_module

    monkeypatch.setattr(session_lifecycle_module, "_session_manager", manager)
    user = _register_user(manager._jwt_manager._authentication_manager, "api-create")

    response = client.post("/security/sessions", json={"subject": user})

    assert response.status_code == 200
    assert response.json()["access_token"].count(".") == 2


def test_api_create_session_unknown_user_returns_404(client: TestClient, manager: SessionManager, monkeypatch):
    from backend.security import session_lifecycle as session_lifecycle_module

    monkeypatch.setattr(session_lifecycle_module, "_session_manager", manager)

    response = client.post("/security/sessions", json={"subject": "does-not-exist"})

    assert response.status_code == 404


def test_api_get_session(client: TestClient, manager: SessionManager, monkeypatch):
    from backend.security import session_lifecycle as session_lifecycle_module

    monkeypatch.setattr(session_lifecycle_module, "_session_manager", manager)
    user = _register_user(manager._jwt_manager._authentication_manager, "api-get")
    created = manager.create(user)

    response = client.get(f"/security/sessions/{created.metadata.session_id}")

    assert response.status_code == 200
    assert response.json()["state"] == "Active"


def test_api_get_unknown_session_returns_404(client: TestClient, manager: SessionManager, monkeypatch):
    from backend.security import session_lifecycle as session_lifecycle_module

    monkeypatch.setattr(session_lifecycle_module, "_session_manager", manager)

    response = client.get("/security/sessions/does-not-exist")

    assert response.status_code == 404


def test_api_refresh_session(client: TestClient, manager: SessionManager, monkeypatch):
    from backend.security import session_lifecycle as session_lifecycle_module

    monkeypatch.setattr(session_lifecycle_module, "_session_manager", manager)
    user = _register_user(manager._jwt_manager._authentication_manager, "api-refresh")
    created = manager.create(user)

    response = client.post(f"/security/sessions/{created.metadata.session_id}/refresh")

    assert response.status_code == 200
    assert response.json()["access_token"] != created.access_token


def test_api_refresh_unknown_session_returns_404(client: TestClient, manager: SessionManager, monkeypatch):
    from backend.security import session_lifecycle as session_lifecycle_module

    monkeypatch.setattr(session_lifecycle_module, "_session_manager", manager)

    response = client.post("/security/sessions/does-not-exist/refresh")

    assert response.status_code == 404


def test_api_terminate_session(client: TestClient, manager: SessionManager, monkeypatch):
    from backend.security import session_lifecycle as session_lifecycle_module

    monkeypatch.setattr(session_lifecycle_module, "_session_manager", manager)
    user = _register_user(manager._jwt_manager._authentication_manager, "api-terminate")
    created = manager.create(user)

    response = client.delete(f"/security/sessions/{created.metadata.session_id}")

    assert response.status_code == 200
    assert response.json()["success"] is True


def test_api_terminate_unknown_session_returns_404(client: TestClient, manager: SessionManager, monkeypatch):
    from backend.security import session_lifecycle as session_lifecycle_module

    monkeypatch.setattr(session_lifecycle_module, "_session_manager", manager)

    response = client.delete("/security/sessions/does-not-exist")

    assert response.status_code == 404


def test_api_get_after_terminate_returns_410(client: TestClient, manager: SessionManager, monkeypatch):
    from backend.security import session_lifecycle as session_lifecycle_module

    monkeypatch.setattr(session_lifecycle_module, "_session_manager", manager)
    user = _register_user(manager._jwt_manager._authentication_manager, "api-terminate-get")
    created = manager.create(user)
    client.delete(f"/security/sessions/{created.metadata.session_id}")

    response = client.get(f"/security/sessions/{created.metadata.session_id}")

    assert response.status_code == 410
