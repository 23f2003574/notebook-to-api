from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .deployment_governance_audit_history import (
    GovernanceIntegrityAuditHistoryRepository,
    GovernanceIntegrityAuditRecord,
)


class GovernanceIntegrityAuditCurrentState(
    str,
    Enum,
):
    NO_HISTORY = "no_history"

    HEALTHY = "healthy"

    UNHEALTHY = "unhealthy"


@dataclass(frozen=True)
class GovernanceIntegrityAuditStatisticsSnapshot:
    """
    Aggregate operational statistics derived from audit history.

    Answers "what has happened overall?" — distinct from trend analysis
    ("is the recent direction improving or degrading?") and regression
    detection ("did the latest state become worse than its immediate
    baseline?"). A history can be 95% healthy overall (this snapshot)
    while its very latest audit just regressed (a separate concern).
    """

    total_audits: int

    healthy_audits: int

    unhealthy_audits: int

    health_rate: float | None

    current_state: GovernanceIntegrityAuditCurrentState

    current_streak: int

    longest_healthy_streak: int

    longest_unhealthy_streak: int

    first_audit_started_at: datetime | None

    latest_audit_started_at: datetime | None

    total_records_checked: int

    total_invalid_records: int

    total_integrity_mismatches: int

    total_missing_integrity_metadata: int

    total_invalid_integrity_metadata: int

    def __post_init__(self) -> None:
        non_negative_fields = (
            self.total_audits,
            self.healthy_audits,
            self.unhealthy_audits,
            self.current_streak,
            self.longest_healthy_streak,
            self.longest_unhealthy_streak,
            self.total_records_checked,
            self.total_invalid_records,
            self.total_integrity_mismatches,
            self.total_missing_integrity_metadata,
            self.total_invalid_integrity_metadata,
        )

        if any(value < 0 for value in non_negative_fields):
            raise ValueError(
                "audit statistics counts must not be negative"
            )

        if (
            self.healthy_audits + self.unhealthy_audits
            != self.total_audits
        ):
            raise ValueError(
                "healthy_audits + unhealthy_audits "
                "must equal total_audits"
            )

        if self.total_audits == 0:
            if self.health_rate is not None:
                raise ValueError(
                    "empty history must not have a health rate"
                )

            if (
                self.current_state
                is not GovernanceIntegrityAuditCurrentState.NO_HISTORY
            ):
                raise ValueError(
                    "empty history must use NO_HISTORY state"
                )

            if self.current_streak != 0:
                raise ValueError(
                    "empty history must have a zero current streak"
                )

        else:
            if self.health_rate is None:
                raise ValueError(
                    "non-empty history must have a health rate"
                )

            if not (0.0 <= self.health_rate <= 1.0):
                raise ValueError(
                    "health_rate must be between zero and one"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "total_audits": self.total_audits,
            "healthy_audits": self.healthy_audits,
            "unhealthy_audits": self.unhealthy_audits,
            "health_rate": self.health_rate,
            "current_state": self.current_state.value,
            "current_streak": self.current_streak,
            "longest_healthy_streak": self.longest_healthy_streak,
            "longest_unhealthy_streak": self.longest_unhealthy_streak,
            "first_audit_started_at": (
                None
                if self.first_audit_started_at is None
                else self.first_audit_started_at.isoformat()
            ),
            "latest_audit_started_at": (
                None
                if self.latest_audit_started_at is None
                else self.latest_audit_started_at.isoformat()
            ),
            "aggregate_failures": {
                "total_records_checked": (
                    self.total_records_checked
                ),
                "invalid_records": self.total_invalid_records,
                "integrity_mismatches": (
                    self.total_integrity_mismatches
                ),
                "missing_integrity_metadata": (
                    self.total_missing_integrity_metadata
                ),
                "invalid_integrity_metadata": (
                    self.total_invalid_integrity_metadata
                ),
            },
        }


def calculate_governance_integrity_audit_statistics(
    records: tuple[GovernanceIntegrityAuditRecord, ...],
) -> GovernanceIntegrityAuditStatisticsSnapshot:
    """
    Calculate aggregate statistics from newest-first audit records.

    A pure function (no repository dependency) so it can be reused
    against any already-selected record set: a repository window, an
    exported evidence bundle's records, or future offline analysis.
    """

    if not records:
        return GovernanceIntegrityAuditStatisticsSnapshot(
            total_audits=0,
            healthy_audits=0,
            unhealthy_audits=0,
            health_rate=None,
            current_state=(
                GovernanceIntegrityAuditCurrentState.NO_HISTORY
            ),
            current_streak=0,
            longest_healthy_streak=0,
            longest_unhealthy_streak=0,
            first_audit_started_at=None,
            latest_audit_started_at=None,
            total_records_checked=0,
            total_invalid_records=0,
            total_integrity_mismatches=0,
            total_missing_integrity_metadata=0,
            total_invalid_integrity_metadata=0,
        )

    total_audits = len(records)

    healthy_audits = sum(
        1 for record in records if record.healthy
    )

    unhealthy_audits = total_audits - healthy_audits

    health_rate = healthy_audits / total_audits

    current_state = (
        GovernanceIntegrityAuditCurrentState.HEALTHY
        if records[0].healthy
        else GovernanceIntegrityAuditCurrentState.UNHEALTHY
    )

    current_health = records[0].healthy

    current_streak = 0

    for record in records:
        if record.healthy != current_health:
            break

        current_streak += 1

    # Streak extremes are computed oldest-to-newest so consecutive runs
    # are tracked in chronological order rather than the repository's
    # newest-first listing order.
    chronological_records = tuple(reversed(records))

    longest_healthy_streak = 0
    longest_unhealthy_streak = 0

    healthy_streak = 0
    unhealthy_streak = 0

    for record in chronological_records:
        if record.healthy:
            healthy_streak += 1
            unhealthy_streak = 0

            longest_healthy_streak = max(
                longest_healthy_streak, healthy_streak
            )

        else:
            unhealthy_streak += 1
            healthy_streak = 0

            longest_unhealthy_streak = max(
                longest_unhealthy_streak, unhealthy_streak
            )

    latest_audit_started_at = records[0].started_at
    first_audit_started_at = records[-1].started_at

    total_records_checked = sum(
        record.total_records for record in records
    )

    total_invalid_records = sum(
        record.invalid_records for record in records
    )

    total_integrity_mismatches = sum(
        record.integrity_mismatches for record in records
    )

    total_missing_integrity_metadata = sum(
        record.missing_integrity_metadata for record in records
    )

    total_invalid_integrity_metadata = sum(
        record.invalid_integrity_metadata for record in records
    )

    return GovernanceIntegrityAuditStatisticsSnapshot(
        total_audits=total_audits,
        healthy_audits=healthy_audits,
        unhealthy_audits=unhealthy_audits,
        health_rate=health_rate,
        current_state=current_state,
        current_streak=current_streak,
        longest_healthy_streak=longest_healthy_streak,
        longest_unhealthy_streak=longest_unhealthy_streak,
        first_audit_started_at=first_audit_started_at,
        latest_audit_started_at=latest_audit_started_at,
        total_records_checked=total_records_checked,
        total_invalid_records=total_invalid_records,
        total_integrity_mismatches=total_integrity_mismatches,
        total_missing_integrity_metadata=(
            total_missing_integrity_metadata
        ),
        total_invalid_integrity_metadata=(
            total_invalid_integrity_metadata
        ),
    )


class GovernanceIntegrityAuditStatisticsService:
    """
    Calculates operational statistics from persisted audit history.
    """

    def __init__(
        self,
        repository: GovernanceIntegrityAuditHistoryRepository,
    ) -> None:
        self._repository = repository

    def calculate(
        self,
        *,
        limit: int | None = None,
    ) -> GovernanceIntegrityAuditStatisticsSnapshot:
        if limit is not None and limit <= 0:
            raise ValueError(
                "limit must be greater than zero"
            )

        records = (
            self._repository.list(limit=limit)
            if limit is not None
            else self._repository.list()
        )

        return calculate_governance_integrity_audit_statistics(
            records
        )
