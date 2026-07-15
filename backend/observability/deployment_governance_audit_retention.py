from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from .deployment_governance_audit_history import (
    GovernanceIntegrityAuditHistoryRepository,
)


GovernanceIntegrityAuditRetentionClock = Callable[[], datetime]


@dataclass(frozen=True)
class GovernanceIntegrityAuditRetentionPolicy:
    """
    Policy controlling which historical audit records are retained.

    A record is prunable if it violates either configured limit (union
    semantics), unless it is the single most recent audit and
    preserve_latest is enabled.
    """

    max_records: int | None = None

    max_age_days: int | None = None

    preserve_latest: bool = True

    def __post_init__(self) -> None:
        if self.max_records is None and self.max_age_days is None:
            raise ValueError(
                "at least one retention limit must be configured"
            )

        if self.max_records is not None and self.max_records <= 0:
            raise ValueError(
                "max_records must be greater than zero"
            )

        if self.max_age_days is not None and self.max_age_days <= 0:
            raise ValueError(
                "max_age_days must be greater than zero"
            )


@dataclass(frozen=True)
class GovernanceIntegrityAuditPruningPlan:
    """
    Deterministic preview of one retention-policy evaluation.
    """

    evaluated_at: datetime

    total_records: int

    retained_records: int

    prunable_records: int

    retained_audit_ids: tuple[str, ...]

    prunable_audit_ids: tuple[str, ...]

    oldest_retained_started_at: datetime | None

    newest_retained_started_at: datetime | None

    def __post_init__(self) -> None:
        if self.total_records < 0:
            raise ValueError(
                "total_records must not be negative"
            )

        if (
            self.retained_records + self.prunable_records
            != self.total_records
        ):
            raise ValueError(
                "retained_records + prunable_records "
                "must equal total_records"
            )

        if len(self.retained_audit_ids) != self.retained_records:
            raise ValueError(
                "retained_audit_ids must match retained_records"
            )

        if len(self.prunable_audit_ids) != self.prunable_records:
            raise ValueError(
                "prunable_audit_ids must match prunable_records"
            )

    @property
    def has_prunable_records(self) -> bool:
        return self.prunable_records > 0

    def to_dict(self) -> dict[str, object]:
        return {
            "evaluated_at": self.evaluated_at.isoformat(),
            "total_records": self.total_records,
            "retained_records": self.retained_records,
            "prunable_records": self.prunable_records,
            "has_prunable_records": self.has_prunable_records,
            "retained_audit_ids": list(self.retained_audit_ids),
            "prunable_audit_ids": list(self.prunable_audit_ids),
            "oldest_retained_started_at": (
                None
                if self.oldest_retained_started_at is None
                else self.oldest_retained_started_at.isoformat()
            ),
            "newest_retained_started_at": (
                None
                if self.newest_retained_started_at is None
                else self.newest_retained_started_at.isoformat()
            ),
        }


@dataclass(frozen=True)
class GovernanceIntegrityAuditPruningResult:
    """
    Result of applying (or previewing) one pruning plan.
    """

    plan: GovernanceIntegrityAuditPruningPlan

    applied: bool

    deleted_records: int

    def __post_init__(self) -> None:
        if self.deleted_records < 0:
            raise ValueError(
                "deleted_records must not be negative"
            )

        if not self.applied and self.deleted_records != 0:
            raise ValueError(
                "dry-run pruning must not delete records"
            )

        if self.deleted_records > self.plan.prunable_records:
            raise ValueError(
                "deleted_records must not exceed "
                "planned prunable records"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "applied": self.applied,
            "deleted_records": self.deleted_records,
            "plan": self.plan.to_dict(),
        }


class GovernanceIntegrityAuditRetentionService:
    """
    Plans and applies audit-history retention policies.

    Planning (plan()) and execution (prune()) are deliberately separate:
    plan() is always a pure, side-effect-free preview so callers can render
    or validate a pruning decision before any deletion occurs.
    """

    def __init__(
        self,
        repository: GovernanceIntegrityAuditHistoryRepository,
        *,
        clock: GovernanceIntegrityAuditRetentionClock | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def plan(
        self,
        policy: GovernanceIntegrityAuditRetentionPolicy,
    ) -> GovernanceIntegrityAuditPruningPlan:
        evaluated_at = self._clock()

        # list() is newest-first per the audit-history repository contract.
        records = self._repository.list()

        prunable_ids: set[str] = set()

        if policy.max_records is not None:
            for record in records[policy.max_records:]:
                prunable_ids.add(record.audit_id)

        if policy.max_age_days is not None:
            cutoff = evaluated_at - timedelta(days=policy.max_age_days)

            for record in records:
                if record.started_at < cutoff:
                    prunable_ids.add(record.audit_id)

        if policy.preserve_latest and records:
            prunable_ids.discard(records[0].audit_id)

        retained_records = tuple(
            record
            for record in records
            if record.audit_id not in prunable_ids
        )

        prunable_records = tuple(
            record
            for record in records
            if record.audit_id in prunable_ids
        )

        return GovernanceIntegrityAuditPruningPlan(
            evaluated_at=evaluated_at,
            total_records=len(records),
            retained_records=len(retained_records),
            prunable_records=len(prunable_records),
            retained_audit_ids=tuple(
                record.audit_id for record in retained_records
            ),
            prunable_audit_ids=tuple(
                record.audit_id for record in prunable_records
            ),
            oldest_retained_started_at=(
                None
                if not retained_records
                else retained_records[-1].started_at
            ),
            newest_retained_started_at=(
                None
                if not retained_records
                else retained_records[0].started_at
            ),
        )

    def prune(
        self,
        policy: GovernanceIntegrityAuditRetentionPolicy,
        *,
        apply: bool = False,
    ) -> GovernanceIntegrityAuditPruningResult:
        plan = self.plan(policy)

        if not apply:
            return GovernanceIntegrityAuditPruningResult(
                plan=plan,
                applied=False,
                deleted_records=0,
            )

        deleted_records = self._repository.delete_by_ids(
            plan.prunable_audit_ids
        )

        return GovernanceIntegrityAuditPruningResult(
            plan=plan,
            applied=True,
            deleted_records=deleted_records,
        )
