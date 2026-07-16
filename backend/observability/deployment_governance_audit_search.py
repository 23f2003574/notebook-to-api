from __future__ import annotations

from dataclasses import dataclass

from .deployment_governance_audit_bookmarks import (
    GovernanceIntegrityAuditBookmarkRepository,
)
from .deployment_governance_audit_history import (
    GovernanceIntegrityAuditHistoryRepository,
    GovernanceIntegrityAuditRecord,
)
from .deployment_governance_audit_labels import (
    GovernanceIntegrityAuditLabelRepository,
)


@dataclass(frozen=True)
class GovernanceIntegrityAuditSearchQuery:
    """
    Filter criteria for searching recorded governance integrity audits.

    All specified filters are combined with AND; none of them do fuzzy
    matching.
    """

    audit_id: str | None = None

    healthy: bool | None = None

    label: str | None = None

    bookmark: str | None = None

    def __post_init__(self) -> None:
        if (
            self.audit_id is None
            and self.healthy is None
            and self.label is None
            and self.bookmark is None
        ):
            raise ValueError(
                "at least one search filter must be specified"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "audit_id": self.audit_id,
            "healthy": self.healthy,
            "label": self.label,
            "bookmark": self.bookmark,
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, object],
    ) -> "GovernanceIntegrityAuditSearchQuery":
        return cls(
            audit_id=data.get("audit_id"),
            healthy=data.get("healthy"),
            label=data.get("label"),
            bookmark=data.get("bookmark"),
        )


class GovernanceIntegrityAuditSearchService:
    """
    Filters recorded governance integrity audits across audit history,
    labels, and bookmarks.
    """

    def __init__(
        self,
        history_repository: GovernanceIntegrityAuditHistoryRepository,
        label_repository: GovernanceIntegrityAuditLabelRepository,
        bookmark_repository: GovernanceIntegrityAuditBookmarkRepository,
    ) -> None:
        self._history_repository = history_repository

        self._label_repository = label_repository

        self._bookmark_repository = bookmark_repository

    def search(
        self,
        query: GovernanceIntegrityAuditSearchQuery,
    ) -> tuple[
        GovernanceIntegrityAuditRecord,
        ...
    ]:
        """
        Return audits matching every specified filter, preserving the
        repository's newest-to-oldest ordering.
        """

        records = self._history_repository.list()

        if query.audit_id is not None:
            records = tuple(
                record
                for record in records
                if record.audit_id == query.audit_id
            )

        if query.healthy is not None:
            records = tuple(
                record
                for record in records
                if record.healthy == query.healthy
            )

        if query.label is not None:
            labeled_audit_ids = set(
                self._label_repository.audits(query.label)
            )

            records = tuple(
                record
                for record in records
                if record.audit_id in labeled_audit_ids
            )

        if query.bookmark is not None:
            bookmark = self._bookmark_repository.get(query.bookmark)

            bookmarked_audit_id = (
                bookmark.audit_id if bookmark is not None else None
            )

            records = tuple(
                record
                for record in records
                if record.audit_id == bookmarked_audit_id
            )

        return records
