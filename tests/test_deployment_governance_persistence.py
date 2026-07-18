from __future__ import annotations

from pathlib import Path

import pytest

from backend.observability.deployment_governance_persistence import (
    DeploymentGovernancePersistenceBackend,
    DeploymentGovernancePersistenceConfig,
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)
from backend.observability.in_memory_deployment_governance_trace_repository import (
    InMemoryDeploymentGovernanceTraceRepository,
)
from backend.observability.sqlite_deployment_governance_trace_repository import (
    SQLiteDeploymentGovernanceTraceRepository,
)
from backend.observability.deployment_governance_audit_history import (
    InMemoryGovernanceIntegrityAuditHistoryRepository,
)
from backend.observability.sqlite_deployment_governance_audit_history import (
    SQLiteGovernanceIntegrityAuditHistoryRepository,
)


def test_memory_config_builds_in_memory_runtime() -> None:
    runtime = (
        build_deployment_governance_persistence(
            DeploymentGovernancePersistenceConfig.memory()
        )
    )

    assert (
        runtime.backend
        is DeploymentGovernancePersistenceBackend.MEMORY
    )

    assert runtime.durable is False

    assert isinstance(
        runtime.repository,
        InMemoryDeploymentGovernanceTraceRepository,
    )

    assert runtime.database is None


def test_sqlite_config_builds_durable_runtime(
    tmp_path: Path,
) -> None:
    database_path = (
        tmp_path
        / "governance-runtime.db"
    )

    runtime = (
        build_deployment_governance_persistence(
            DeploymentGovernancePersistenceConfig.sqlite(
                database_path
            )
        )
    )

    assert (
        runtime.backend
        is DeploymentGovernancePersistenceBackend.SQLITE
    )

    assert runtime.durable is True

    assert isinstance(
        runtime.repository,
        SQLiteDeploymentGovernanceTraceRepository,
    )

    assert runtime.database is not None

    assert (
        runtime.database.current_schema_version()
        == 19
    )


def test_default_runtime_uses_memory_backend() -> None:
    runtime = (
        build_deployment_governance_persistence()
    )

    assert (
        runtime.backend
        is DeploymentGovernancePersistenceBackend.MEMORY
    )

    assert runtime.durable is False


def test_runtime_registry_is_functional(
    tmp_path: Path,
) -> None:
    """
    The runtime's registry should be immediately usable, exercising the same
    trace_engine + repository wiring the rest of the governance subsystem
    depends on.
    """

    runtime = (
        build_deployment_governance_persistence(
            DeploymentGovernancePersistenceConfig.sqlite(
                tmp_path / "runtime-registry.db"
            )
        )
    )

    trace = runtime.registry.trace_engine.create(
        "deployment-bootstrap",
        "payments-api",
        "production",
        "sha256:bootstrap",
    )

    runtime.registry.register(trace)

    restored = runtime.registry.get_by_deployment_id(
        "deployment-bootstrap"
    )

    assert restored is not None
    assert restored.trace_id == trace.trace_id


@pytest.mark.parametrize(
    (
        "raw_value",
        "expected",
    ),
    (
        (
            "memory",
            DeploymentGovernancePersistenceBackend.MEMORY,
        ),
        (
            "MEMORY",
            DeploymentGovernancePersistenceBackend.MEMORY,
        ),
        (
            " sqlite ",
            DeploymentGovernancePersistenceBackend.SQLITE,
        ),
    ),
)
def test_backend_parser_normalizes_values(
    raw_value: str,
    expected: DeploymentGovernancePersistenceBackend,
) -> None:
    assert (
        DeploymentGovernancePersistenceBackend.parse(
            raw_value
        )
        is expected
    )


def test_backend_parser_rejects_unknown_backend() -> None:
    with pytest.raises(
        ValueError,
        match="unsupported deployment governance",
    ):
        DeploymentGovernancePersistenceBackend.parse(
            "mongodb"
        )


def test_environment_config_defaults_to_memory() -> None:
    config = (
        deployment_governance_persistence_config_from_env(
            environ={}
        )
    )

    assert (
        config.backend
        is DeploymentGovernancePersistenceBackend.MEMORY
    )


def test_environment_config_builds_sqlite_settings(
    tmp_path: Path,
) -> None:
    database_path = (
        tmp_path
        / "configured.db"
    )

    config = (
        deployment_governance_persistence_config_from_env(
            environ={
                "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND": (
                    "sqlite"
                ),
                "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH": str(
                    database_path
                ),
                "NOTEBOOK2API_GOVERNANCE_SQLITE_TIMEOUT_SECONDS": (
                    "15.5"
                ),
                "NOTEBOOK2API_GOVERNANCE_SQLITE_WAL": (
                    "false"
                ),
                "NOTEBOOK2API_GOVERNANCE_SQLITE_FOREIGN_KEYS": (
                    "true"
                ),
            }
        )
    )

    assert (
        config.backend
        is DeploymentGovernancePersistenceBackend.SQLITE
    )

    assert (
        config.database_path
        == database_path
    )

    assert (
        config.sqlite_timeout_seconds
        == 15.5
    )

    assert (
        config.sqlite_enable_wal
        is False
    )

    assert (
        config.sqlite_enforce_foreign_keys
        is True
    )


def test_invalid_boolean_environment_value_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match=(
            "NOTEBOOK2API_GOVERNANCE_SQLITE_WAL"
        ),
    ):
        deployment_governance_persistence_config_from_env(
            environ={
                "NOTEBOOK2API_GOVERNANCE_SQLITE_WAL": (
                    "sometimes"
                ),
            }
        )


def test_memory_runtime_does_not_support_integrity_audit() -> None:
    runtime = (
        build_deployment_governance_persistence(
            DeploymentGovernancePersistenceConfig.memory()
        )
    )

    assert (
        runtime.supports_integrity_audit
        is False
    )

    with pytest.raises(
        RuntimeError,
        match="does not support integrity auditing",
    ):
        runtime.build_integrity_audit_service()


def test_sqlite_runtime_supports_integrity_audit(
    tmp_path: Path,
) -> None:
    runtime = (
        build_deployment_governance_persistence(
            DeploymentGovernancePersistenceConfig.sqlite(
                tmp_path
                / "audit-runtime.db"
            )
        )
    )

    assert (
        runtime.supports_integrity_audit
        is True
    )

    service = (
        runtime.build_integrity_audit_service()
    )

    report = service.audit()

    assert report.healthy is True
    assert report.total_records == 0


def test_memory_runtime_builds_in_memory_audit_history() -> None:
    runtime = (
        build_deployment_governance_persistence(
            DeploymentGovernancePersistenceConfig.memory()
        )
    )

    assert isinstance(
        runtime.audit_history_repository,
        InMemoryGovernanceIntegrityAuditHistoryRepository,
    )

    # The in-memory trace repository does not implement
    # DeploymentGovernanceTraceIntegrityAuditSource (see
    # test_memory_runtime_does_not_support_integrity_audit above), so the
    # recording service inherits the same "unsupported" failure rather than
    # silently producing an empty audit.
    with pytest.raises(
        RuntimeError,
        match="does not support integrity auditing",
    ):
        runtime.build_integrity_audit_recording_service().audit_and_record()

    assert (
        runtime.audit_history_repository.count()
        == 0
    )


def test_sqlite_runtime_records_audit_history_durably(
    tmp_path: Path,
) -> None:
    database_path = (
        tmp_path
        / "runtime-audit-recording.db"
    )

    runtime = (
        build_deployment_governance_persistence(
            DeploymentGovernancePersistenceConfig.sqlite(
                database_path
            )
        )
    )

    assert isinstance(
        runtime.audit_history_repository,
        SQLiteGovernanceIntegrityAuditHistoryRepository,
    )

    result = (
        runtime
        .build_integrity_audit_recording_service()
        .audit_and_record()
    )

    assert result.healthy is True

    assert (
        runtime.audit_history_repository.count()
        == 1
    )

    second_runtime = (
        build_deployment_governance_persistence(
            DeploymentGovernancePersistenceConfig.sqlite(
                database_path
            )
        )
    )

    latest = (
        second_runtime
        .audit_history_repository
        .latest()
    )

    assert latest is not None

    assert (
        latest.audit_id
        == result.audit_id
    )
