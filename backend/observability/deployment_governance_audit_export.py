from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .deployment_governance_audit_evidence_integrity import (
    GovernanceIntegrityAuditEvidenceManifest,
    build_governance_audit_evidence_manifest,
    default_governance_audit_evidence_manifest_path,
    write_governance_audit_evidence_manifest,
)
from .deployment_governance_audit_history import (
    GovernanceIntegrityAuditHistoryRepository,
    GovernanceIntegrityAuditRecord,
)
from .deployment_governance_audit_regression import (
    GovernanceIntegrityRegressionSnapshot,
    detect_governance_integrity_regression,
)
from .deployment_governance_audit_trends import (
    GovernanceIntegrityAuditTrendSnapshot,
    analyze_governance_integrity_audit_records,
)


GOVERNANCE_AUDIT_EVIDENCE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class GovernanceIntegrityAuditExportOptions:
    """
    Controls which evidence is included in an audit export.
    """

    limit: int | None = None

    include_trend: bool = True

    include_regression: bool = True

    trend_window: int = 20

    create_manifest: bool = True

    def __post_init__(self) -> None:
        if self.limit is not None and self.limit <= 0:
            raise ValueError(
                "limit must be greater than zero"
            )

        if self.trend_window <= 0:
            raise ValueError(
                "trend_window must be greater than zero"
            )


@dataclass(frozen=True)
class GovernanceIntegrityAuditExportSummary:
    """
    Summary of records included in one evidence bundle.
    """

    total_audits: int

    healthy_audits: int

    unhealthy_audits: int

    newest_audit_id: str | None

    oldest_audit_id: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "total_audits": self.total_audits,
            "healthy_audits": self.healthy_audits,
            "unhealthy_audits": self.unhealthy_audits,
            "newest_audit_id": self.newest_audit_id,
            "oldest_audit_id": self.oldest_audit_id,
        }


def _record_to_dict(
    record: GovernanceIntegrityAuditRecord,
) -> dict[str, object]:
    """
    The one canonical JSON representation of a historical audit record.

    Mirrors serialize_governance_integrity_audit_record() in
    deployment_governance_audit_history_service.py; kept as a private
    copy here (rather than imported) because the export bundle's schema
    is versioned independently and must not silently drift if the CLI
    inspection serializer changes shape.
    """

    return {
        "audit_id": record.audit_id,
        "backend": record.backend,
        "started_at": record.started_at.isoformat(),
        "completed_at": record.completed_at.isoformat(),
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
class GovernanceIntegrityAuditEvidenceBundle:
    """
    Portable representation of governance audit evidence.

    trend and regression are derived only from `records` (never from the
    full repository), so the bundle remains internally explainable after
    the original database is gone: sample_size can never exceed
    record_count.
    """

    schema_version: int

    exported_at: datetime

    record_count: int

    records: tuple[GovernanceIntegrityAuditRecord, ...]

    summary: GovernanceIntegrityAuditExportSummary

    trend: GovernanceIntegrityAuditTrendSnapshot | None

    regression: GovernanceIntegrityRegressionSnapshot | None

    def __post_init__(self) -> None:
        if self.schema_version <= 0:
            raise ValueError(
                "schema_version must be greater than zero"
            )

        if self.record_count != len(self.records):
            raise ValueError(
                "record_count must match records"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "exported_at": self.exported_at.isoformat(),
            "record_count": self.record_count,
            "records": [
                _record_to_dict(record) for record in self.records
            ],
            "summary": self.summary.to_dict(),
            "trend": (
                None if self.trend is None else self.trend.to_dict()
            ),
            "regression": (
                None
                if self.regression is None
                else self.regression.to_dict()
            ),
        }


def serialize_governance_integrity_audit_evidence(
    bundle: GovernanceIntegrityAuditEvidenceBundle,
    *,
    pretty: bool = True,
) -> str:
    """
    Deterministic UTF-8 JSON serialization: stable key ordering, ISO-8601
    timestamps, no Python-specific objects.
    """

    return json.dumps(
        bundle.to_dict(),
        ensure_ascii=False,
        indent=2 if pretty else None,
        sort_keys=True,
        separators=None if pretty else (",", ":"),
    )


@dataclass(frozen=True)
class GovernanceIntegrityAuditEvidenceExportResult:
    """
    Result of exporting governance evidence to disk.
    """

    bundle: GovernanceIntegrityAuditEvidenceBundle

    evidence_path: Path

    manifest: GovernanceIntegrityAuditEvidenceManifest | None

    manifest_path: Path | None

    def __post_init__(self) -> None:
        if (self.manifest is None) != (self.manifest_path is None):
            raise ValueError(
                "manifest and manifest_path must either both be "
                "present or both be absent"
            )


class GovernanceIntegrityAuditExportService:
    """
    Builds portable evidence bundles from audit history.
    """

    def __init__(
        self,
        *,
        repository: GovernanceIntegrityAuditHistoryRepository,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def build_bundle(
        self,
        options: GovernanceIntegrityAuditExportOptions | None = None,
    ) -> GovernanceIntegrityAuditEvidenceBundle:
        options = options or GovernanceIntegrityAuditExportOptions()

        records = (
            self._repository.list(limit=options.limit)
            if options.limit is not None
            else self._repository.list()
        )

        healthy_audits = sum(
            1 for record in records if record.healthy
        )

        summary = GovernanceIntegrityAuditExportSummary(
            total_audits=len(records),
            healthy_audits=healthy_audits,
            unhealthy_audits=len(records) - healthy_audits,
            newest_audit_id=(
                None if not records else records[0].audit_id
            ),
            oldest_audit_id=(
                None if not records else records[-1].audit_id
            ),
        )

        # Trend and regression are derived only from the records selected
        # for this bundle (never a fresh repository query), so the bundle
        # stays self-consistent: sample_size can never exceed record_count.
        trend = (
            analyze_governance_integrity_audit_records(
                records[: options.trend_window]
            )
            if options.include_trend
            else None
        )

        regression = (
            detect_governance_integrity_regression(records[:2])
            if options.include_regression
            else None
        )

        return GovernanceIntegrityAuditEvidenceBundle(
            schema_version=GOVERNANCE_AUDIT_EVIDENCE_SCHEMA_VERSION,
            exported_at=self._clock(),
            record_count=len(records),
            records=records,
            summary=summary,
            trend=trend,
            regression=regression,
        )

    def export_to_file(
        self,
        output_path: str | Path,
        *,
        options: GovernanceIntegrityAuditExportOptions | None = None,
        pretty: bool = True,
        overwrite: bool = False,
    ) -> GovernanceIntegrityAuditEvidenceExportResult:
        options = options or GovernanceIntegrityAuditExportOptions()

        path = Path(output_path)

        manifest_path = (
            default_governance_audit_evidence_manifest_path(path)
        )

        # Preflight both destinations before writing anything: failing
        # after the evidence file is already written (because only the
        # manifest path collided) would leave a partially completed
        # export.
        if path.exists() and not overwrite:
            raise FileExistsError(
                f"output file already exists: {path}"
            )

        if (
            options.create_manifest
            and manifest_path.exists()
            and not overwrite
        ):
            raise FileExistsError(
                f"manifest file already exists: {manifest_path}"
            )

        bundle = self.build_bundle(options)

        payload = serialize_governance_integrity_audit_evidence(
            bundle, pretty=pretty
        )

        path.parent.mkdir(parents=True, exist_ok=True)

        path.write_text(payload + "\n", encoding="utf-8")

        manifest = None
        written_manifest_path = None

        if options.create_manifest:
            manifest = build_governance_audit_evidence_manifest(
                evidence_path=path,
                record_count=bundle.record_count,
                exported_at=bundle.exported_at,
            )

            written_manifest_path = (
                write_governance_audit_evidence_manifest(
                    manifest,
                    manifest_path,
                    pretty=pretty,
                    overwrite=overwrite,
                )
            )

        return GovernanceIntegrityAuditEvidenceExportResult(
            bundle=bundle,
            evidence_path=path,
            manifest=manifest,
            manifest_path=written_manifest_path,
        )
