from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Sequence

from .deployment_governance_audit_collections import (
    GovernanceIntegrityAuditCollectionRepository,
)
from .deployment_governance_audit_history import (
    GovernanceIntegrityAuditHistoryRepository,
    GovernanceIntegrityAuditRecord,
)
from .deployment_governance_audit_history_service import (
    serialize_governance_integrity_audit_record,
)
from .deployment_governance_audit_statistics import (
    GovernanceIntegrityAuditStatisticsService,
    GovernanceIntegrityAuditStatisticsSnapshot,
    calculate_governance_integrity_audit_statistics,
)


@dataclass(frozen=True)
class GovernanceIntegrityAuditReport:
    """
    A portable, point-in-time summary of one or more governance
    integrity audits.

    Reports never re-execute or mutate audits; they only reassemble
    already-recorded records into a shareable JSON or Markdown document.
    """

    title: str

    generated_at: datetime

    audits: tuple[
        GovernanceIntegrityAuditRecord,
        ...
    ]

    statistics: GovernanceIntegrityAuditStatisticsSnapshot

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError(
                "title must not be empty"
            )

        if self.generated_at.tzinfo is None:
            raise ValueError(
                "generated_at must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "generated_at": self.generated_at.isoformat(),
            "audits": [
                serialize_governance_integrity_audit_record(record)
                for record in self.audits
            ],
            "statistics": self.statistics.to_dict(),
        }

    def to_json(
        self,
        *,
        pretty: bool = True,
    ) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            indent=2 if pretty else None,
            sort_keys=True,
        )

    def to_markdown(self) -> str:
        lines: list[str] = [
            f"# {self.title}",
            "",
            f"Generated: {self.generated_at.isoformat()}",
            "",
            "## Statistics",
            "",
        ]

        if self.statistics.total_audits == 0:
            lines.append(
                "No governance integrity audits are included in "
                "this report."
            )

        else:
            assert self.statistics.health_rate is not None

            lines.append(
                f"- Audits: {self.statistics.total_audits}"
            )
            lines.append(
                f"- Healthy: {self.statistics.healthy_audits}"
            )
            lines.append(
                f"- Unhealthy: {self.statistics.unhealthy_audits}"
            )
            lines.append(
                "- Health rate: "
                f"{self.statistics.health_rate * 100.0:.2f}%"
            )

        lines.append("")

        lines.append("## Audits")

        lines.append("")

        if not self.audits:
            lines.append("No audits are included in this report.")

        else:
            for record in self.audits:
                lines.append(f"- {record.audit_id}")

        return "\n".join(lines) + "\n"


class GovernanceIntegrityAuditReportService:
    """
    Generates portable governance integrity audit reports from an
    explicit set of audits or from a collection.
    """

    def __init__(
        self,
        history_repository: GovernanceIntegrityAuditHistoryRepository,
        collection_repository: GovernanceIntegrityAuditCollectionRepository,
        statistics_service: GovernanceIntegrityAuditStatisticsService,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._history_repository = history_repository

        self._collection_repository = collection_repository

        self._statistics_service = statistics_service

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

    def report_from_audits(
        self,
        title: str,
        audit_ids: Sequence[str],
    ) -> GovernanceIntegrityAuditReport:
        """
        Build a report from an explicit, ordered list of audit
        identifiers.

        Raises LookupError if any audit does not exist. The audits in
        the resulting report preserve the requested order; statistics
        are computed from the selected audits only.
        """

        records = tuple(
            self._require_record(audit_id)
            for audit_id in audit_ids
        )

        return GovernanceIntegrityAuditReport(
            title=title,
            generated_at=self._clock(),
            audits=records,
            statistics=self._statistics_for(records),
        )

    def report_from_collection(
        self,
        collection: str,
        *,
        title: str | None = None,
    ) -> GovernanceIntegrityAuditReport:
        """
        Build a report from every audit in a collection.

        Raises LookupError if the collection does not exist. Defaults
        the report title to the collection's name.
        """

        collection_record = self._collection_repository.get(
            collection
        )

        if collection_record is None:
            raise LookupError(
                f"collection '{collection}' was not found"
            )

        records = tuple(
            self._require_record(audit_id)
            for audit_id in self._collection_repository.audits(
                collection
            )
        )

        return GovernanceIntegrityAuditReport(
            title=title or collection_record.name,
            generated_at=self._clock(),
            audits=records,
            statistics=self._statistics_for(records),
        )

    def _require_record(
        self,
        audit_id: str,
    ) -> GovernanceIntegrityAuditRecord:
        record = self._history_repository.get_by_audit_id(audit_id)

        if record is None:
            raise LookupError(
                f"governance integrity audit '{audit_id}' was not found"
            )

        return record

    @staticmethod
    def _statistics_for(
        records: tuple[GovernanceIntegrityAuditRecord, ...],
    ) -> GovernanceIntegrityAuditStatisticsSnapshot:
        # Statistics (streaks in particular) assume newest-to-oldest
        # input, independent of the order the caller requested audits
        # be listed in the report itself.
        chronologically_ordered = tuple(
            sorted(
                records,
                key=lambda record: (
                    record.started_at,
                    record.audit_id,
                ),
                reverse=True,
            )
        )

        return calculate_governance_integrity_audit_statistics(
            chronologically_ordered
        )
