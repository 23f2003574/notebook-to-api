import pytest

from backend.governance import runtime
from backend.governance.artifact_registry import ArtifactMetadata
from backend.governance.artifact_release_bootstrap import (
    DEFAULT_CHANNELS,
    DEFAULT_POLICY_NAMES,
    REQUIRED_SERVICES,
    SUBSYSTEM_NAME,
    ArtifactReleaseBootstrap,
    ArtifactReleaseBootstrapError,
    UnknownServiceError,
    bootstrap_artifact_release_subsystem,
    get_artifact_release_bootstrap,
)


@pytest.fixture(autouse=True)
def _reset_runtime():
    runtime.reset()
    yield
    runtime.reset()


def test_register_wires_every_required_service():
    bootstrap = ArtifactReleaseBootstrap()

    services = bootstrap.register()

    assert set(services) == set(REQUIRED_SERVICES)
    assert all(value is not None for value in services.values())


def test_registered_services_reflects_last_register_call():
    bootstrap = ArtifactReleaseBootstrap()

    assert bootstrap.registered_services() == {}

    bootstrap.register()

    assert set(bootstrap.registered_services()) == set(REQUIRED_SERVICES)


def test_discover_returns_named_service():
    bootstrap = ArtifactReleaseBootstrap()
    bootstrap.register()

    release_manager = bootstrap.discover("release_manager")

    assert release_manager is bootstrap.registered_services()["release_manager"]


def test_discover_unknown_service_raises():
    bootstrap = ArtifactReleaseBootstrap()
    bootstrap.register()

    with pytest.raises(UnknownServiceError):
        bootstrap.discover("does-not-exist")


def test_validate_registers_automatically_if_not_yet_registered():
    bootstrap = ArtifactReleaseBootstrap()

    result = bootstrap.validate()

    assert result.valid is True
    assert set(result.registered_services) == set(REQUIRED_SERVICES)
    assert result.missing_services == ()


def test_validate_raises_when_a_required_service_is_missing():
    bootstrap = ArtifactReleaseBootstrap()
    with bootstrap._lock:
        bootstrap._services = {
            name: object() for name in REQUIRED_SERVICES if name != "dashboard_api"
        }

    with pytest.raises(ArtifactReleaseBootstrapError) as exc_info:
        bootstrap.validate()

    assert exc_info.value.result.missing_services == ("dashboard_api",)
    assert exc_info.value.result.valid is False


def test_health_check_delegates_to_the_dashboard():
    bootstrap = ArtifactReleaseBootstrap()
    bootstrap.register()

    report = bootstrap.health_check()

    assert report["status"] == "ok"
    assert "releases" in report


def test_health_check_raises_when_dashboard_not_registered():
    bootstrap = ArtifactReleaseBootstrap()

    with pytest.raises(ArtifactReleaseBootstrapError):
        bootstrap.health_check()


def test_initialize_default_channels_creates_all_tiers():
    bootstrap = ArtifactReleaseBootstrap()
    bootstrap.register()

    created = bootstrap.initialize_default_channels()

    assert created == tuple(name for name, _, _ in DEFAULT_CHANNELS)
    channel_manager = bootstrap.discover("channel_manager")
    names = {channel.name for channel in channel_manager.list()}
    assert names == set(created)


def test_initialize_default_channels_is_idempotent():
    bootstrap = ArtifactReleaseBootstrap()
    bootstrap.register()

    bootstrap.initialize_default_channels()
    bootstrap.initialize_default_channels()

    channel_manager = bootstrap.discover("channel_manager")
    assert len(channel_manager.list()) == len(DEFAULT_CHANNELS)


def test_initialize_default_policies_is_idempotent():
    bootstrap = ArtifactReleaseBootstrap()
    bootstrap.register()

    first = bootstrap.initialize_default_policies()
    second = bootstrap.initialize_default_policies()

    assert first == DEFAULT_POLICY_NAMES
    assert second == DEFAULT_POLICY_NAMES


def test_default_retention_config_exposes_max_versions():
    bootstrap = ArtifactReleaseBootstrap()

    config = bootstrap.default_retention_config()

    assert config["max_versions"] > 0


def test_ensure_retention_policy_is_idempotent():
    bootstrap = ArtifactReleaseBootstrap()
    bootstrap.register()

    bootstrap.ensure_retention_policy("svc-boot-retention")
    bootstrap.ensure_retention_policy("svc-boot-retention")

    retention_manager = bootstrap.discover("retention_manager")
    names = [policy.name for policy in retention_manager.policies()]
    assert names.count("svc-boot-retention") == 1


def test_register_api_confirms_governance_prefix():
    bootstrap = ArtifactReleaseBootstrap()

    assert bootstrap.register_api() is True


def test_bootstrap_artifact_release_subsystem_registers_with_runtime():
    result = bootstrap_artifact_release_subsystem()

    assert result.valid is True
    assert SUBSYSTEM_NAME in runtime.registered_subsystems()
    assert SUBSYSTEM_NAME in runtime.registered_health_checks()


def test_bootstrap_artifact_release_subsystem_is_idempotent():
    first = bootstrap_artifact_release_subsystem()
    second = bootstrap_artifact_release_subsystem()

    assert first.valid is True
    assert second.valid is True
    assert runtime.registered_subsystems().count(SUBSYSTEM_NAME) == 1


def test_runtime_startup_validation_runs_the_bootstrap():
    bootstrap_artifact_release_subsystem()

    results = runtime.run_startup_validation()

    assert results[SUBSYSTEM_NAME].valid is True


def test_runtime_health_checks_run_the_bootstrap_health_check():
    bootstrap_artifact_release_subsystem()

    reports = runtime.run_health_checks()

    assert reports[SUBSYSTEM_NAME]["status"] == "ok"


def test_runtime_run_subsystem_validation_targets_one_subsystem():
    bootstrap_artifact_release_subsystem()

    result = runtime.run_subsystem_validation(SUBSYSTEM_NAME)

    assert result.valid is True


def test_runtime_run_health_check_targets_one_subsystem():
    bootstrap_artifact_release_subsystem()

    report = runtime.run_health_check(SUBSYSTEM_NAME)

    assert report["status"] == "ok"


def test_end_to_end_artifact_to_release_workflow():
    result = bootstrap_artifact_release_subsystem()
    assert result.valid is True

    services = get_artifact_release_bootstrap().registered_services()
    registry = services["artifact_registry"]
    version_manager = services["version_manager"]
    promotion_engine = services["promotion_engine"]
    release_manager = services["release_manager"]
    notes_generator = services["notes_generator"]
    channel_manager = services["channel_manager"]
    policy_engine = services["policy_engine"]
    verification_engine = services["verification_engine"]
    analytics_service = services["analytics_service"]
    dashboard_api = services["dashboard_api"]

    name, version = "svc-bootstrap-e2e", "1.0.0"
    registry.publish(
        name,
        version,
        location=f"loc-{name}-{version}",
        metadata=ArtifactMetadata(
            content_type="application/octet-stream",
            size_bytes=1024,
            checksum="a" * 64,
            checksum_algorithm="sha256",
        ),
    )
    version_manager.create(name, version)
    promotion_engine.promote(name, version, "Staging")
    promotion_engine.promote(name, version, "Production")

    release = release_manager.create(
        "release-bootstrap-e2e", [{"name": name, "version": version}]
    )
    notes_generator.generate(release.release_id, commits=["feat: bootstrap end to end"])
    channel_manager.assign(release.release_id)  # uses the default "alpha" channel
    policy_engine.evaluate(
        release.release_id,
        overrides={
            "Approval Required": True,
            "Release Notes Present": True,
            "Channel Assigned": True,
        },
    )
    report = verification_engine.verify(release.release_id)
    analytics_service.record_release(release.release_id)
    analytics_service.record_verification(release.release_id, report.passed)

    assert report.passed is True

    published = release_manager.publish(release.release_id)
    assert published.state == "PUBLISHED"

    overview = dashboard_api.overview()
    assert overview["releases"]["total"] >= 1
