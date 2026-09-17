import pytest
from fastapi.testclient import TestClient

from backend.security.security_bootstrap import (
    REQUIRED_SERVICES,
    SecurityBootstrap,
    SecurityBootstrapError,
    UnknownServiceError,
    bootstrap_security_subsystem,
    get_security_bootstrap,
)


def test_register_services_wires_every_required_service():
    bootstrap = SecurityBootstrap()

    services = bootstrap.register_services()

    assert set(services.keys()) == set(REQUIRED_SERVICES)
    assert all(value is not None for value in services.values())


def test_registered_services_reflects_last_call():
    bootstrap = SecurityBootstrap()
    bootstrap.register_services()

    assert set(bootstrap.registered_services().keys()) == set(REQUIRED_SERVICES)


def test_discover_returns_named_service():
    bootstrap = SecurityBootstrap()
    bootstrap.register_services()

    service = bootstrap.discover("identity_registry")

    assert service is not None


def test_discover_unknown_service_raises():
    bootstrap = SecurityBootstrap()
    bootstrap.register_services()

    with pytest.raises(UnknownServiceError):
        bootstrap.discover("does-not-exist")


def test_wire_components_confirms_security_prefix():
    bootstrap = SecurityBootstrap()

    assert bootstrap.wire_components() is True


def test_initialize_registers_automatically_if_not_yet_registered():
    bootstrap = SecurityBootstrap()

    result = bootstrap.initialize()

    assert result["valid"] is True
    assert set(result["registered_services"]) == set(REQUIRED_SERVICES)
    assert result["missing_services"] == ()


def test_initialize_raises_when_a_required_service_is_missing():
    bootstrap = SecurityBootstrap()
    with bootstrap._lock:
        bootstrap._services = {
            name: object() for name in REQUIRED_SERVICES if name != "dashboard_api"
        }

    with pytest.raises(SecurityBootstrapError) as exc_info:
        bootstrap.initialize()

    assert exc_info.value.missing_services == ("dashboard_api",)


def test_shutdown_clears_registered_services():
    bootstrap = SecurityBootstrap()
    bootstrap.register_services()

    bootstrap.shutdown()

    assert bootstrap.registered_services() == {}


def test_get_security_bootstrap_returns_singleton():
    assert get_security_bootstrap() is get_security_bootstrap()


def test_bootstrap_security_subsystem_is_valid():
    result = bootstrap_security_subsystem()

    assert result["valid"] is True


def test_bootstrap_security_subsystem_is_idempotent():
    bootstrap_security_subsystem()
    result = bootstrap_security_subsystem()

    assert result["valid"] is True


def test_end_to_end_security_pipeline():
    from backend.security.identity_registry import IdentityRegistry
    from backend.security.auth_service import AuthenticationRequest, AuthenticationService
    from backend.security.jwt_manager import JWTTokenManager
    from backend.security.rbac_engine import RBACEngine
    from backend.security.permission_engine import PermissionEngine
    from backend.security.api_key_manager import APIKeyManager
    from backend.security.secret_vault import SecretVaultService
    from backend.security.audit_logger import SecurityAuditLogger
    from backend.security.rbac import RoleBasedAccessControl
    from backend.security.session_manager import SessionManager as LegacySessionManager
    from backend.security.jwt_service import JWTTokenService
    from backend.security.authentication import AuthenticationManager

    identity_registry = IdentityRegistry()
    auth_service = AuthenticationService(identity_registry=identity_registry)
    auth_service.enroll_credentials("Username/Password", "alice", "hunter2")

    result = auth_service.authenticate(
        AuthenticationRequest(auth_type="Username/Password", identifier="alice", secret="hunter2", metadata={})
    )
    assert result.success is True

    authentication_manager = AuthenticationManager()
    authentication_manager.register("alice", "hunter2")
    jwt_manager = JWTTokenManager(authentication_manager=authentication_manager)
    access, refresh = jwt_manager.issue(authentication_manager.get_user_id("alice"))
    assert access.token.count(".") == 2

    legacy_session_manager = LegacySessionManager(jwt_service=JWTTokenService(authentication_manager=authentication_manager))
    rbac_engine = RBACEngine(session_manager=legacy_session_manager)
    rbac_engine.assign_role(result.identity_id, "Developer")
    assert rbac_engine.authorize(result.identity_id, "write_resource") is True

    legacy_rbac = RoleBasedAccessControl(authentication_manager=authentication_manager)
    permission_engine = PermissionEngine(rbac=legacy_rbac)
    permission_engine.grant(result.identity_id, "orders", "Read")
    assert permission_engine.check(result.identity_id, "orders", "Read") is True

    secret_vault = SecretVaultService()
    api_key_manager = APIKeyManager(permission_engine=permission_engine, secret_vault=secret_vault)
    api_key = api_key_manager.create_key(result.identity_id, "ci-key", "User", scopes=["orders:Read"])
    assert api_key_manager.check_scope(api_key.secret, "orders", "Read") is True

    audit_logger = SecurityAuditLogger()
    audit_logger.record("Authentication", result.identity_id, f"user:alice", "login")
    assert audit_logger.count() == 1


@pytest.fixture
def client() -> TestClient:
    from backend.app import app

    return TestClient(app)


def test_api_root_still_reachable(client: TestClient):
    response = client.get("/")

    assert response.status_code == 200


def test_api_legacy_and_v2_security_routes_both_live(client: TestClient):
    legacy_response = client.get("/security/dashboard")
    v2_response = client.get("/v2/security/dashboard")

    assert legacy_response.status_code == 200
    assert v2_response.status_code == 200
    assert set(v2_response.json().keys()) == {"identities", "audit", "analytics"}


def test_api_v2_identity_registry_reachable(client: TestClient):
    response = client.post(
        "/v2/security/identities", json={"display_name": "app-alice", "identity_type": "user"}
    )

    assert response.status_code == 200
    assert response.json()["display_name"] == "app-alice"
