from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .deployment_governance_audit_history import (
    GovernanceIntegrityAuditHistoryRepository,
    GovernanceIntegrityAuditRecord,
)


class GovernanceIntegrityAuditTimelineState(
    str,
    Enum,
):
    """
    Coarse chronological state of one audit timeline event.
    """

    HEALTHY = "healthy"

    UNHEALTHY = "unhealthy"


@dataclass(frozen=True)
class GovernanceIntegrityAuditTimelineEvent:
    """
    One chronological audit-history entry for timeline visualization.

    A direct, uncalculated mapping of a stored audit record's identity,
    timestamps, state, and record counts -- no derived metrics.
    """

    audit_id: str

    started_at: datetime

    completed_at: datetime

    state: GovernanceIntegrityAuditTimelineState

    total_records: int

    invalid_records: int

    integrity_mismatches: int

    def to_dict(self) -> dict[str, object]:
        return {
            "audit_id": self.audit_id,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "state": self.state.value,
            "total_records": self.total_records,
            "invalid_records": self.invalid_records,
            "integrity_mismatches": self.integrity_mismatches,
        }


def _timeline_event_from_record(
    record: GovernanceIntegrityAuditRecord,
) -> GovernanceIntegrityAuditTimelineEvent:
    return GovernanceIntegrityAuditTimelineEvent(
        audit_id=record.audit_id,
        started_at=record.started_at,
        completed_at=record.completed_at,
        state=(
            GovernanceIntegrityAuditTimelineState.HEALTHY
            if record.healthy
            else GovernanceIntegrityAuditTimelineState.UNHEALTHY
        ),
        total_records=record.total_records,
        invalid_records=record.invalid_records,
        integrity_mismatches=record.integrity_mismatches,
    )


class GovernanceIntegrityAuditTimelineService:
    """
    Builds chronological timeline data from persisted audit history.
    """

    def __init__(
        self,
        repository: GovernanceIntegrityAuditHistoryRepository,
    ) -> None:
        self._repository = repository

    def timeline(
        self,
        *,
        limit: int | None = None,
    ) -> tuple[
        GovernanceIntegrityAuditTimelineEvent,
        ...
    ]:
        """
        Return timeline events in the repository's newest-to-oldest order.
        """

        if limit is not None and limit <= 0:
            raise ValueError(
                "limit must be greater than zero"
            )

        records = (
            self._repository.list(limit=limit)
            if limit is not None
            else self._repository.list()
        )

        return tuple(
            _timeline_event_from_record(record)
            for record in records
        )
