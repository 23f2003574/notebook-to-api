from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Callable, Protocol, runtime_checkable

from .deployment_governance_audit_history import (
    GovernanceIntegrityAuditRecord,
)
from .deployment_governance_audit_search import (
    GovernanceIntegrityAuditSearchQuery,
    GovernanceIntegrityAuditSearchService,
)


@dataclass(frozen=True)
class GovernanceIntegritySavedAuditQuery:
    """
    A named, reusable governance audit search filter.

    Independent metadata layered on top of search: saving a query never
    executes it and never mutates audit history, labels, or bookmarks.
    """

    name: str

    query: GovernanceIntegrityAuditSearchQuery

    created_at: datetime

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError(
                "name must not be empty"
            )

        if self.created_at.tzinfo is None:
            raise ValueError(
                "created_at must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "query": self.query.to_dict(),
            "created_at": self.created_at.isoformat(),
        }


class GovernanceIntegritySavedAuditQueryError(
    RuntimeError
):
    """
    Base error for saved governance audit query persistence failures.
    """


class GovernanceIntegritySavedAuditQueryAlreadyExistsError(
    GovernanceIntegritySavedAuditQueryError
):
    """
    Raised when a saved query with the same name already exists.
    """


@runtime_checkable
class GovernanceIntegritySavedAuditQueryRepository(Protocol):
    """
    Persistence contract for named, reusable governance audit search
    queries.
    """

    def save(
        self,
        saved_query: GovernanceIntegritySavedAuditQuery,
    ) -> GovernanceIntegritySavedAuditQuery:
        """
        Persist one saved query. Raises if the name already exists.
        """

    def get(
        self,
        name: str,
    ) -> GovernanceIntegritySavedAuditQuery | None:
        """
        Return one saved query by name, or None if it does not exist.
        """

    def list(
        self,
    ) -> tuple[
        GovernanceIntegritySavedAuditQuery,
        ...
    ]:
        """
        Return every saved query, ordered by name.
        """

    def delete(
        self,
        name: str,
    ) -> None:
        """
        Delete one saved query by name. Raises KeyError if it does not
        exist.
        """

    def exists(
        self,
        name: str,
    ) -> bool:
        """
        Return whether a saved query with this name exists.
        """


class InMemoryGovernanceIntegritySavedAuditQueryRepository:
    """
    Thread-safe in-memory implementation of saved governance audit query
    storage.
    """

    def __init__(self) -> None:
        self._saved_queries: dict[
            str,
            GovernanceIntegritySavedAuditQuery,
        ] = {}

        self._lock = RLock()

    def save(
        self,
        saved_query: GovernanceIntegritySavedAuditQuery,
    ) -> GovernanceIntegritySavedAuditQuery:
        with self._lock:
            if saved_query.name in self._saved_queries:
                raise (
                    GovernanceIntegritySavedAuditQueryAlreadyExistsError(
                        f"saved query '{saved_query.name}' "
                        "already exists"
                    )
                )

            self._saved_queries[saved_query.name] = saved_query

            return saved_query

    def get(
        self,
        name: str,
    ) -> GovernanceIntegritySavedAuditQuery | None:
        normalized_name = self._normalize_name(name)

        with self._lock:
            return self._saved_queries.get(normalized_name)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegritySavedAuditQuery,
        ...
    ]:
        with self._lock:
            return tuple(
                sorted(
                    self._saved_queries.values(),
                    key=lambda saved_query: saved_query.name,
                )
            )

    def delete(
        self,
        name: str,
    ) -> None:
        normalized_name = self._normalize_name(name)

        with self._lock:
            if normalized_name not in self._saved_queries:
                raise KeyError(
                    f"saved query '{normalized_name}' was not found"
                )

            del self._saved_queries[normalized_name]

    def exists(
        self,
        name: str,
    ) -> bool:
        normalized_name = self._normalize_name(name)

        with self._lock:
            return normalized_name in self._saved_queries

    @staticmethod
    def _normalize_name(name: str) -> str:
        normalized_name = name.strip()

        if not normalized_name:
            raise ValueError(
                "name must not be empty"
            )

        return normalized_name


class GovernanceIntegritySavedAuditQueryService:
    """
    Saves, executes, and manages reusable governance audit search
    filters.
    """

    def __init__(
        self,
        repository: GovernanceIntegritySavedAuditQueryRepository,
        search_service: GovernanceIntegrityAuditSearchService,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository

        self._search_service = search_service

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

    def save(
        self,
        name: str,
        query: GovernanceIntegrityAuditSearchQuery,
    ) -> GovernanceIntegritySavedAuditQuery:
        """
        Save a reusable search query under a unique name.

        Raises ValueError if a saved query with this name already
        exists.
        """

        if self._repository.exists(name):
            raise ValueError(
                f"saved query '{name}' already exists"
            )

        saved_query = GovernanceIntegritySavedAuditQuery(
            name=name,
            query=query,
            created_at=self._clock(),
        )

        return self._repository.save(saved_query)

    def execute(
        self,
        name: str,
    ) -> tuple[
        GovernanceIntegrityAuditRecord,
        ...
    ]:
        """
        Load a saved query by name and run it against current audit
        history, labels, and bookmarks.

        Raises KeyError if the saved query does not exist.
        """

        saved_query = self._repository.get(name)

        if saved_query is None:
            raise KeyError(
                f"saved query '{name}' was not found"
            )

        return self._search_service.search(saved_query.query)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegritySavedAuditQuery,
        ...
    ]:
        return self._repository.list()

    def get(
        self,
        name: str,
    ) -> GovernanceIntegritySavedAuditQuery | None:
        return self._repository.get(name)

    def delete(
        self,
        name: str,
    ) -> None:
        """
        Delete a saved query by name. Raises KeyError if it does not
        exist.
        """

        self._repository.delete(name)
