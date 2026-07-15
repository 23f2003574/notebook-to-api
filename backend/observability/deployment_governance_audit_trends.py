from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .deployment_governance_audit_history import (
    GovernanceIntegrityAuditHistoryRepository,
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
)


class GovernanceIntegrityAuditTrendDirection(
    str,
    Enum,
):
    """
    Direction of recent governance integrity health.

    Derived only from the most recent outcome transition: this is
    deliberately conservative and does not attempt forecasting, anomaly
    prediction, or statistical significance testing.
    """

    IMPROVING = "improving"

    STABLE = "stable"

    DEGRADING = "degrading"

    INSUFFICIENT_DATA = "insufficient_data"


def determine_governance_integrity_audit_trend_direction(
    records: tuple[GovernanceIntegrityAuditRecord, ...],
) -> GovernanceIntegrityAuditTrendDirection:
    """
    Determine the most recent outcome transition.

    Records must be ordered newest first.
    """

    if len(records) < 2:
        return GovernanceIntegrityAuditTrendDirection.INSUFFICIENT_DATA

    current = records[0].outcome
    previous = records[1].outcome

    if current is previous:
        return GovernanceIntegrityAuditTrendDirection.STABLE

    if (
        previous is GovernanceIntegrityAuditOutcome.UNHEALTHY
        and current is GovernanceIntegrityAuditOutcome.HEALTHY
    ):
        return GovernanceIntegrityAuditTrendDirection.IMPROVING

    return GovernanceIntegrityAuditTrendDirection.DEGRADING


def calculate_governance_integrity_audit_streak(
    records: tuple[GovernanceIntegrityAuditRecord, ...],
) -> int:
    """
    Count consecutive records matching the latest audit outcome.

    Records must be ordered newest first.
    """

    if not records:
        return 0

    current_outcome = records[0].outcome

    streak = 0

    for record in records:
        if record.outcome is not current_outcome:
            break

        streak += 1

    return streak


@dataclass(frozen=True)
class GovernanceIntegrityAuditTrendSnapshot:
    """
    Derived trend information for recent integrity audits.
    """

    sample_size: int

    healthy_audits: int

    unhealthy_audits: int

    health_rate: float | None

    failure_rate: float | None

    current_outcome: GovernanceIntegrityAuditOutcome | None

    previous_outcome: GovernanceIntegrityAuditOutcome | None

    current_streak: int

    direction: GovernanceIntegrityAuditTrendDirection

    def __post_init__(self) -> None:
        if self.sample_size < 0:
            raise ValueError(
                "sample_size must not be negative"
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
            != self.sample_size
        ):
            raise ValueError(
                "healthy_audits + unhealthy_audits "
                "must equal sample_size"
            )

        if self.current_streak < 0:
            raise ValueError(
                "current_streak must not be negative"
            )

        if self.sample_size == 0:
            if self.current_outcome is not None:
                raise ValueError(
                    "current_outcome must be absent "
                    "for an empty sample"
                )

            if self.current_streak != 0:
                raise ValueError(
                    "current_streak must be zero "
                    "for an empty sample"
                )

    @property
    def has_data(self) -> bool:
        return self.sample_size > 0

    def to_dict(self) -> dict[str, object]:
        return {
            "sample_size": self.sample_size,
            "healthy_audits": self.healthy_audits,
            "unhealthy_audits": self.unhealthy_audits,
            "health_rate": self.health_rate,
            "failure_rate": self.failure_rate,
            "current_outcome": (
                None
                if self.current_outcome is None
                else self.current_outcome.value
            ),
            "previous_outcome": (
                None
                if self.previous_outcome is None
                else self.previous_outcome.value
            ),
            "current_streak": self.current_streak,
            "direction": self.direction.value,
        }


class GovernanceIntegrityAuditTrendService:
    """
    Derives recent operational trends from audit history.

    Trends are computed over a bounded recent window (not the entire audit
    table) so a long healthy history does not mask a recent regression.
    """

    DEFAULT_WINDOW = 20

    def __init__(
        self,
        repository: GovernanceIntegrityAuditHistoryRepository,
    ) -> None:
        self._repository = repository

    def analyze(
        self,
        *,
        window: int = DEFAULT_WINDOW,
    ) -> GovernanceIntegrityAuditTrendSnapshot:
        """
        Analyze the most recent audit-history window.
        """

        if window <= 0:
            raise ValueError(
                "window must be greater than zero"
            )

        records = self._repository.list(limit=window)

        sample_size = len(records)

        if sample_size == 0:
            return GovernanceIntegrityAuditTrendSnapshot(
                sample_size=0,
                healthy_audits=0,
                unhealthy_audits=0,
                health_rate=None,
                failure_rate=None,
                current_outcome=None,
                previous_outcome=None,
                current_streak=0,
                direction=(
                    GovernanceIntegrityAuditTrendDirection
                    .INSUFFICIENT_DATA
                ),
            )

        healthy_audits = sum(
            1
            for record in records
            if record.outcome is GovernanceIntegrityAuditOutcome.HEALTHY
        )

        unhealthy_audits = sample_size - healthy_audits

        health_rate = healthy_audits / sample_size

        failure_rate = unhealthy_audits / sample_size

        return GovernanceIntegrityAuditTrendSnapshot(
            sample_size=sample_size,
            healthy_audits=healthy_audits,
            unhealthy_audits=unhealthy_audits,
            health_rate=health_rate,
            failure_rate=failure_rate,
            current_outcome=records[0].outcome,
            previous_outcome=(
                None if sample_size < 2 else records[1].outcome
            ),
            current_streak=(
                calculate_governance_integrity_audit_streak(records)
            ),
            direction=(
                determine_governance_integrity_audit_trend_direction(
                    records
                )
            ),
        )
