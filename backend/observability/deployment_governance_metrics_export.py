from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_metrics import (
        GovernanceIntegrityMetrics,
        GovernanceIntegrityMetricsService,
    )

_CSV_FIELDNAMES = (
    "row_type",
    "captured_at",
    "total_dispatches",
    "successful_dispatches",
    "failed_dispatches",
    "retry_dispatches",
    "average_duration_ms",
)


class GovernanceIntegrityMetricsExportService:
    """
    Exports the current governance audit notification delivery
    metrics, and optionally their captured history, for offline
    analysis.

    Reads through a GovernanceIntegrityMetricsService rather than
    holding its own state: this service only formats, it never
    records or persists anything.
    """

    def __init__(
        self,
        metrics_service: "GovernanceIntegrityMetricsService",
    ) -> None:
        self._metrics_service = metrics_service

    def export_dict(
        self,
        *,
        include_history: bool = False,
        history_limit: int | None = None,
    ) -> dict[str, object]:
        """
        Build the export payload as a plain dict with a fixed,
        deterministic field order: exported_at, then metrics, then
        history (only when requested).
        """

        payload: dict[str, object] = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "metrics": self._metrics_service.snapshot().to_dict(),
        }

        if include_history:
            payload["history"] = [
                snapshot.to_dict()
                for snapshot in self._metrics_service.history(
                    history_limit
                )
            ]

        return payload

    def export_json(
        self,
        *,
        include_history: bool = False,
        history_limit: int | None = None,
    ) -> str:
        """
        Render the export payload as indented JSON, preserving the
        deterministic field order from export_dict rather than
        sorting keys alphabetically.
        """

        return json.dumps(
            self.export_dict(
                include_history=include_history,
                history_limit=history_limit,
            ),
            indent=2,
            ensure_ascii=False,
        )

    def export_csv(
        self,
        *,
        include_history: bool = False,
        history_limit: int | None = None,
    ) -> str:
        """
        Render the export payload as CSV text: always one "current"
        row for the live metrics, followed by one "history" row per
        captured snapshot when include_history is set, newest first.

        Columns are fixed by _CSV_FIELDNAMES so column order never
        depends on dict iteration order.
        """

        buffer = io.StringIO()

        writer = csv.DictWriter(buffer, fieldnames=_CSV_FIELDNAMES)

        writer.writeheader()

        exported_at = datetime.now(timezone.utc)

        writer.writerow(
            self._metrics_row(
                "current",
                exported_at,
                self._metrics_service.snapshot(),
            )
        )

        if include_history:
            for snapshot in self._metrics_service.history(
                history_limit
            ):
                writer.writerow(
                    self._metrics_row(
                        "history",
                        snapshot.captured_at,
                        snapshot.metrics,
                    )
                )

        return buffer.getvalue()

    @staticmethod
    def _metrics_row(
        row_type: str,
        captured_at: datetime,
        metrics: "GovernanceIntegrityMetrics",
    ) -> dict[str, object]:
        return {
            "row_type": row_type,
            "captured_at": captured_at.isoformat(),
            "total_dispatches": metrics.total_dispatches,
            "successful_dispatches": metrics.successful_dispatches,
            "failed_dispatches": metrics.failed_dispatches,
            "retry_dispatches": metrics.retry_dispatches,
            "average_duration_ms": metrics.average_duration_ms,
        }
