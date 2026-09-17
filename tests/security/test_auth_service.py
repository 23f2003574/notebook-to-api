from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.security.auth_service import (
    AccountLockedError,
    AuthenticationRequest,
    AuthenticationResult,
    AuthenticationService,
    InvalidAuthenticationTypeError,
    OAuthProvider,
    UnknownProviderError,
    UnknownSessionError,
    router as auth_service_router,
)
from backend.security.identity_registry import IdentityRegistry

BASE_TIME = datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def identity_registry() -> IdentityRegistry:
    return IdentityRegistry()


@pytest.fixture
def service(identity_registry: IdentityRegistry) -> AuthenticationService:
    return AuthenticationService(identity_registry=identity_registry, max_failed_attempts=3)


def test_authenticate_succeeds_with_enrolled_credentials(service: AuthenticationService):
    service.enroll_credentials("Username/Password", "alice", "hunter2")

    result = service.authenticate(
        AuthenticationRequest(
            auth_type="Username/Password", identifier="alice", secret="hunter2", metadata={}
        ),
        timestamp=BASE_TIME,
    )

    assert isinstance(result, AuthenticationResult)
    assert result.success is True
    assert result.identity_id is not None
    assert result.session_token is not None


def test_authenticate_reuses_identity_across_logins(service: AuthenticationService):
    service.enroll_credentials("Username/Password", "alice", "hunter2")
    request = AuthenticationRequest(
        auth_type="Username/Password", identifier="alice", secret="hunter2", metadata={}
    )

    first = service.authenticate(request, timestamp=BASE_TIME)
    second = service.authenticate(request, timestamp=BASE_TIME)

    assert first.identity_id == second.identity_id
    assert first.session_token != second.session_token


def test_authenticate_invalid_credentials_fails(service: AuthenticationService):
    service.enroll_credentials("Username/Password", "alice", "hunter2")

    result = service.authenticate(
        AuthenticationRequest(
            auth_type="Username/Password", identifier="alice", secret="wrong", metadata={}
        )
    )

    assert result.success is False
    assert result.session_token is None


def test_authenticate_unknown_provider_raises(service: AuthenticationService):
    with pytest.raises(UnknownProviderError):
        service.authenticate(
            AuthenticationRequest(auth_type="Carrier Pigeon", identifier="x", secret="y", metadata={})
        )


def test_failed_attempts_tracked_and_reset_on_success(service: AuthenticationService):
    service.enroll_credentials("Username/Password", "alice", "hunter2")
    bad_request = AuthenticationRequest(
        auth_type="Username/Password", identifier="alice", secret="wrong", metadata={}
    )
    service.authenticate(bad_request)
    service.authenticate(bad_request)

    assert service.failed_attempts("alice") == 2

    good_request = AuthenticationRequest(
        auth_type="Username/Password", identifier="alice", secret="hunter2", metadata={}
    )
    service.authenticate(good_request)

    assert service.failed_attempts("alice") == 0


def test_account_locked_after_max_failed_attempts(service: AuthenticationService):
    service.enroll_credentials("Username/Password", "alice", "hunter2")
    bad_request = AuthenticationRequest(
        auth_type="Username/Password", identifier="alice", secret="wrong", metadata={}
    )
    for _ in range(3):
        service.authenticate(bad_request)

    with pytest.raises(AccountLockedError):
        service.authenticate(bad_request)


def test_authenticate_service_accepts_api_client(service: AuthenticationService):
    service.enroll_credentials("API Client", "client-1", "secret-1")

    result = service.authenticate_service("client-1", "secret-1")

    assert result.success is True
    assert result.auth_type == "API Client"


def test_authenticate_service_rejects_non_service_type(service: AuthenticationService):
    with pytest.raises(InvalidAuthenticationTypeError):
        service.authenticate_service("alice", "hunter2", auth_type="Username/Password")


def test_oauth_provider_uses_pluggable_verifier(service: AuthenticationService):
    service.register_provider("OAuth Provider", OAuthProvider(lambda identifier, token: token == "valid-token"))

    result = service.authenticate(
        AuthenticationRequest(
            auth_type="OAuth Provider", identifier="alice", secret="valid-token", metadata={}
        )
    )

    assert result.success is True


def test_verify_credentials_checks_provider_directly(service: AuthenticationService):
    service.enroll_credentials("Username/Password", "alice", "hunter2")

    assert service.verify_credentials("Username/Password", "alice", "hunter2") is True
    assert service.verify_credentials("Username/Password", "alice", "wrong") is False


def test_logout_ends_session(service: AuthenticationService):
    service.enroll_credentials("Username/Password", "alice", "hunter2")
    result = service.authenticate(
        AuthenticationRequest(
            auth_type="Username/Password", identifier="alice", secret="hunter2", metadata={}
        )
    )

    service.logout(result.session_token)

    with pytest.raises(UnknownSessionError):
        service.verify_session(result.session_token)


def test_logout_unknown_session_raises(service: AuthenticationService):
    with pytest.raises(UnknownSessionError):
        service.logout("does-not-exist")


def test_providers_lists_registered_provider_names(service: AuthenticationService):
    auth_types = {entry["auth_type"] for entry in service.providers()}

    assert auth_types == {"Username/Password", "API Client", "OAuth Provider", "Service Account"}


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(auth_service_router)
    return TestClient(app)


def test_api_login_success(client: TestClient):
    from backend.security.auth_service import get_authentication_service

    get_authentication_service().enroll_credentials("Username/Password", "authsvc-login-alice", "hunter2")

    response = client.post(
        "/security/auth/login",
        json={"auth_type": "Username/Password", "identifier": "authsvc-login-alice", "secret": "hunter2"},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True


def test_api_login_invalid_credentials_returns_401(client: TestClient):
    response = client.post(
        "/security/auth/login",
        json={"auth_type": "Username/Password", "identifier": "nobody", "secret": "wrong"},
    )

    assert response.status_code == 401


def test_api_login_unknown_provider_returns_422(client: TestClient):
    response = client.post(
        "/security/auth/login",
        json={"auth_type": "Carrier Pigeon", "identifier": "x", "secret": "y"},
    )

    assert response.status_code == 422


def test_api_logout(client: TestClient):
    from backend.security.auth_service import get_authentication_service

    get_authentication_service().enroll_credentials("Username/Password", "authsvc-logout-alice", "hunter2")
    login_response = client.post(
        "/security/auth/login",
        json={"auth_type": "Username/Password", "identifier": "authsvc-logout-alice", "secret": "hunter2"},
    )
    session_token = login_response.json()["session_token"]

    response = client.post("/security/auth/logout", json={"session_token": session_token})

    assert response.status_code == 200
    assert response.json()["success"] is True


def test_api_logout_unknown_session_returns_404(client: TestClient):
    response = client.post("/security/auth/logout", json={"session_token": "does-not-exist"})

    assert response.status_code == 404


def test_api_verify_valid_session(client: TestClient):
    from backend.security.auth_service import get_authentication_service

    get_authentication_service().enroll_credentials("Username/Password", "authsvc-verify-alice", "hunter2")
    login_response = client.post(
        "/security/auth/login",
        json={"auth_type": "Username/Password", "identifier": "authsvc-verify-alice", "secret": "hunter2"},
    )
    session_token = login_response.json()["session_token"]

    response = client.post("/security/auth/verify", json={"session_token": session_token})

    assert response.status_code == 200
    assert response.json()["valid"] is True


def test_api_verify_unknown_session_returns_404(client: TestClient):
    response = client.post("/security/auth/verify", json={"session_token": "does-not-exist"})

    assert response.status_code == 404


def test_api_list_providers(client: TestClient):
    response = client.get("/security/auth/providers")

    assert response.status_code == 200
    auth_types = {entry["auth_type"] for entry in response.json()}
    assert "Username/Password" in auth_types
    assert "OAuth Provider" in auth_types
