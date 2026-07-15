from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol
from uuid import uuid4

from .deployment_governance_audit_history import (
    GovernanceIntegrityAuditHistoryRepository,
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
)
from .deployment_governance_integrity_audit import (
    GovernanceTraceIntegrityAuditReport,
)


class GovernanceIntegrityAuditExecutor(Protocol):
    """
    Minimal execution contract required by the audit recording service.
    """

    def audit(
        self,
        *,
        batch_size: int = 500,
    ) -> GovernanceTraceIntegrityAuditReport:
        """
        Execute a complete governance trace integrity audit.
        """


GovernanceIntegrityAuditIdFactory = Callable[[], str]


def generate_governance_integrity_audit_id() -> str:
    """
    Generate a unique identifier for one historical integrity audit.
    """

    return str(uuid4())


@dataclass(frozen=True)
class GovernanceIntegrityAuditRecordingResult:
    """
    Result of executing and durably recording one integrity audit.
    """

    report: GovernanceTraceIntegrityAuditReport

    record: GovernanceIntegrityAuditRecord

    @property
    def audit_id(self) -> str:
        return self.record.audit_id

    @property
    def healthy(self) -> bool:
        return self.record.healthy


class GovernanceIntegrityAuditRecordMapper:
    """
    Maps a live integrity audit report into its historical summary record.

    The live report (GovernanceTraceIntegrityAuditReport) carries per-trace
    findings useful during an active investigation. The historical record
    (GovernanceIntegrityAuditRecord) carries only the compact aggregate
    summary suitable for durable long-term storage. This mapper is the one
    authoritative transformation point between the two models.
    """

    def __init__(
        self,
        *,
        backend: str,
    ) -> None:
        normalized_backend = backend.strip()

        if not normalized_backend:
            raise ValueError(
                "backend must not be empty"
            )

        self._backend = normalized_backend

    def from_report(
        self,
        report: GovernanceTraceIntegrityAuditReport,
        *,
        audit_id: str,
    ) -> GovernanceIntegrityAuditRecord:
        """
        Convert one completed audit report into a historical record.
        """

        normalized_audit_id = audit_id.strip()

        if not normalized_audit_id:
            raise ValueError(
                "audit_id must not be empty"
            )

        return GovernanceIntegrityAuditRecord(
            audit_id=normalized_audit_id,
            backend=self._backend,
            started_at=report.started_at,
            completed_at=report.completed_at,
            outcome=(
                GovernanceIntegrityAuditOutcome.HEALTHY
                if report.healthy
                else GovernanceIntegrityAuditOutcome.UNHEALTHY
            ),
            total_records=report.total_records,
            valid_records=report.valid_records,
            invalid_records=report.invalid_records,
            integrity_mismatches=report.integrity_mismatches,
            missing_integrity_metadata=report.missing_integrity_metadata,
            invalid_integrity_metadata=report.invalid_integrity_metadata,
            invalid_persisted_records=report.invalid_persisted_records,
        )


class GovernanceIntegrityAuditRecordingService:
    """
    Executes governance integrity audits and records completed audit history.

    The underlying audit executor remains responsible only for verification.
    The history repository remains responsible only for persistence. This
    service coordinates the two without coupling either one directly to the
    other.
    """

    def __init__(
        self,
        *,
        audit_executor: GovernanceIntegrityAuditExecutor,
        history_repository: GovernanceIntegrityAuditHistoryRepository,
        record_mapper: GovernanceIntegrityAuditRecordMapper,
        audit_id_factory: GovernanceIntegrityAuditIdFactory = (
            generate_governance_integrity_audit_id
        ),
    ) -> None:
        self._audit_executor = audit_executor
        self._history_repository = history_repository
        self._record_mapper = record_mapper
        self._audit_id_factory = audit_id_factory

    def audit_and_record(
        self,
        *,
        batch_size: int = 500,
    ) -> GovernanceIntegrityAuditRecordingResult:
        """
        Execute one integrity audit and persist its historical summary.

        The audit ID is generated only after the audit executes
        successfully: an execution that fails before producing a report is
        not a completed historical audit and must not consume an
        identifier or leave a partial trace in the audit history.
        """

        if batch_size <= 0:
            raise ValueError(
                "batch_size must be greater than zero"
            )

        report = self._audit_executor.audit(
            batch_size=batch_size
        )

        audit_id = self._generate_audit_id()

        record = self._record_mapper.from_report(
            report,
            audit_id=audit_id,
        )

        persisted_record = self._history_repository.save(record)

        return GovernanceIntegrityAuditRecordingResult(
            report=report,
            record=persisted_record,
        )

    def _generate_audit_id(self) -> str:
        audit_id = self._audit_id_factory().strip()

        if not audit_id:
            raise ValueError(
                "audit_id_factory returned an empty audit identifier"
            )

        return audit_id
