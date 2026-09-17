from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .deployment_governance_audit_history import (
    GovernanceIntegrityAuditHistoryRepository,
    GovernanceIntegrityAuditRecord,
)
from .deployment_governance_audit_history_service import (
    serialize_governance_integrity_audit_record,
)


@dataclass(frozen=True)
class GovernanceIntegrityAuditReplay:
    """
    Reconstructed context of one previously recorded governance integrity
    audit.

    Replay never re-executes the audit and never mutates persisted state;
    it only reassembles a stored audit record for trend analysis,
    regression comparison, or debugging.
    """

    audit_id: str

    record: GovernanceIntegrityAuditRecord

    replayed_at: datetime

    def __post_init__(self) -> None:
        if self.audit_id != self.record.audit_id:
            raise ValueError(
                "audit_id must match record.audit_id"
            )

        if self.replayed_at.tzinfo is None:
            raise ValueError(
                "replayed_at must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "audit_id": self.audit_id,
            "replayed_at": self.replayed_at.isoformat(),
            "record": serialize_governance_integrity_audit_record(
                self.record
            ),
        }


class GovernanceIntegrityAuditReplayService:
    """
    Reconstructs stored governance integrity audits from history.

    Purely read-only: no persistence changes result from any replay.
    """

    def __init__(
        self,
        repository: GovernanceIntegrityAuditHistoryRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

    def replay(
        self,
        audit_id: str,
    ) -> GovernanceIntegrityAuditReplay:
        """
        Replay one audit by its identifier.

        Raises KeyError if no audit with this identifier is recorded.
        """

        record = self._repository.get_by_audit_id(audit_id)

        if record is None:
            raise KeyError(
                f"governance integrity audit '{audit_id}' was not found"
            )

        return GovernanceIntegrityAuditReplay(
            audit_id=record.audit_id,
            record=record,
            replayed_at=self._clock(),
        )

    def replay_latest(
        self,
    ) -> GovernanceIntegrityAuditReplay:
        """
        Replay the most recently started audit.

        Raises LookupError if no audits have been recorded.
        """

        record = self._repository.latest()

        if record is None:
            raise LookupError(
                "no governance integrity audits have been recorded"
            )

        return GovernanceIntegrityAuditReplay(
            audit_id=record.audit_id,
            record=record,
            replayed_at=self._clock(),
        )

    def replay_recent(
        self,
        *,
        limit: int,
    ) -> tuple[
        GovernanceIntegrityAuditReplay,
        ...
    ]:
        """
        Replay the `limit` most recently started audits, newest first.
        """

        if limit <= 0:
            raise ValueError(
                "limit must be greater than zero"
            )

        records = self._repository.list(limit=limit)

        replayed_at = self._clock()

        return tuple(
            GovernanceIntegrityAuditReplay(
                audit_id=record.audit_id,
                record=record,
                replayed_at=replayed_at,
            )
            for record in records
        )
