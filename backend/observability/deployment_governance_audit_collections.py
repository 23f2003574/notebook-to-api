from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Callable, Protocol, runtime_checkable

from .deployment_governance_audit_history import (
    GovernanceIntegrityAuditHistoryRepository,
)


@dataclass(frozen=True)
class GovernanceIntegrityAuditCollection:
    """
    A named, explicit group of governance integrity audits (e.g. a
    release, an incident, a migration, an investigation).

    Unlike a saved query (which stores reusable filter criteria and is
    re-evaluated on every run), a collection stores explicit membership:
    which specific audits belong to it, decided by the operator rather
    than derived from a filter.
    """

    name: str

    description: str | None

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
            "description": self.description,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class GovernanceIntegrityAuditCollectionEntry:
    """
    One audit's membership in one collection.
    """

    collection: str

    audit_id: str

    added_at: datetime

    def __post_init__(self) -> None:
        if not self.collection.strip():
            raise ValueError(
                "collection must not be empty"
            )

        if not self.audit_id.strip():
            raise ValueError(
                "audit_id must not be empty"
            )

        if self.added_at.tzinfo is None:
            raise ValueError(
                "added_at must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "collection": self.collection,
            "audit_id": self.audit_id,
            "added_at": self.added_at.isoformat(),
        }


class GovernanceIntegrityAuditCollectionError(
    RuntimeError
):
    """
    Base error for governance audit collection persistence failures.
    """


class GovernanceIntegrityAuditCollectionAlreadyExistsError(
    GovernanceIntegrityAuditCollectionError
):
    """
    Raised when a collection with the same name already exists.
    """


class GovernanceIntegrityAuditCollectionEntryAlreadyExistsError(
    GovernanceIntegrityAuditCollectionError
):
    """
    Raised when an audit is already a member of a collection.
    """


@runtime_checkable
class GovernanceIntegrityAuditCollectionRepository(Protocol):
    """
    Persistence contract for governance audit collections and their
    membership.
    """

    def create(
        self,
        collection: GovernanceIntegrityAuditCollection,
    ) -> GovernanceIntegrityAuditCollection:
        """
        Persist one collection. Raises if the name already exists.
        """

    def delete(
        self,
        name: str,
    ) -> None:
        """
        Delete one collection and all of its entries. Raises KeyError if
        it does not exist.
        """

    def get(
        self,
        name: str,
    ) -> GovernanceIntegrityAuditCollection | None:
        """
        Return one collection by name, or None if it does not exist.
        """

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityAuditCollection,
        ...
    ]:
        """
        Return every collection, ordered by name.
        """

    def add_audit(
        self,
        collection: str,
        audit_id: str,
        *,
        added_at: datetime | None = None,
    ) -> GovernanceIntegrityAuditCollectionEntry:
        """
        Add one audit to one collection. Raises if it is already a
        member.
        """

    def remove_audit(
        self,
        collection: str,
        audit_id: str,
    ) -> None:
        """
        Remove one audit from one collection. Raises KeyError if it is
        not a member.
        """

    def audits(
        self,
        collection: str,
    ) -> tuple[str, ...]:
        """
        Return every audit identifier in one collection, newest to
        oldest.
        """

    def collections(
        self,
        audit_id: str,
    ) -> tuple[str, ...]:
        """
        Return every collection name containing one audit, newest to
        oldest.
        """


class InMemoryGovernanceIntegrityAuditCollectionRepository:
    """
    Thread-safe in-memory implementation of governance audit collection
    storage.
    """

    def __init__(self) -> None:
        self._collections: dict[
            str,
            GovernanceIntegrityAuditCollection,
        ] = {}

        self._entries: dict[
            tuple[str, str],
            GovernanceIntegrityAuditCollectionEntry,
        ] = {}

        self._lock = RLock()

    def create(
        self,
        collection: GovernanceIntegrityAuditCollection,
    ) -> GovernanceIntegrityAuditCollection:
        with self._lock:
            if collection.name in self._collections:
                raise (
                    GovernanceIntegrityAuditCollectionAlreadyExistsError(
                        f"collection '{collection.name}' already exists"
                    )
                )

            self._collections[collection.name] = collection

            return collection

    def delete(
        self,
        name: str,
    ) -> None:
        normalized_name = self._normalize(name, "name")

        with self._lock:
            if normalized_name not in self._collections:
                raise KeyError(
                    f"collection '{normalized_name}' was not found"
                )

            del self._collections[normalized_name]

            for key in tuple(self._entries):
                if key[0] == normalized_name:
                    del self._entries[key]

    def get(
        self,
        name: str,
    ) -> GovernanceIntegrityAuditCollection | None:
        normalized_name = self._normalize(name, "name")

        with self._lock:
            return self._collections.get(normalized_name)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityAuditCollection,
        ...
    ]:
        with self._lock:
            return tuple(
                sorted(
                    self._collections.values(),
                    key=lambda collection: collection.name,
                )
            )

    def add_audit(
        self,
        collection: str,
        audit_id: str,
        *,
        added_at: datetime | None = None,
    ) -> GovernanceIntegrityAuditCollectionEntry:
        normalized_collection = self._normalize(
            collection, "collection"
        )

        normalized_audit_id = self._normalize(audit_id, "audit_id")

        entry = GovernanceIntegrityAuditCollectionEntry(
            collection=normalized_collection,
            audit_id=normalized_audit_id,
            added_at=added_at or datetime.now(timezone.utc),
        )

        key = (normalized_collection, normalized_audit_id)

        with self._lock:
            if key in self._entries:
                raise (
                    GovernanceIntegrityAuditCollectionEntryAlreadyExistsError(
                        f"audit '{normalized_audit_id}' is already in "
                        f"collection '{normalized_collection}'"
                    )
                )

            self._entries[key] = entry

            return entry

    def remove_audit(
        self,
        collection: str,
        audit_id: str,
    ) -> None:
        normalized_collection = self._normalize(
            collection, "collection"
        )

        normalized_audit_id = self._normalize(audit_id, "audit_id")

        key = (normalized_collection, normalized_audit_id)

        with self._lock:
            if key not in self._entries:
                raise KeyError(
                    f"audit '{normalized_audit_id}' is not in "
                    f"collection '{normalized_collection}'"
                )

            del self._entries[key]

    def audits(
        self,
        collection: str,
    ) -> tuple[str, ...]:
        normalized_collection = self._normalize(
            collection, "collection"
        )

        with self._lock:
            matches = [
                entry
                for entry in self._entries.values()
                if entry.collection == normalized_collection
            ]

        matches.sort(key=self._entry_sort_key, reverse=True)

        return tuple(entry.audit_id for entry in matches)

    def collections(
        self,
        audit_id: str,
    ) -> tuple[str, ...]:
        normalized_audit_id = self._normalize(audit_id, "audit_id")

        with self._lock:
            matches = [
                entry
                for entry in self._entries.values()
                if entry.audit_id == normalized_audit_id
            ]

        matches.sort(key=self._entry_sort_key, reverse=True)

        return tuple(entry.collection for entry in matches)

    @staticmethod
    def _normalize(value: str, field_name: str) -> str:
        normalized_value = value.strip()

        if not normalized_value:
            raise ValueError(
                f"{field_name} must not be empty"
            )

        return normalized_value

    @staticmethod
    def _entry_sort_key(
        entry: GovernanceIntegrityAuditCollectionEntry,
    ) -> tuple[datetime, str, str]:
        return (
            entry.added_at,
            entry.collection,
            entry.audit_id,
        )


class GovernanceIntegrityAuditCollectionService:
    """
    Creates, deletes, and manages membership for governance audit
    collections.
    """

    def __init__(
        self,
        collection_repository: GovernanceIntegrityAuditCollectionRepository,
        history_repository: GovernanceIntegrityAuditHistoryRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._collection_repository = collection_repository

        self._history_repository = history_repository

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

    def create(
        self,
        name: str,
        description: str | None = None,
    ) -> GovernanceIntegrityAuditCollection:
        """
        Create a new, uniquely named collection.

        Raises ValueError if a collection with this name already
        exists.
        """

        if self._collection_repository.get(name) is not None:
            raise ValueError(
                f"collection '{name}' already exists"
            )

        collection = GovernanceIntegrityAuditCollection(
            name=name,
            description=description,
            created_at=self._clock(),
        )

        return self._collection_repository.create(collection)

    def delete(
        self,
        name: str,
    ) -> None:
        """
        Delete a collection and every entry in it. Raises KeyError if it
        does not exist.
        """

        self._collection_repository.delete(name)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityAuditCollection,
        ...
    ]:
        return self._collection_repository.list()

    def get(
        self,
        name: str,
    ) -> GovernanceIntegrityAuditCollection | None:
        return self._collection_repository.get(name)

    def add(
        self,
        collection: str,
        audit_id: str,
    ) -> GovernanceIntegrityAuditCollectionEntry:
        """
        Add an existing audit to an existing collection.

        Raises LookupError if the collection or the audit does not
        exist, and ValueError if the audit is already a member of the
        collection.
        """

        if self._collection_repository.get(collection) is None:
            raise LookupError(
                f"collection '{collection}' was not found"
            )

        if self._history_repository.get_by_audit_id(audit_id) is None:
            raise LookupError(
                f"governance integrity audit '{audit_id}' was not found"
            )

        if audit_id in self._collection_repository.audits(collection):
            raise ValueError(
                f"audit '{audit_id}' is already in "
                f"collection '{collection}'"
            )

        return self._collection_repository.add_audit(
            collection, audit_id, added_at=self._clock()
        )

    def remove(
        self,
        collection: str,
        audit_id: str,
    ) -> None:
        """
        Remove an audit from a collection. Raises KeyError if it is not
        a member.
        """

        self._collection_repository.remove_audit(collection, audit_id)

    def audits(
        self,
        collection: str,
    ) -> tuple[str, ...]:
        return self._collection_repository.audits(collection)

    def collections(
        self,
        audit_id: str,
    ) -> tuple[str, ...]:
        return self._collection_repository.collections(audit_id)
