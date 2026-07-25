import pytest

from backend.governance import runtime
from backend.governance.deployment_observability_bootstrap import (
    REQUIRED_SERVICES,
    SUBSYSTEM_NAME,
    DeploymentObservabilityBootstrap,
    DeploymentObservabilityBootstrapError,
    bootstrap_observability_subsystem,
)


@pytest.fixture(autouse=True)
def _reset_runtime():
    runtime.reset()
    yield
    runtime.reset()


def test_register_wires_every_required_service():
    bootstrap = DeploymentObservabilityBootstrap()

    services = bootstrap.register()

    assert set(services) == set(REQUIRED_SERVICES)
    assert all(value is not None for value in services.values())


def test_registered_services_reflects_last_register_call():
    bootstrap = DeploymentObservabilityBootstrap()

    assert bootstrap.registered_services() == {}

    bootstrap.register()

    assert set(bootstrap.registered_services()) == set(REQUIRED_SERVICES)


def test_validate_registers_automatically_if_not_yet_registered():
    bootstrap = DeploymentObservabilityBootstrap()

    result = bootstrap.validate()

    assert result.valid is True
    assert set(result.registered_services) == set(REQUIRED_SERVICES)
    assert result.missing_services == ()


def test_validate_raises_when_a_required_service_is_missing():
    bootstrap = DeploymentObservabilityBootstrap()
    with bootstrap._lock:
        bootstrap._services = {
            name: object() for name in REQUIRED_SERVICES if name != "slo_manager"
        }

    with pytest.raises(DeploymentObservabilityBootstrapError) as exc_info:
        bootstrap.validate()

    assert exc_info.value.result.missing_services == ("slo_manager",)
    assert exc_info.value.result.valid is False


def test_health_check_delegates_to_the_observability_dashboard():
    bootstrap = DeploymentObservabilityBootstrap()
    bootstrap.register()

    report = bootstrap.health_check()

    assert "status" in report


def test_health_check_raises_when_dashboard_not_registered():
    bootstrap = DeploymentObservabilityBootstrap()

    with pytest.raises(DeploymentObservabilityBootstrapError):
        bootstrap.health_check()


def test_bootstrap_observability_subsystem_registers_with_runtime():
    result = bootstrap_observability_subsystem()

    assert result.valid is True
    assert SUBSYSTEM_NAME in runtime.registered_subsystems()
    assert SUBSYSTEM_NAME in runtime.registered_health_checks()


def test_runtime_startup_validation_runs_the_bootstrap():
    bootstrap_observability_subsystem()

    results = runtime.run_startup_validation()

    assert results[SUBSYSTEM_NAME].valid is True


def test_runtime_health_checks_run_the_bootstrap_health_check():
    bootstrap_observability_subsystem()

    reports = runtime.run_health_checks()

    assert "status" in reports[SUBSYSTEM_NAME]


def test_bootstrap_observability_subsystem_is_idempotent():
    first = bootstrap_observability_subsystem()
    second = bootstrap_observability_subsystem()

    assert first.valid is True
    assert second.valid is True
    assert runtime.registered_subsystems().count(SUBSYSTEM_NAME) == 1
