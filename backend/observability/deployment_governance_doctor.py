from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from enum import IntEnum
from typing import Protocol, TextIO

from .deployment_governance_audit_retention import (
    governance_integrity_audit_automatic_retention_config_from_env,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)
from .deployment_governance_persistence_diagnostics import (
    GovernancePersistenceDiagnosticsSnapshot,
)


class GovernanceDoctorExitCode(
    IntEnum
):
    """
    Process exit codes produced by the governance persistence doctor.
    """

    HEALTHY = 0

    UNHEALTHY = 1

    DIAGNOSTICS_FAILED = 2


class GovernanceDiagnosticsService(
    Protocol
):
    """
    The subset of DeploymentGovernancePersistenceDiagnosticsService the
    doctor depends on.
    """

    def capture(
        self,
        *,
        include_integrity_audit: bool = False,
        integrity_audit_batch_size: int = 500,
    ) -> GovernancePersistenceDiagnosticsSnapshot:
        ...


class GovernanceDoctorRuntime(
    Protocol
):
    """
    The subset of DeploymentGovernancePersistenceRuntime the doctor depends
    on. Defined as a narrow protocol (rather than importing the concrete
    runtime type) so tests can supply a lightweight fake instead of a real
    persistence runtime.
    """

    def build_diagnostics_service(
        self,
    ) -> GovernanceDiagnosticsService:
        ...


@dataclass(frozen=True)
class GovernanceDoctorOptions:
    """
    Options controlling governance persistence diagnostics execution.
    """

    deep: bool = False

    json_output: bool = False

    integrity_audit_batch_size: int = 500

    def __post_init__(
        self,
    ) -> None:
        if (
            self.integrity_audit_batch_size
            <= 0
        ):
            raise ValueError(
                "integrity_audit_batch_size "
                "must be greater than zero"
            )


@dataclass(frozen=True)
class GovernanceDoctorResult:
    """
    Result of one governance persistence doctor execution.
    """

    exit_code: GovernanceDoctorExitCode

    snapshot: (
        GovernancePersistenceDiagnosticsSnapshot
        | None
    )

    error: str | None = None

    @property
    def succeeded(
        self,
    ) -> bool:
        return (
            self.exit_code
            is not GovernanceDoctorExitCode
            .DIAGNOSTICS_FAILED
        )


class DeploymentGovernanceDoctor:
    """
    Executes and renders deployment governance persistence diagnostics.

    The doctor consumes an already-built persistence runtime; it does not
    decide backend selection, database paths, or environment configuration.
    That composition remains the responsibility of
    build_deployment_governance_persistence() /
    deployment_governance_persistence_config_from_env().
    """

    def __init__(
        self,
        runtime: GovernanceDoctorRuntime,
    ) -> None:
        self._runtime = runtime

    def run(
        self,
        options: GovernanceDoctorOptions,
    ) -> GovernanceDoctorResult:
        """
        Execute governance persistence diagnostics.
        """

        try:
            snapshot = (
                self._runtime
                .build_diagnostics_service()
                .capture(
                    include_integrity_audit=(
                        options.deep
                    ),
                    integrity_audit_batch_size=(
                        options
                        .integrity_audit_batch_size
                    ),
                )
            )

        except Exception as exc:
            return GovernanceDoctorResult(
                exit_code=(
                    GovernanceDoctorExitCode
                    .DIAGNOSTICS_FAILED
                ),
                snapshot=None,
                error=str(
                    exc
                ),
            )

        exit_code = (
            GovernanceDoctorExitCode.HEALTHY
            if snapshot.operationally_healthy
            else GovernanceDoctorExitCode.UNHEALTHY
        )

        return GovernanceDoctorResult(
            exit_code=exit_code,
            snapshot=snapshot,
            error=None,
        )

    def execute(
        self,
        options: GovernanceDoctorOptions,
        *,
        stdout: TextIO = sys.stdout,
        stderr: TextIO = sys.stderr,
    ) -> GovernanceDoctorExitCode:
        """
        Execute diagnostics and render the result to output streams.
        """

        result = self.run(
            options
        )

        if not result.succeeded:
            self._render_failure(
                result,
                json_output=(
                    options.json_output
                ),
                stderr=stderr,
            )

            return result.exit_code

        assert result.snapshot is not None

        if options.json_output:
            self._render_json(
                result.snapshot,
                stdout=stdout,
            )

        else:
            self._render_human(
                result.snapshot,
                deep=options.deep,
                stdout=stdout,
            )

        return result.exit_code

    @staticmethod
    def _render_json(
        snapshot: GovernancePersistenceDiagnosticsSnapshot,
        *,
        stdout: TextIO,
    ) -> None:
        """
        Render machine-readable diagnostics.

        Only JSON is written to stdout so `... | jq` style piping stays
        valid; there is no leading/trailing human text in --json mode.
        """

        json.dump(
            snapshot.to_dict(),
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write(
            "\n"
        )

    @staticmethod
    def _render_failure(
        result: GovernanceDoctorResult,
        *,
        json_output: bool,
        stderr: TextIO,
    ) -> None:
        """
        Render a diagnostics execution failure.
        """

        message = (
            result.error
            or
            "governance persistence diagnostics failed"
        )

        if json_output:
            json.dump(
                {
                    "status": (
                        "diagnostics_failed"
                    ),
                    "error": message,
                    "exit_code": int(
                        result.exit_code
                    ),
                },
                stderr,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )

            stderr.write(
                "\n"
            )

            return

        stderr.write(
            "Governance persistence diagnostics failed.\n"
        )

        stderr.write(
            f"Reason: {message}\n"
        )

    @classmethod
    def _render_human(
        cls,
        snapshot: GovernancePersistenceDiagnosticsSnapshot,
        *,
        deep: bool,
        stdout: TextIO,
    ) -> None:
        """
        Render human-readable governance persistence diagnostics.
        """

        cls._write_heading(
            stdout,
            "Deployment Governance Persistence Doctor",
        )

        cls._write_field(
            stdout,
            "Status",
            (
                "HEALTHY"
                if snapshot.operationally_healthy
                else "UNHEALTHY"
            ),
        )

        cls._write_field(
            stdout,
            "Captured at",
            snapshot.captured_at.isoformat(),
        )

        stdout.write(
            "\n"
        )

        cls._write_section(
            stdout,
            "Persistence",
        )

        cls._write_field(
            stdout,
            "Backend",
            snapshot.backend.value,
        )

        cls._write_field(
            stdout,
            "Durable",
            cls._yes_no(
                snapshot.durable
            ),
        )

        cls._write_field(
            stdout,
            "Database path",
            (
                "not applicable"
                if snapshot.database_path is None
                else str(
                    snapshot.database_path
                )
            ),
        )

        stdout.write(
            "\n"
        )

        cls._write_section(
            stdout,
            "Repository",
        )

        cls._write_field(
            stdout,
            "Total records",
            str(
                snapshot.repository.total_records
            ),
        )

        statistics = dict(
            snapshot.repository.statistics
        )

        if statistics:
            for key in sorted(
                statistics
            ):
                cls._write_field(
                    stdout,
                    cls._humanize_key(
                        key
                    ),
                    cls._format_value(
                        statistics[
                            key
                        ]
                    ),
                )

        stdout.write(
            "\n"
        )

        cls._write_section(
            stdout,
            "Schema",
        )

        if snapshot.schema is None:
            cls._write_field(
                stdout,
                "Available",
                "no",
            )

        else:
            cls._write_field(
                stdout,
                "Current version",
                str(
                    snapshot.schema.current_version
                ),
            )

            cls._write_field(
                stdout,
                "Applied migrations",
                (
                    ", ".join(
                        str(
                            version
                        )
                        for version
                        in snapshot
                        .schema
                        .applied_versions
                    )
                    or "none"
                ),
            )

            cls._write_field(
                stdout,
                "Migration count",
                str(
                    snapshot.schema.migration_count
                ),
            )

        stdout.write(
            "\n"
        )

        cls._write_section(
            stdout,
            "Integrity",
        )

        cls._write_field(
            stdout,
            "Audit supported",
            cls._yes_no(
                snapshot.integrity.supported
            ),
        )

        cls._write_field(
            stdout,
            "Audit executed",
            cls._yes_no(
                snapshot.integrity.executed
            ),
        )

        if snapshot.integrity.executed:
            cls._write_field(
                stdout,
                "Integrity status",
                (
                    "HEALTHY"
                    if snapshot.integrity.healthy
                    else "UNHEALTHY"
                ),
            )

            cls._write_field(
                stdout,
                "Records scanned",
                cls._format_optional_int(
                    snapshot.integrity.total_records
                ),
            )

            cls._write_field(
                stdout,
                "Valid records",
                cls._format_optional_int(
                    snapshot.integrity.valid_records
                ),
            )

            cls._write_field(
                stdout,
                "Invalid records",
                cls._format_optional_int(
                    snapshot.integrity.invalid_records
                ),
            )

            if deep:
                cls._write_field(
                    stdout,
                    "Integrity mismatches",
                    cls._format_optional_int(
                        snapshot
                        .integrity
                        .integrity_mismatches
                    ),
                )

                cls._write_field(
                    stdout,
                    "Missing integrity metadata",
                    cls._format_optional_int(
                        snapshot
                        .integrity
                        .missing_integrity_metadata
                    ),
                )

                cls._write_field(
                    stdout,
                    "Invalid integrity metadata",
                    cls._format_optional_int(
                        snapshot
                        .integrity
                        .invalid_integrity_metadata
                    ),
                )

                cls._write_field(
                    stdout,
                    "Invalid persisted records",
                    cls._format_optional_int(
                        snapshot
                        .integrity
                        .invalid_persisted_records
                    ),
                )

        elif snapshot.integrity.supported:
            cls._write_field(
                stdout,
                "Integrity status",
                "not verified",
            )

            cls._write_field(
                stdout,
                "Deep audit",
                (
                    "run with --deep to verify "
                    "all persisted records"
                ),
            )

        else:
            cls._write_field(
                stdout,
                "Integrity status",
                "not applicable",
            )

        stdout.write(
            "\n"
        )

        cls._write_section(
            stdout,
            "Audit History",
        )

        cls._write_field(
            stdout,
            "Recorded audits",
            str(
                snapshot.audit_history.total_audits
            ),
        )

        cls._write_field(
            stdout,
            "Healthy audits",
            str(
                snapshot.audit_history.healthy_audits
            ),
        )

        cls._write_field(
            stdout,
            "Unhealthy audits",
            str(
                snapshot.audit_history.unhealthy_audits
            ),
        )

        if snapshot.audit_history.has_history:
            cls._write_field(
                stdout,
                "Latest audit ID",
                (
                    snapshot
                    .audit_history
                    .latest_audit_id
                    or "not available"
                ),
            )

            cls._write_field(
                stdout,
                "Latest audit status",
                (
                    "HEALTHY"
                    if snapshot
                    .audit_history
                    .latest_audit_healthy
                    else "UNHEALTHY"
                ),
            )

            cls._write_field(
                stdout,
                "Latest invalid records",
                cls._format_optional_int(
                    snapshot
                    .audit_history
                    .latest_audit_invalid_records
                ),
            )

        if snapshot.audit_history.current_audit_recorded:
            cls._write_field(
                stdout,
                "Current audit recorded",
                "yes",
            )

            cls._write_field(
                stdout,
                "Current audit ID",
                (
                    snapshot
                    .audit_history
                    .current_audit_id
                    or "not available"
                ),
            )

    @staticmethod
    def _write_heading(
        stream: TextIO,
        value: str,
    ) -> None:
        stream.write(
            f"{value}\n"
        )

        stream.write(
            f"{'=' * len(value)}\n"
        )

    @staticmethod
    def _write_section(
        stream: TextIO,
        value: str,
    ) -> None:
        stream.write(
            f"{value}\n"
        )

        stream.write(
            f"{'-' * len(value)}\n"
        )

    @staticmethod
    def _write_field(
        stream: TextIO,
        label: str,
        value: str,
    ) -> None:
        stream.write(
            f"{label}: {value}\n"
        )

    @staticmethod
    def _yes_no(
        value: bool,
    ) -> str:
        return (
            "yes"
            if value
            else "no"
        )

    @staticmethod
    def _format_optional_int(
        value: int | None,
    ) -> str:
        return (
            "not available"
            if value is None
            else str(
                value
            )
        )

    @staticmethod
    def _humanize_key(
        value: str,
    ) -> str:
        return (
            value
            .replace(
                "_",
                " ",
            )
            .strip()
            .capitalize()
        )

    @staticmethod
    def _format_value(
        value: object,
    ) -> str:
        if value is None:
            return "none"

        if isinstance(
            value,
            bool,
        ):
            return (
                "yes"
                if value
                else "no"
            )

        return str(
            value
        )


def run_deployment_governance_doctor(
    *,
    deep: bool = False,
    json_output: bool = False,
    integrity_audit_batch_size: int = 500,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap governance persistence and execute the doctor command.

    This is the composition boundary: it reads environment configuration,
    builds the persistence runtime, and hands it to the doctor. The doctor
    itself never decides backend selection or database paths.
    """

    try:
        config = (
            deployment_governance_persistence_config_from_env()
        )

        runtime = (
            build_deployment_governance_persistence(
                config,
                automatic_audit_retention=(
                    governance_integrity_audit_automatic_retention_config_from_env()
                ),
            )
        )

        options = GovernanceDoctorOptions(
            deep=deep,
            json_output=json_output,
            integrity_audit_batch_size=(
                integrity_audit_batch_size
            ),
        )

    except Exception as exc:
        if json_output:
            json.dump(
                {
                    "status": "diagnostics_failed",
                    "error": str(
                        exc
                    ),
                    "exit_code": int(
                        GovernanceDoctorExitCode
                        .DIAGNOSTICS_FAILED
                    ),
                },
                stderr,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )

            stderr.write(
                "\n"
            )

        else:
            stderr.write(
                "Governance persistence diagnostics "
                "could not be initialized.\n"
            )

            stderr.write(
                f"Reason: {exc}\n"
            )

        return int(
            GovernanceDoctorExitCode
            .DIAGNOSTICS_FAILED
        )

    doctor = DeploymentGovernanceDoctor(
        runtime
    )

    return int(
        doctor.execute(
            options,
            stdout=stdout,
            stderr=stderr,
        )
    )
