from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_authentication import (
    BUILT_IN_AUTHENTICATION_PROVIDERS,
    AuthenticationResult,
    DeploymentAuthenticationManager,
    DeploymentIdentity,
    get_authentication_manager,
)
from backend.observability.deployment_governance_event_bus import (
    GovernanceEventBus,
)

BASE_TIME = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


def _manager(**kwargs) -> DeploymentAuthenticationManager:
    return DeploymentAuthenticationManager(clock=_clock, **kwargs)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """
    The authentication manager is a process-wide singleton; most tests
    below construct their own fresh manager instead (see _manager),
    and only the singleton and API tests touch the shared instance,
    matching test_deployment_governance_rbac.py's own fixture.
    """

    def _reset():
        get_authentication_manager().clear()

    _reset()
    yield
    _reset()


# --- Models --------------------------------------------------------------


class TestDeploymentIdentity:

    def test_rejects_empty_identity_id(self):
        with pytest.raises(
            ValueError, match="identity_id must not be empty"
        ):
            DeploymentIdentity(
                identity_id="", principal="p1", provider="LOCAL",
                authenticated_at=BASE_TIME,
            )

    def test_rejects_empty_principal(self):
        with pytest.raises(
            ValueError, match="principal must not be empty"
        ):
            DeploymentIdentity(
                identity_id="i1", principal="", provider="LOCAL",
                authenticated_at=BASE_TIME,
            )

    def test_rejects_naive_authenticated_at(self):
        with pytest.raises(
            ValueError, match="authenticated_at must be timezone-aware"
        ):
            DeploymentIdentity(
                identity_id="i1", principal="p1", provider="LOCAL",
                authenticated_at=datetime(2026, 7, 24, 12, 0, 0),
            )

    def test_to_dict(self):
        identity = DeploymentIdentity(
            identity_id="i1", principal="p1", provider="LOCAL",
            authenticated_at=BASE_TIME,
        )

        assert identity.to_dict() == {
            "identity_id": "i1",
            "principal": "p1",
            "provider": "LOCAL",
            "authenticated_at": BASE_TIME.isoformat(),
        }


class TestAuthenticationResult:

    def test_rejects_missing_identity_when_authenticated(self):
        with pytest.raises(
            ValueError, match="identity must be set"
        ):
            AuthenticationResult(
                authenticated=True, identity=None, reason=None
            )

    def test_rejects_reason_when_authenticated(self):
        identity = DeploymentIdentity(
            identity_id="i1", principal="p1", provider="LOCAL",
            authenticated_at=BASE_TIME,
        )

        with pytest.raises(
            ValueError, match="reason must not be set"
        ):
            AuthenticationResult(
                authenticated=True, identity=identity, reason="why",
            )

    def test_rejects_identity_when_not_authenticated(self):
        identity = DeploymentIdentity(
            identity_id="i1", principal="p1", provider="LOCAL",
            authenticated_at=BASE_TIME,
        )

        with pytest.raises(
            ValueError, match="identity must not be set"
        ):
            AuthenticationResult(
                authenticated=False, identity=identity, reason="why",
            )

    def test_rejects_missing_reason_when_not_authenticated(self):
        with pytest.raises(ValueError, match="reason must be set"):
            AuthenticationResult(
                authenticated=False, identity=None, reason=None
            )

    def test_to_dict_denied(self):
        result = AuthenticationResult(
            authenticated=False, identity=None, reason="nope"
        )

        assert result.to_dict() == {
            "authenticated": False, "identity": None, "reason": "nope",
        }


# --- Successful authentication ---------------------------------------------


class TestSuccessfulAuthentication:

    def test_local_provider(self):
        manager = _manager()
        manager.register_local_credential("p1", "hunter2")

        result = manager.authenticate(
            "p1", "LOCAL", {"password": "hunter2"}
        )

        assert result.authenticated is True
        assert result.identity.principal == "p1"
        assert result.identity.provider == "LOCAL"

    def test_api_key_provider(self):
        manager = _manager()
        manager.register_api_key("key-123", "p1")

        result = manager.authenticate(
            "p1", "API_KEY", {"api_key": "key-123"}
        )

        assert result.authenticated is True
        assert result.identity.provider == "API_KEY"

    def test_bearer_token_provider(self):
        manager = _manager()
        manager.issue_bearer_token("p1", "tok-abc")

        result = manager.authenticate(
            "p1", "BEARER_TOKEN", {"token": "tok-abc"}
        )

        assert result.authenticated is True
        assert result.identity.provider == "BEARER_TOKEN"

    def test_publishes_authentication_succeeded(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("authentication_succeeded", events.append)
        manager = _manager(event_bus=bus)
        manager.register_local_credential("p1", "hunter2")

        manager.authenticate("p1", "LOCAL", {"password": "hunter2"})

        assert len(events) == 1

    def test_issues_a_registered_session(self):
        manager = _manager()
        manager.register_local_credential("p1", "hunter2")

        result = manager.authenticate(
            "p1", "LOCAL", {"password": "hunter2"}
        )

        status = manager.status(result.identity.identity_id)

        assert status.authenticated is True

    def test_built_in_providers_constant(self):
        assert set(BUILT_IN_AUTHENTICATION_PROVIDERS) == {
            "LOCAL", "API_KEY", "BEARER_TOKEN",
        }


# --- Invalid credentials -----------------------------------------------------


class TestInvalidCredentials:

    def test_local_wrong_password(self):
        manager = _manager()
        manager.register_local_credential("p1", "hunter2")

        result = manager.authenticate(
            "p1", "LOCAL", {"password": "wrong"}
        )

        assert result.authenticated is False
        assert result.identity is None

    def test_local_unknown_principal(self):
        manager = _manager()

        result = manager.authenticate(
            "does-not-exist", "LOCAL", {"password": "x"}
        )

        assert result.authenticated is False

    def test_api_key_unknown(self):
        manager = _manager()

        result = manager.authenticate(
            "p1", "API_KEY", {"api_key": "does-not-exist"}
        )

        assert result.authenticated is False

    def test_bearer_token_wrong_owner(self):
        manager = _manager()
        manager.issue_bearer_token("p1", "tok-abc")

        result = manager.authenticate(
            "p2", "BEARER_TOKEN", {"token": "tok-abc"}
        )

        assert result.authenticated is False

    def test_unknown_provider(self):
        manager = _manager()

        result = manager.authenticate("p1", "OAUTH", {})

        assert result.authenticated is False
        assert "unknown authentication provider" in result.reason

    def test_publishes_authentication_failed(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("authentication_failed", events.append)
        manager = _manager(event_bus=bus)

        manager.authenticate("p1", "LOCAL", {"password": "x"})

        assert len(events) == 1

    def test_rejects_empty_principal(self):
        manager = _manager()

        with pytest.raises(
            ValueError, match="principal must not be empty"
        ):
            manager.authenticate("", "LOCAL", {})

    def test_rejects_empty_provider(self):
        manager = _manager()

        with pytest.raises(
            ValueError, match="provider must not be empty"
        ):
            manager.authenticate("p1", "", {})


# --- Expired token -----------------------------------------------------------


class TestExpiredToken:

    def test_authenticate_denies_already_expired_token(self):
        manager = _manager()
        manager.issue_bearer_token(
            "p1", "tok-abc", expires_at=BASE_TIME - timedelta(seconds=1),
        )

        result = manager.authenticate(
            "p1", "BEARER_TOKEN", {"token": "tok-abc"}
        )

        assert result.authenticated is False
        assert result.reason == "token has expired"

    def test_validate_denies_once_clock_passes_expiry(self):
        clock_box = {"now": BASE_TIME}
        manager = DeploymentAuthenticationManager(
            clock=lambda: clock_box["now"]
        )
        manager.issue_bearer_token(
            "p1", "tok-abc",
            expires_at=BASE_TIME + timedelta(minutes=5),
        )

        result = manager.authenticate(
            "p1", "BEARER_TOKEN", {"token": "tok-abc"}
        )

        assert result.authenticated is True

        clock_box["now"] = BASE_TIME + timedelta(minutes=10)

        status = manager.status(result.identity.identity_id)

        assert status.authenticated is False
        assert status.reason == "token has expired"

    def test_tokens_without_expiry_never_expire(self):
        manager = _manager()
        manager.issue_bearer_token("p1", "tok-abc")

        result = manager.authenticate(
            "p1", "BEARER_TOKEN", {"token": "tok-abc"}
        )

        status = manager.status(result.identity.identity_id)

        assert status.authenticated is True


# --- Revoke flow -------------------------------------------------------------


class TestRevokeFlow:

    def test_revoke_denies_subsequent_status(self):
        manager = _manager()
        manager.register_local_credential("p1", "hunter2")
        result = manager.authenticate(
            "p1", "LOCAL", {"password": "hunter2"}
        )

        manager.revoke(result.identity.identity_id)

        status = manager.status(result.identity.identity_id)

        assert status.authenticated is False
        assert status.reason == "identity has been revoked"

    def test_revoke_unknown_identity_raises(self):
        manager = _manager()

        with pytest.raises(KeyError):
            manager.revoke("does-not-exist")

    def test_revoke_is_idempotent(self):
        manager = _manager()
        manager.register_local_credential("p1", "hunter2")
        result = manager.authenticate(
            "p1", "LOCAL", {"password": "hunter2"}
        )

        manager.revoke(result.identity.identity_id)
        manager.revoke(result.identity.identity_id)

        status = manager.status(result.identity.identity_id)

        assert status.authenticated is False

    def test_publishes_authentication_revoked_once(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("authentication_revoked", events.append)
        manager = _manager(event_bus=bus)
        manager.register_local_credential("p1", "hunter2")
        result = manager.authenticate(
            "p1", "LOCAL", {"password": "hunter2"}
        )

        manager.revoke(result.identity.identity_id)
        manager.revoke(result.identity.identity_id)

        assert len(events) == 1


# --- Status lookup -----------------------------------------------------------


class TestStatusLookup:

    def test_status_of_unknown_identity_raises(self):
        manager = _manager()

        with pytest.raises(KeyError):
            manager.status("does-not-exist")

    def test_validate_matches_status(self):
        manager = _manager()
        manager.register_local_credential("p1", "hunter2")
        result = manager.authenticate(
            "p1", "LOCAL", {"password": "hunter2"}
        )

        assert manager.validate(
            result.identity.identity_id
        ) == manager.status(result.identity.identity_id)


# --- Clear -------------------------------------------------------------------


class TestClear:

    def test_clear_removes_sessions_and_credentials(self):
        manager = _manager()
        manager.register_local_credential("p1", "hunter2")
        result = manager.authenticate(
            "p1", "LOCAL", {"password": "hunter2"}
        )

        manager.clear()

        with pytest.raises(KeyError):
            manager.status(result.identity.identity_id)

        denied = manager.authenticate(
            "p1", "LOCAL", {"password": "hunter2"}
        )

        assert denied.authenticated is False


# --- RBAC integration (isolated: authorize_identity only) -------------------


class TestRbacIntegration:

    def test_authorize_identity_delegates_to_authorize(self):
        from backend.observability.deployment_governance_rbac import (
            DeploymentRBACEngine,
        )

        rbac_engine = DeploymentRBACEngine(clock=_clock)
        rbac_engine.assign_role("p1", "Developer")

        identity = DeploymentIdentity(
            identity_id="i1", principal="p1", provider="LOCAL",
            authenticated_at=BASE_TIME,
        )

        decision = rbac_engine.authorize_identity(
            identity, "deployment.deploy"
        )

        assert decision.allowed is True
        assert decision.principal_id == "p1"


# --- Singleton ---------------------------------------------------------


class TestSingleton:

    def test_get_authentication_manager_returns_same_instance(self):
        assert (
            get_authentication_manager()
            is get_authentication_manager()
        )


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceSecurityAuthenticationApi:

    def test_post_authenticate_success(self, client):
        get_authentication_manager().register_local_credential(
            "api-p-1", "hunter2"
        )

        response = client.post(
            "/governance/security/authenticate",
            params={
                "principal": "api-p-1",
                "provider": "LOCAL",
                "credentials": '{"password": "hunter2"}',
            },
        )

        assert response.status_code == 200
        assert response.json()["authenticated"] is True

    def test_post_authenticate_invalid_credentials(self, client):
        response = client.post(
            "/governance/security/authenticate",
            params={
                "principal": "api-p-2",
                "provider": "LOCAL",
                "credentials": '{"password": "wrong"}',
            },
        )

        assert response.status_code == 200
        assert response.json()["authenticated"] is False

    def test_get_status_unknown_returns_404(self, client):
        response = client.get(
            "/governance/security/status/does-not-exist"
        )

        assert response.status_code == 404

    def test_full_authenticate_status_revoke_flow(self, client):
        get_authentication_manager().register_local_credential(
            "api-p-3", "hunter2"
        )

        authenticate_response = client.post(
            "/governance/security/authenticate",
            params={
                "principal": "api-p-3",
                "provider": "LOCAL",
                "credentials": '{"password": "hunter2"}',
            },
        )

        identity_id = (
            authenticate_response.json()["identity"]["identity_id"]
        )

        status_response = client.get(
            f"/governance/security/status/{identity_id}"
        )

        assert status_response.status_code == 200
        assert status_response.json()["authenticated"] is True

        revoke_response = client.post(
            "/governance/security/revoke",
            params={"identity_id": identity_id},
        )

        assert revoke_response.status_code == 200
        assert revoke_response.json() == {"revoked": identity_id}

        post_revoke_status = client.get(
            f"/governance/security/status/{identity_id}"
        )

        assert post_revoke_status.json()["authenticated"] is False

    def test_post_revoke_unknown_returns_404(self, client):
        response = client.post(
            "/governance/security/revoke",
            params={"identity_id": "does-not-exist"},
        )

        assert response.status_code == 404
