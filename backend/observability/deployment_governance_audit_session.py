from __future__ import annotations

from dataclasses import dataclass

from .deployment_governance_audit_history import (
    GovernanceIntegrityAuditHistoryRepository,
    GovernanceIntegrityAuditRecord,
)
from .deployment_governance_audit_history_service import (
    serialize_governance_integrity_audit_record,
)


@dataclass(frozen=True)
class GovernanceIntegrityAuditSession:
    """
    Ordered reconstruction of recorded governance integrity audits, for
    navigation and analysis (e.g. a future timeline UI stepping through
    audits one at a time).
    """

    records: tuple[
        GovernanceIntegrityAuditRecord,
        ...
    ]

    total_audits: int

    first_audit_id: str | None

    latest_audit_id: str | None

    def __post_init__(self) -> None:
        if self.total_audits != len(self.records):
            raise ValueError(
                "total_audits must match the number of records"
            )

        expected_latest_audit_id = (
            self.records[0].audit_id if self.records else None
        )

        if self.latest_audit_id != expected_latest_audit_id:
            raise ValueError(
                "latest_audit_id must match the newest record"
            )

        expected_first_audit_id = (
            self.records[-1].audit_id if self.records else None
        )

        if self.first_audit_id != expected_first_audit_id:
            raise ValueError(
                "first_audit_id must match the oldest record"
            )

        for previous, current in zip(
            self.records, self.records[1:]
        ):
            if previous.started_at < current.started_at:
                raise ValueError(
                    "records must be ordered newest to oldest"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "total_audits": self.total_audits,
            "latest_audit_id": self.latest_audit_id,
            "first_audit_id": self.first_audit_id,
            "records": [
                serialize_governance_integrity_audit_record(record)
                for record in self.records
            ],
        }


class GovernanceIntegrityAuditSessionService:
    """
    Reconstructs an ordered session of recorded governance integrity
    audits from history.
    """

    def __init__(
        self,
        repository: GovernanceIntegrityAuditHistoryRepository,
    ) -> None:
        self._repository = repository

    def session(
        self,
        *,
        limit: int | None = None,
    ) -> GovernanceIntegrityAuditSession:
        """
        Reconstruct an ordered session of the most recent audits.
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

        return GovernanceIntegrityAuditSession(
            records=records,
            total_audits=len(records),
            first_audit_id=(
                records[-1].audit_id if records else None
            ),
            latest_audit_id=(
                records[0].audit_id if records else None
            ),
        )

    def latest(
        self,
    ) -> GovernanceIntegrityAuditRecord | None:
        """
        Return the most recently started audit, or None if none exist.
        """

        return self._repository.latest()

    def oldest(
        self,
    ) -> GovernanceIntegrityAuditRecord | None:
        """
        Return the earliest recorded audit, or None if none exist.
        """

        records = self._repository.list()

        return records[-1] if records else None

    def audit_ids(
        self,
    ) -> tuple[str, ...]:
        """
        Return every recorded audit identifier, newest to oldest.
        """

        return tuple(
            record.audit_id
            for record in self._repository.list()
        )
