from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol, Sequence, runtime_checkable

from .deployment_governance_trace_integrity import (
    DeploymentGovernanceTraceIntegrity,
    GovernanceTraceIntegrityError,
    GovernanceTraceIntegrityMetadata,
    GovernanceTraceIntegrityMismatchError,
)
from .deployment_governance_trace_repository import (
    GovernanceTraceRecord,
)


class GovernanceTraceIntegrityAuditStatus(
    str,
    Enum,
):
    """
    Integrity audit status for one persisted governance trace.
    """

    VALID = "valid"

    INTEGRITY_MISMATCH = "integrity_mismatch"

    MISSING_INTEGRITY_METADATA = (
        "missing_integrity_metadata"
    )

    INVALID_INTEGRITY_METADATA = (
        "invalid_integrity_metadata"
    )

    INVALID_PERSISTED_RECORD = (
        "invalid_persisted_record"
    )


@dataclass(frozen=True)
class GovernanceTraceIntegrityAuditCandidate:
    """
    One persisted governance trace candidate exposed for integrity auditing.

    The candidate separates record reconstruction from integrity metadata so
    the audit service can verify records without invoking normal repository
    reads that fail fast on integrity errors.
    """

    trace_id: str

    record: GovernanceTraceRecord | None

    integrity_algorithm: str | None

    integrity_version: int | None

    integrity_digest: str | None

    reconstruction_error: str | None = None

    @property
    def has_complete_integrity_metadata(
        self,
    ) -> bool:
        return all(
            value is not None
            for value in (
                self.integrity_algorithm,
                self.integrity_version,
                self.integrity_digest,
            )
        )


@runtime_checkable
class DeploymentGovernanceTraceIntegrityAuditSource(
    Protocol
):
    """
    Optional repository capability for raw integrity audit inspection.
    """

    def iter_integrity_audit_candidates(
        self,
        *,
        batch_size: int = 500,
    ) -> Sequence[
        GovernanceTraceIntegrityAuditCandidate
    ]:
        """
        Return persisted governance traces in a form suitable for integrity
        auditing without performing normal fail-fast integrity verification.
        """


@dataclass(frozen=True)
class GovernanceTraceIntegrityAuditFinding:
    """
    Integrity audit result for one persisted governance trace.
    """

    trace_id: str

    status: GovernanceTraceIntegrityAuditStatus

    message: str | None = None

    @property
    def valid(
        self,
    ) -> bool:
        return (
            self.status
            is GovernanceTraceIntegrityAuditStatus.VALID
        )


@dataclass(frozen=True)
class GovernanceTraceIntegrityAuditReport:
    """
    Structured report produced by a full governance persistence audit.
    """

    started_at: datetime

    completed_at: datetime

    findings: tuple[
        GovernanceTraceIntegrityAuditFinding,
        ...
    ]

    @property
    def total_records(
        self,
    ) -> int:
        return len(
            self.findings
        )

    @property
    def valid_records(
        self,
    ) -> int:
        return sum(
            1
            for finding in self.findings
            if finding.valid
        )

    @property
    def invalid_records(
        self,
    ) -> int:
        return (
            self.total_records
            - self.valid_records
        )

    @property
    def healthy(
        self,
    ) -> bool:
        return (
            self.invalid_records
            == 0
        )

    @property
    def integrity_mismatches(
        self,
    ) -> int:
        return self._count_status(
            GovernanceTraceIntegrityAuditStatus.INTEGRITY_MISMATCH
        )

    @property
    def missing_integrity_metadata(
        self,
    ) -> int:
        return self._count_status(
            GovernanceTraceIntegrityAuditStatus.MISSING_INTEGRITY_METADATA
        )

    @property
    def invalid_integrity_metadata(
        self,
    ) -> int:
        return self._count_status(
            GovernanceTraceIntegrityAuditStatus.INVALID_INTEGRITY_METADATA
        )

    @property
    def invalid_persisted_records(
        self,
    ) -> int:
        return self._count_status(
            GovernanceTraceIntegrityAuditStatus.INVALID_PERSISTED_RECORD
        )

    def findings_for_status(
        self,
        status: GovernanceTraceIntegrityAuditStatus,
    ) -> tuple[
        GovernanceTraceIntegrityAuditFinding,
        ...
    ]:
        return tuple(
            finding
            for finding in self.findings
            if finding.status is status
        )

    def _count_status(
        self,
        status: GovernanceTraceIntegrityAuditStatus,
    ) -> int:
        return sum(
            1
            for finding in self.findings
            if finding.status is status
        )


class DeploymentGovernanceIntegrityAuditService:
    """
    Performs comprehensive integrity audits over persisted governance traces.

    Unlike normal repository reads, the audit records individual failures and
    continues scanning the remaining persisted records.
    """

    def __init__(
        self,
        source: DeploymentGovernanceTraceIntegrityAuditSource,
    ) -> None:
        if not isinstance(
            source,
            DeploymentGovernanceTraceIntegrityAuditSource,
        ):
            raise TypeError(
                "source must implement "
                "DeploymentGovernanceTraceIntegrityAuditSource"
            )

        self._source = source

    def audit(
        self,
        *,
        batch_size: int = 500,
    ) -> GovernanceTraceIntegrityAuditReport:
        """
        Audit every persisted governance trace exposed by the source.
        """

        if batch_size <= 0:
            raise ValueError(
                "batch_size must be greater than zero"
            )

        started_at = datetime.now(
            timezone.utc
        )

        candidates = (
            self._source.iter_integrity_audit_candidates(
                batch_size=batch_size
            )
        )

        findings = tuple(
            self._audit_candidate(
                candidate
            )
            for candidate in candidates
        )

        completed_at = datetime.now(
            timezone.utc
        )

        return GovernanceTraceIntegrityAuditReport(
            started_at=started_at,
            completed_at=completed_at,
            findings=findings,
        )

    def _audit_candidate(
        self,
        candidate: GovernanceTraceIntegrityAuditCandidate,
    ) -> GovernanceTraceIntegrityAuditFinding:
        """
        Audit one persisted governance trace candidate.
        """

        if candidate.record is None:
            return GovernanceTraceIntegrityAuditFinding(
                trace_id=candidate.trace_id,
                status=(
                    GovernanceTraceIntegrityAuditStatus
                    .INVALID_PERSISTED_RECORD
                ),
                message=(
                    candidate.reconstruction_error
                    or
                    "persisted governance trace could not "
                    "be reconstructed"
                ),
            )

        if not candidate.has_complete_integrity_metadata:
            return GovernanceTraceIntegrityAuditFinding(
                trace_id=candidate.trace_id,
                status=(
                    GovernanceTraceIntegrityAuditStatus
                    .MISSING_INTEGRITY_METADATA
                ),
                message=(
                    "persisted governance trace has incomplete "
                    "integrity metadata"
                ),
            )

        try:
            metadata = GovernanceTraceIntegrityMetadata(
                algorithm=str(
                    candidate.integrity_algorithm
                ),
                version=int(
                    candidate.integrity_version
                ),
                digest=str(
                    candidate.integrity_digest
                ),
            )

        except (
            TypeError,
            ValueError,
        ) as exc:
            return GovernanceTraceIntegrityAuditFinding(
                trace_id=candidate.trace_id,
                status=(
                    GovernanceTraceIntegrityAuditStatus
                    .INVALID_INTEGRITY_METADATA
                ),
                message=str(
                    exc
                ),
            )

        try:
            DeploymentGovernanceTraceIntegrity.verify(
                candidate.record,
                metadata,
            )

        except GovernanceTraceIntegrityMismatchError as exc:
            return GovernanceTraceIntegrityAuditFinding(
                trace_id=candidate.trace_id,
                status=(
                    GovernanceTraceIntegrityAuditStatus
                    .INTEGRITY_MISMATCH
                ),
                message=str(
                    exc
                ),
            )

        except GovernanceTraceIntegrityError as exc:
            return GovernanceTraceIntegrityAuditFinding(
                trace_id=candidate.trace_id,
                status=(
                    GovernanceTraceIntegrityAuditStatus
                    .INVALID_INTEGRITY_METADATA
                ),
                message=str(
                    exc
                ),
            )

        return GovernanceTraceIntegrityAuditFinding(
            trace_id=candidate.trace_id,
            status=(
                GovernanceTraceIntegrityAuditStatus.VALID
            ),
            message=None,
        )
