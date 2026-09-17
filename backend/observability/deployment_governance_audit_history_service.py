from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .deployment_governance_audit_history import (
    GovernanceIntegrityAuditHistoryQuery,
    GovernanceIntegrityAuditHistoryRepository,
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
)


def serialize_governance_integrity_audit_record(
    record: GovernanceIntegrityAuditRecord,
) -> dict[str, object]:
    """
    Serialize one historical audit record into JSON-compatible primitives.
    """

    return {
        "audit_id": record.audit_id,
        "backend": record.backend,
        "started_at": record.started_at.isoformat(),
        "completed_at": record.completed_at.isoformat(),
        "duration_seconds": record.duration_seconds,
        "outcome": record.outcome.value,
        "healthy": record.healthy,
        "total_records": record.total_records,
        "valid_records": record.valid_records,
        "invalid_records": record.invalid_records,
        "integrity_mismatches": record.integrity_mismatches,
        "missing_integrity_metadata": record.missing_integrity_metadata,
        "invalid_integrity_metadata": record.invalid_integrity_metadata,
        "invalid_persisted_records": record.invalid_persisted_records,
    }


@dataclass(frozen=True)
class GovernanceIntegrityAuditHistorySummary:
    """
    Aggregate summary of recorded governance integrity audits.
    """

    total_audits: int

    healthy_audits: int

    unhealthy_audits: int

    latest_audit: GovernanceIntegrityAuditRecord | None

    def __post_init__(self) -> None:
        if self.total_audits < 0:
            raise ValueError(
                "total_audits must not be negative"
            )

        if self.healthy_audits < 0:
            raise ValueError(
                "healthy_audits must not be negative"
            )

        if self.unhealthy_audits < 0:
            raise ValueError(
                "unhealthy_audits must not be negative"
            )

        if (
            self.healthy_audits + self.unhealthy_audits
            != self.total_audits
        ):
            raise ValueError(
                "healthy_audits + unhealthy_audits "
                "must equal total_audits"
            )

    @property
    def has_history(self) -> bool:
        return self.total_audits > 0

    @property
    def has_unhealthy_history(self) -> bool:
        return self.unhealthy_audits > 0

    def to_dict(self) -> dict[str, object]:
        return {
            "total_audits": self.total_audits,
            "healthy_audits": self.healthy_audits,
            "unhealthy_audits": self.unhealthy_audits,
            "has_history": self.has_history,
            "has_unhealthy_history": self.has_unhealthy_history,
            "latest_audit": (
                None
                if self.latest_audit is None
                else serialize_governance_integrity_audit_record(
                    self.latest_audit
                )
            ),
        }


@dataclass(frozen=True)
class GovernanceIntegrityAuditHistoryResult:
    """
    Result of one audit-history query: the complete-history summary plus the
    records matching this specific query's filters. The two are distinct on
    purpose: summary always describes the whole history, records describes
    only what this query matched.
    """

    summary: GovernanceIntegrityAuditHistorySummary

    records: tuple[GovernanceIntegrityAuditRecord, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "summary": self.summary.to_dict(),
            "records": [
                serialize_governance_integrity_audit_record(record)
                for record in self.records
            ],
        }


class GovernanceIntegrityAuditHistoryService:
    """
    Read-only application service for governance integrity audit history.

    Owns query construction, summary assembly, and canonical serialization
    so callers (CLI, future API endpoints) only decide which filters to
    apply and how to render the result.
    """

    def __init__(
        self,
        repository: GovernanceIntegrityAuditHistoryRepository,
    ) -> None:
        self._repository = repository

    def summary(self) -> GovernanceIntegrityAuditHistorySummary:
        """
        Return aggregate audit-history statistics.
        """

        return GovernanceIntegrityAuditHistorySummary(
            total_audits=self._repository.count(),
            healthy_audits=self._repository.count_by_outcome(
                GovernanceIntegrityAuditOutcome.HEALTHY
            ),
            unhealthy_audits=self._repository.count_by_outcome(
                GovernanceIntegrityAuditOutcome.UNHEALTHY
            ),
            latest_audit=self._repository.latest(),
        )

    def search(
        self,
        *,
        backend: str | None = None,
        outcome: GovernanceIntegrityAuditOutcome | None = None,
        started_at_or_after: datetime | None = None,
        started_at_or_before: datetime | None = None,
        limit: int = 20,
    ) -> GovernanceIntegrityAuditHistoryResult:
        """
        Query historical integrity audits and return aggregate context
        alongside the matching records.
        """

        query = GovernanceIntegrityAuditHistoryQuery(
            backend=backend,
            outcome=outcome,
            started_at_or_after=started_at_or_after,
            started_at_or_before=started_at_or_before,
            limit=limit,
        )

        return GovernanceIntegrityAuditHistoryResult(
            summary=self.summary(),
            records=self._repository.query(query),
        )
