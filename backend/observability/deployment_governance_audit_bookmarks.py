from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Callable, Protocol, runtime_checkable

from .deployment_governance_audit_history import (
    GovernanceIntegrityAuditHistoryRepository,
)


@dataclass(frozen=True)
class GovernanceIntegrityAuditBookmark:
    """
    A named pointer to one recorded governance integrity audit.

    Bookmarks are separate metadata layered on top of audit history for
    quick navigation; they never modify the audit record they point to.
    """

    name: str

    audit_id: str

    created_at: datetime

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError(
                "name must not be empty"
            )

        if not self.audit_id.strip():
            raise ValueError(
                "audit_id must not be empty"
            )

        if self.created_at.tzinfo is None:
            raise ValueError(
                "created_at must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "audit_id": self.audit_id,
            "created_at": self.created_at.isoformat(),
        }


class GovernanceIntegrityAuditBookmarkError(
    RuntimeError
):
    """
    Base error for governance audit bookmark persistence failures.
    """


class GovernanceIntegrityAuditBookmarkAlreadyExistsError(
    GovernanceIntegrityAuditBookmarkError
):
    """
    Raised when a bookmark with the same name already exists.
    """


@runtime_checkable
class GovernanceIntegrityAuditBookmarkRepository(Protocol):
    """
    Persistence contract for named governance integrity audit bookmarks.
    """

    def save(
        self,
        bookmark: GovernanceIntegrityAuditBookmark,
    ) -> GovernanceIntegrityAuditBookmark:
        """
        Persist one bookmark. Raises if the name already exists.
        """

    def get(
        self,
        name: str,
    ) -> GovernanceIntegrityAuditBookmark | None:
        """
        Return one bookmark by name, or None if it does not exist.
        """

    def delete(
        self,
        name: str,
    ) -> None:
        """
        Delete one bookmark by name. Raises KeyError if it does not exist.
        """

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityAuditBookmark,
        ...
    ]:
        """
        Return every bookmark, ordered by name.
        """

    def exists(
        self,
        name: str,
    ) -> bool:
        """
        Return whether a bookmark with this name exists.
        """


class InMemoryGovernanceIntegrityAuditBookmarkRepository:
    """
    Thread-safe in-memory implementation of governance audit bookmark
    storage.
    """

    def __init__(self) -> None:
        self._bookmarks: dict[
            str,
            GovernanceIntegrityAuditBookmark,
        ] = {}

        self._lock = RLock()

    def save(
        self,
        bookmark: GovernanceIntegrityAuditBookmark,
    ) -> GovernanceIntegrityAuditBookmark:
        with self._lock:
            if bookmark.name in self._bookmarks:
                raise GovernanceIntegrityAuditBookmarkAlreadyExistsError(
                    f"governance audit bookmark '{bookmark.name}' "
                    "already exists"
                )

            self._bookmarks[bookmark.name] = bookmark

            return bookmark

    def get(
        self,
        name: str,
    ) -> GovernanceIntegrityAuditBookmark | None:
        normalized_name = self._normalize_name(name)

        with self._lock:
            return self._bookmarks.get(normalized_name)

    def delete(
        self,
        name: str,
    ) -> None:
        normalized_name = self._normalize_name(name)

        with self._lock:
            if normalized_name not in self._bookmarks:
                raise KeyError(
                    f"governance audit bookmark '{normalized_name}' "
                    "was not found"
                )

            del self._bookmarks[normalized_name]

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityAuditBookmark,
        ...
    ]:
        with self._lock:
            return tuple(
                sorted(
                    self._bookmarks.values(),
                    key=lambda bookmark: bookmark.name,
                )
            )

    def exists(
        self,
        name: str,
    ) -> bool:
        normalized_name = self._normalize_name(name)

        with self._lock:
            return normalized_name in self._bookmarks

    @staticmethod
    def _normalize_name(name: str) -> str:
        normalized_name = name.strip()

        if not normalized_name:
            raise ValueError(
                "name must not be empty"
            )

        return normalized_name


class GovernanceIntegrityAuditBookmarkService:
    """
    Creates and manages named bookmarks for recorded governance integrity
    audits.
    """

    def __init__(
        self,
        bookmark_repository: GovernanceIntegrityAuditBookmarkRepository,
        history_repository: GovernanceIntegrityAuditHistoryRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._bookmark_repository = bookmark_repository

        self._history_repository = history_repository

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

    def create(
        self,
        name: str,
        audit_id: str,
    ) -> GovernanceIntegrityAuditBookmark:
        """
        Create a named bookmark pointing to an existing audit.

        Raises LookupError if the audit does not exist, and ValueError if
        a bookmark with this name already exists.
        """

        if self._history_repository.get_by_audit_id(audit_id) is None:
            raise LookupError(
                f"governance integrity audit '{audit_id}' was not found"
            )

        if self._bookmark_repository.exists(name):
            raise ValueError(
                f"bookmark '{name}' already exists"
            )

        bookmark = GovernanceIntegrityAuditBookmark(
            name=name,
            audit_id=audit_id,
            created_at=self._clock(),
        )

        return self._bookmark_repository.save(bookmark)

    def bookmark_latest(
        self,
        name: str,
    ) -> GovernanceIntegrityAuditBookmark:
        """
        Create a named bookmark pointing to the most recently started
        audit.

        Raises LookupError if no audits have been recorded.
        """

        record = self._history_repository.latest()

        if record is None:
            raise LookupError(
                "no governance integrity audits have been recorded"
            )

        return self.create(name, record.audit_id)

    def get(
        self,
        name: str,
    ) -> GovernanceIntegrityAuditBookmark | None:
        return self._bookmark_repository.get(name)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityAuditBookmark,
        ...
    ]:
        return self._bookmark_repository.list()

    def delete(
        self,
        name: str,
    ) -> None:
        """
        Delete a bookmark by name. Raises KeyError if it does not exist.
        """

        self._bookmark_repository.delete(name)
