from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from typing import TYPE_CHECKING, Callable, Protocol, runtime_checkable

from .deployment_governance_audit_reports import (
    GovernanceIntegrityAuditReport,
    GovernanceIntegrityAuditReportService,
)

if TYPE_CHECKING:
    from .deployment_governance_audit_collections import (
        GovernanceIntegrityAuditCollectionService,
    )
    from .deployment_governance_audit_saved_queries import (
        GovernanceIntegritySavedAuditQueryService,
    )


_VALID_OUTPUT_FORMATS = (
    "json",
    "markdown",
)


class GovernanceIntegrityAuditReportSource(
    str,
    Enum,
):
    """
    What a report template's audit selection is derived from.
    """

    COLLECTION = "collection"

    SAVED_QUERY = "saved_query"


@dataclass(frozen=True)
class GovernanceIntegrityAuditReportTemplate:
    """
    A named, reusable report configuration: a title, a collection or
    saved query to source audits from, and an output format.
    """

    name: str

    title: str

    source: GovernanceIntegrityAuditReportSource

    source_name: str

    output_format: str

    created_at: datetime

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError(
                "name must not be empty"
            )

        if not self.title.strip():
            raise ValueError(
                "title must not be empty"
            )

        if not self.source_name.strip():
            raise ValueError(
                "source_name must not be empty"
            )

        if self.output_format not in _VALID_OUTPUT_FORMATS:
            raise ValueError(
                "output_format must be one of: "
                f"{', '.join(_VALID_OUTPUT_FORMATS)}"
            )

        if self.created_at.tzinfo is None:
            raise ValueError(
                "created_at must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "title": self.title,
            "source": self.source.value,
            "source_name": self.source_name,
            "output_format": self.output_format,
            "created_at": self.created_at.isoformat(),
        }


class GovernanceIntegrityAuditReportTemplateError(
    RuntimeError
):
    """
    Base error for governance audit report template persistence
    failures.
    """


class GovernanceIntegrityAuditReportTemplateAlreadyExistsError(
    GovernanceIntegrityAuditReportTemplateError
):
    """
    Raised when a template with the same name already exists.
    """


@runtime_checkable
class GovernanceIntegrityAuditReportTemplateRepository(Protocol):
    """
    Persistence contract for named, reusable governance audit report
    templates.
    """

    def save(
        self,
        template: GovernanceIntegrityAuditReportTemplate,
    ) -> GovernanceIntegrityAuditReportTemplate:
        """
        Persist one template. Raises if the name already exists.
        """

    def get(
        self,
        name: str,
    ) -> GovernanceIntegrityAuditReportTemplate | None:
        """
        Return one template by name, or None if it does not exist.
        """

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityAuditReportTemplate,
        ...
    ]:
        """
        Return every template, ordered by name.
        """

    def delete(
        self,
        name: str,
    ) -> None:
        """
        Delete one template by name. Raises KeyError if it does not
        exist.
        """

    def exists(
        self,
        name: str,
    ) -> bool:
        """
        Return whether a template with this name exists.
        """


class InMemoryGovernanceIntegrityAuditReportTemplateRepository:
    """
    Thread-safe in-memory implementation of governance audit report
    template storage.
    """

    def __init__(self) -> None:
        self._templates: dict[
            str,
            GovernanceIntegrityAuditReportTemplate,
        ] = {}

        self._lock = RLock()

    def save(
        self,
        template: GovernanceIntegrityAuditReportTemplate,
    ) -> GovernanceIntegrityAuditReportTemplate:
        with self._lock:
            if template.name in self._templates:
                raise (
                    GovernanceIntegrityAuditReportTemplateAlreadyExistsError(
                        f"report template '{template.name}' "
                        "already exists"
                    )
                )

            self._templates[template.name] = template

            return template

    def get(
        self,
        name: str,
    ) -> GovernanceIntegrityAuditReportTemplate | None:
        normalized_name = self._normalize_name(name)

        with self._lock:
            return self._templates.get(normalized_name)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityAuditReportTemplate,
        ...
    ]:
        with self._lock:
            return tuple(
                sorted(
                    self._templates.values(),
                    key=lambda template: template.name,
                )
            )

    def delete(
        self,
        name: str,
    ) -> None:
        normalized_name = self._normalize_name(name)

        with self._lock:
            if normalized_name not in self._templates:
                raise KeyError(
                    f"report template '{normalized_name}' "
                    "was not found"
                )

            del self._templates[normalized_name]

    def exists(
        self,
        name: str,
    ) -> bool:
        normalized_name = self._normalize_name(name)

        with self._lock:
            return normalized_name in self._templates

    @staticmethod
    def _normalize_name(name: str) -> str:
        normalized_name = name.strip()

        if not normalized_name:
            raise ValueError(
                "name must not be empty"
            )

        return normalized_name


class GovernanceIntegrityAuditReportTemplateService:
    """
    Creates, manages, and generates reports from reusable governance
    audit report templates.
    """

    def __init__(
        self,
        repository: GovernanceIntegrityAuditReportTemplateRepository,
        report_service: GovernanceIntegrityAuditReportService,
        collection_service: "GovernanceIntegrityAuditCollectionService",
        saved_query_service: "GovernanceIntegritySavedAuditQueryService",
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository

        self._report_service = report_service

        self._collection_service = collection_service

        self._saved_query_service = saved_query_service

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

    def create(
        self,
        name: str,
        title: str,
        source: GovernanceIntegrityAuditReportSource,
        source_name: str,
        output_format: str,
    ) -> GovernanceIntegrityAuditReportTemplate:
        """
        Create a new, uniquely named report template.

        Raises ValueError if a template with this name already exists,
        and LookupError if the referenced collection or saved query
        does not exist.
        """

        if self._repository.exists(name):
            raise ValueError(
                f"report template '{name}' already exists"
            )

        self._require_source(source, source_name)

        template = GovernanceIntegrityAuditReportTemplate(
            name=name,
            title=title,
            source=source,
            source_name=source_name,
            output_format=output_format,
            created_at=self._clock(),
        )

        return self._repository.save(template)

    def generate(
        self,
        name: str,
    ) -> GovernanceIntegrityAuditReport:
        """
        Load a template by name, resolve its source, and generate a
        fresh report.

        Raises KeyError if the template does not exist.
        """

        template = self._repository.get(name)

        if template is None:
            raise KeyError(
                f"report template '{name}' was not found"
            )

        if template.source is GovernanceIntegrityAuditReportSource.COLLECTION:
            return self._report_service.report_from_collection(
                template.source_name, title=template.title
            )

        matching_records = self._saved_query_service.execute(
            template.source_name
        )

        return self._report_service.report_from_audits(
            template.title,
            [record.audit_id for record in matching_records],
        )

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityAuditReportTemplate,
        ...
    ]:
        return self._repository.list()

    def get(
        self,
        name: str,
    ) -> GovernanceIntegrityAuditReportTemplate | None:
        return self._repository.get(name)

    def delete(
        self,
        name: str,
    ) -> None:
        """
        Delete a template by name. Raises KeyError if it does not
        exist.
        """

        self._repository.delete(name)

    def _require_source(
        self,
        source: GovernanceIntegrityAuditReportSource,
        source_name: str,
    ) -> None:
        if source is GovernanceIntegrityAuditReportSource.COLLECTION:
            if self._collection_service.get(source_name) is None:
                raise LookupError(
                    f"collection '{source_name}' was not found"
                )

            return

        if self._saved_query_service.get(source_name) is None:
            raise LookupError(
                f"saved query '{source_name}' was not found"
            )
