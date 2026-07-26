import pytest

from backend.governance import runtime
from backend.governance.deployment_orchestration_bootstrap import (
    REQUIRED_SERVICES,
    SUBSYSTEM_NAME,
    DeploymentOrchestrationBootstrap,
    DeploymentOrchestrationBootstrapError,
    UnknownServiceError,
    bootstrap_orchestration_subsystem,
)


@pytest.fixture(autouse=True)
def _reset_runtime():
    runtime.reset()
    yield
    runtime.reset()


def test_register_wires_every_required_service():
    bootstrap = DeploymentOrchestrationBootstrap()

    services = bootstrap.register()

    assert set(services) == set(REQUIRED_SERVICES)
    assert all(value is not None for value in services.values())


def test_registered_services_reflects_last_register_call():
    bootstrap = DeploymentOrchestrationBootstrap()

    assert bootstrap.registered_services() == {}

    bootstrap.register()

    assert set(bootstrap.registered_services()) == set(REQUIRED_SERVICES)


def test_discover_returns_named_service():
    bootstrap = DeploymentOrchestrationBootstrap()
    bootstrap.register()

    scheduler = bootstrap.discover("scheduler")

    assert scheduler is bootstrap.registered_services()["scheduler"]


def test_discover_unknown_service_raises():
    bootstrap = DeploymentOrchestrationBootstrap()
    bootstrap.register()

    with pytest.raises(UnknownServiceError):
        bootstrap.discover("does-not-exist")


def test_validate_registers_automatically_if_not_yet_registered():
    bootstrap = DeploymentOrchestrationBootstrap()

    result = bootstrap.validate()

    assert result.valid is True
    assert set(result.registered_services) == set(REQUIRED_SERVICES)
    assert result.missing_services == ()


def test_validate_raises_when_a_required_service_is_missing():
    bootstrap = DeploymentOrchestrationBootstrap()
    with bootstrap._lock:
        bootstrap._services = {
            name: object() for name in REQUIRED_SERVICES if name != "scheduler"
        }

    with pytest.raises(DeploymentOrchestrationBootstrapError) as exc_info:
        bootstrap.validate()

    assert exc_info.value.result.missing_services == ("scheduler",)
    assert exc_info.value.result.valid is False


def test_health_check_delegates_to_the_orchestration_dashboard():
    bootstrap = DeploymentOrchestrationBootstrap()
    bootstrap.register()

    report = bootstrap.health_check()

    assert report["status"] == "ok"
    assert "pipelines" in report


def test_health_check_raises_when_dashboard_not_registered():
    bootstrap = DeploymentOrchestrationBootstrap()

    with pytest.raises(DeploymentOrchestrationBootstrapError):
        bootstrap.health_check()


def test_bootstrap_orchestration_subsystem_registers_with_runtime():
    result = bootstrap_orchestration_subsystem()

    assert result.valid is True
    assert SUBSYSTEM_NAME in runtime.registered_subsystems()
    assert SUBSYSTEM_NAME in runtime.registered_health_checks()


def test_runtime_startup_validation_runs_the_bootstrap():
    bootstrap_orchestration_subsystem()

    results = runtime.run_startup_validation()

    assert results[SUBSYSTEM_NAME].valid is True


def test_runtime_health_checks_run_the_bootstrap_health_check():
    bootstrap_orchestration_subsystem()

    reports = runtime.run_health_checks()

    assert reports[SUBSYSTEM_NAME]["status"] == "ok"


def test_runtime_run_subsystem_validation_targets_one_subsystem():
    bootstrap_orchestration_subsystem()

    result = runtime.run_subsystem_validation(SUBSYSTEM_NAME)

    assert result.valid is True


def test_runtime_run_subsystem_validation_unknown_raises():
    with pytest.raises(KeyError):
        runtime.run_subsystem_validation("does-not-exist")


def test_runtime_run_health_check_targets_one_subsystem():
    bootstrap_orchestration_subsystem()

    report = runtime.run_health_check(SUBSYSTEM_NAME)

    assert report["status"] == "ok"


def test_runtime_run_health_check_unknown_raises():
    with pytest.raises(KeyError):
        runtime.run_health_check("does-not-exist")


def test_bootstrap_orchestration_subsystem_is_idempotent():
    first = bootstrap_orchestration_subsystem()
    second = bootstrap_orchestration_subsystem()

    assert first.valid is True
    assert second.valid is True
    assert runtime.registered_subsystems().count(SUBSYSTEM_NAME) == 1
