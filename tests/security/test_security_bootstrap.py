import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.security.bootstrap import (
    DEFAULT_PERMISSIONS,
    DEFAULT_POLICY_NAMES,
    REQUIRED_SERVICES,
    SUBSYSTEM_NAME,
    SecurityBootstrap,
    SecurityBootstrapError,
    UnknownServiceError,
    bootstrap_security_subsystem,
    get_security_bootstrap,
)


def test_register_wires_every_required_service():
    bootstrap = SecurityBootstrap()

    services = bootstrap.register()

    assert set(services) == set(REQUIRED_SERVICES)
    assert all(value is not None for value in services.values())


def test_registered_services_reflects_last_register_call():
    bootstrap = SecurityBootstrap()

    assert bootstrap.registered_services() == {}

    bootstrap.register()

    assert set(bootstrap.registered_services()) == set(REQUIRED_SERVICES)


def test_discover_returns_named_service():
    bootstrap = SecurityBootstrap()
    bootstrap.register()

    session_manager = bootstrap.discover("session_manager")

    assert session_manager is bootstrap.registered_services()["session_manager"]


def test_discover_unknown_service_raises():
    bootstrap = SecurityBootstrap()
    bootstrap.register()

    with pytest.raises(UnknownServiceError):
        bootstrap.discover("does-not-exist")


def test_validate_registers_automatically_if_not_yet_registered():
    bootstrap = SecurityBootstrap()

    result = bootstrap.validate()

    assert result.valid is True
    assert set(result.registered_services) == set(REQUIRED_SERVICES)
    assert result.missing_services == ()


def test_validate_raises_when_a_required_service_is_missing():
    bootstrap = SecurityBootstrap()
    with bootstrap._lock:
        bootstrap._services = {
            name: object() for name in REQUIRED_SERVICES if name != "dashboard_api"
        }

    with pytest.raises(SecurityBootstrapError) as exc_info:
        bootstrap.validate()

    assert exc_info.value.result.missing_services == ("dashboard_api",)
    assert exc_info.value.result.valid is False


def test_health_check_delegates_to_the_dashboard():
    bootstrap = SecurityBootstrap()
    bootstrap.register()

    report = bootstrap.health_check()

    assert report["status"] == "ok"
    assert "authentication" in report


def test_health_check_raises_when_dashboard_not_registered():
    bootstrap = SecurityBootstrap()

    with pytest.raises(SecurityBootstrapError):
        bootstrap.health_check()


def test_initialize_default_roles_creates_all_roles():
    bootstrap = SecurityBootstrap()
    bootstrap.register()

    created = bootstrap.initialize_default_roles()

    rbac = bootstrap.discover("rbac")
    for role_name in created:
        assert rbac.get_role(role_name).name == role_name


def test_initialize_default_roles_is_idempotent():
    bootstrap = SecurityBootstrap()
    bootstrap.register()

    bootstrap.initialize_default_roles()
    bootstrap.initialize_default_roles()

    rbac = bootstrap.discover("rbac")
    assert rbac.get_role("Admin").name == "Admin"


def test_initialize_default_permissions_grants_configured_access():
    bootstrap = SecurityBootstrap()
    bootstrap.register()
    bootstrap.initialize_default_roles()

    granted = bootstrap.initialize_default_permissions()

    assert granted == DEFAULT_PERMISSIONS
    permission_engine = bootstrap.discover("permission_engine")
    permissions = permission_engine.permissions_for_role("Viewer", include_inherited=False)
    assert any(permission.action == "Read" for permission in permissions)


def test_initialize_default_permissions_is_idempotent():
    bootstrap = SecurityBootstrap()
    bootstrap.register()
    bootstrap.initialize_default_roles()

    bootstrap.initialize_default_permissions()
    bootstrap.initialize_default_permissions()

    permission_engine = bootstrap.discover("permission_engine")
    permissions = permission_engine.permissions_for_role("Admin", include_inherited=False)
    assert len(permissions) == 1


def test_initialize_security_policies_registers_all_builtins():
    bootstrap = SecurityBootstrap()
    bootstrap.register()

    registered = bootstrap.initialize_security_policies()

    assert registered == DEFAULT_POLICY_NAMES
    policy_engine = bootstrap.discover("security_policy_engine")
    for name in DEFAULT_POLICY_NAMES:
        assert policy_engine.get_policy(name).name == name


def test_initialize_security_policies_is_idempotent():
    bootstrap = SecurityBootstrap()
    bootstrap.register()

    first = bootstrap.initialize_security_policies()
    second = bootstrap.initialize_security_policies()

    assert first == DEFAULT_POLICY_NAMES
    assert second == DEFAULT_POLICY_NAMES


def test_register_api_confirms_security_prefix():
    bootstrap = SecurityBootstrap()

    assert bootstrap.register_api() is True


def test_bootstrap_security_subsystem_is_valid():
    result = bootstrap_security_subsystem()

    assert result.valid is True
    assert set(result.registered_services) == set(REQUIRED_SERVICES)


def test_bootstrap_security_subsystem_is_idempotent():
    first = bootstrap_security_subsystem()
    second = bootstrap_security_subsystem()

    assert first.valid is True
    assert second.valid is True


def test_get_security_bootstrap_returns_singleton():
    assert get_security_bootstrap() is get_security_bootstrap()


def test_subsystem_name_is_stable():
    assert SUBSYSTEM_NAME == "security_authentication_access_control"


def test_end_to_end_authentication_workflow():
    result = bootstrap_security_subsystem()
    assert result.valid is True

    services = get_security_bootstrap().registered_services()
    authentication_manager = services["authentication_manager"]
    jwt_service = services["jwt_service"]
    rbac = services["rbac"]
    permission_engine = services["permission_engine"]
    session_manager = services["session_manager"]
    audit_log_service = services["audit_log_service"]
    dashboard_api = services["dashboard_api"]
    export_service = services["export_service"]

    credential = authentication_manager.register("bootstrap-e2e-user", "hunter2-strong")
    login = authentication_manager.login("bootstrap-e2e-user", "hunter2-strong")
    assert login.success is True

    rbac.assign_role(credential.user_id, "Developer")
    assert permission_engine.check(credential.user_id, "notebooks", "Read") is True

    session = session_manager.create(credential.user_id, roles=["Developer"])
    assert session_manager.validate(session.metadata.session_id).session_id == session.metadata.session_id

    claims = jwt_service.validate(session.access_token)
    assert claims.subject == credential.user_id

    audit_log_service.record(
        "Authentication", credential.user_id, "login", "attempt", outcome="success"
    )

    overview = dashboard_api.overview()
    assert overview["authentication"]["total_events"] >= 1

    export_result = export_service.export_dashboard()
    assert export_result.record_count == 1

    session_manager.terminate(session.metadata.session_id)


@pytest.fixture
def client() -> TestClient:
    from backend.security.authentication import router as authentication_router
    from backend.security.api_keys import router as api_keys_router
    from backend.security.jwt_service import router as jwt_router
    from backend.security.rbac import router as rbac_router
    from backend.security.permissions import router as permissions_router
    from backend.security.session_manager import router as session_router
    from backend.security.audit_logs import router as audit_router
    from backend.security.security_policy import router as security_policy_router
    from backend.security.secrets import router as secrets_router
    from backend.security.security_analytics import router as security_analytics_router
    from backend.security.dashboard import router as dashboard_router
    from backend.security.export_service import router as export_router

    app = FastAPI()
    for router in (
        authentication_router,
        api_keys_router,
        jwt_router,
        rbac_router,
        permissions_router,
        session_router,
        audit_router,
        security_policy_router,
        secrets_router,
        security_analytics_router,
        dashboard_router,
        export_router,
    ):
        app.include_router(router)
    return TestClient(app)


def test_route_registration_covers_every_service_area(client: TestClient):
    bootstrap_security_subsystem()

    assert client.get("/security/dashboard").status_code == 200
    assert client.get("/security/analytics/summary").status_code == 200
    assert client.get("/security/roles/Admin/permissions").status_code == 200
