from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Callable, Protocol, runtime_checkable

from .deployment_governance_audit_history import (
    GovernanceIntegrityAuditHistoryRepository,
)


@dataclass(frozen=True)
class GovernanceIntegrityAuditLabel:
    """
    One label applied to a recorded governance integrity audit.

    Unlike a bookmark (a unique named pointer to one specific audit),
    labels are many-to-many: the same label can be applied to many
    audits, and the same audit can carry many labels, for search,
    filtering, and organization. Labels are independent of audit history
    itself and never modify the audit record they annotate.
    """

    audit_id: str

    label: str

    created_at: datetime

    def __post_init__(self) -> None:
        if not self.audit_id.strip():
            raise ValueError(
                "audit_id must not be empty"
            )

        if not self.label.strip():
            raise ValueError(
                "label must not be empty"
            )

        if self.created_at.tzinfo is None:
            raise ValueError(
                "created_at must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "audit_id": self.audit_id,
            "label": self.label,
            "created_at": self.created_at.isoformat(),
        }


class GovernanceIntegrityAuditLabelError(
    RuntimeError
):
    """
    Base error for governance audit label persistence failures.
    """


class GovernanceIntegrityAuditLabelAlreadyExistsError(
    GovernanceIntegrityAuditLabelError
):
    """
    Raised when a label is already applied to an audit.
    """


@runtime_checkable
class GovernanceIntegrityAuditLabelRepository(Protocol):
    """
    Persistence contract for governance integrity audit labels.
    """

    def add(
        self,
        label: GovernanceIntegrityAuditLabel,
    ) -> GovernanceIntegrityAuditLabel:
        """
        Persist one label. Raises if this (audit_id, label) pair
        already exists.
        """

    def remove(
        self,
        audit_id: str,
        label: str,
    ) -> None:
        """
        Remove one label from one audit. Raises KeyError if it does not
        exist.
        """

    def labels(
        self,
        audit_id: str,
    ) -> tuple[str, ...]:
        """
        Return every label applied to one audit, newest to oldest.
        """

    def audits(
        self,
        label: str,
    ) -> tuple[str, ...]:
        """
        Return every audit identifier carrying this label, newest to
        oldest.
        """

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityAuditLabel,
        ...
    ]:
        """
        Return every label association, newest to oldest.
        """


class InMemoryGovernanceIntegrityAuditLabelRepository:
    """
    Thread-safe in-memory implementation of governance audit label
    storage.
    """

    def __init__(self) -> None:
        self._labels: dict[
            tuple[str, str],
            GovernanceIntegrityAuditLabel,
        ] = {}

        self._lock = RLock()

    def add(
        self,
        label: GovernanceIntegrityAuditLabel,
    ) -> GovernanceIntegrityAuditLabel:
        key = (label.audit_id, label.label)

        with self._lock:
            if key in self._labels:
                raise GovernanceIntegrityAuditLabelAlreadyExistsError(
                    f"label '{label.label}' is already applied to "
                    f"audit '{label.audit_id}'"
                )

            self._labels[key] = label

            return label

    def remove(
        self,
        audit_id: str,
        label: str,
    ) -> None:
        normalized_audit_id = self._normalize(audit_id, "audit_id")
        normalized_label = self._normalize(label, "label")

        key = (normalized_audit_id, normalized_label)

        with self._lock:
            if key not in self._labels:
                raise KeyError(
                    f"label '{normalized_label}' was not found on "
                    f"audit '{normalized_audit_id}'"
                )

            del self._labels[key]

    def labels(
        self,
        audit_id: str,
    ) -> tuple[str, ...]:
        normalized_audit_id = self._normalize(audit_id, "audit_id")

        with self._lock:
            matches = [
                record
                for record in self._labels.values()
                if record.audit_id == normalized_audit_id
            ]

        matches.sort(key=self._sort_key, reverse=True)

        return tuple(record.label for record in matches)

    def audits(
        self,
        label: str,
    ) -> tuple[str, ...]:
        normalized_label = self._normalize(label, "label")

        with self._lock:
            matches = [
                record
                for record in self._labels.values()
                if record.label == normalized_label
            ]

        matches.sort(key=self._sort_key, reverse=True)

        return tuple(record.audit_id for record in matches)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityAuditLabel,
        ...
    ]:
        with self._lock:
            matches = list(self._labels.values())

        matches.sort(key=self._sort_key, reverse=True)

        return tuple(matches)

    @staticmethod
    def _normalize(value: str, field_name: str) -> str:
        normalized_value = value.strip()

        if not normalized_value:
            raise ValueError(
                f"{field_name} must not be empty"
            )

        return normalized_value

    @staticmethod
    def _sort_key(
        record: GovernanceIntegrityAuditLabel,
    ) -> tuple[datetime, str, str]:
        return (
            record.created_at,
            record.audit_id,
            record.label,
        )


class GovernanceIntegrityAuditLabelService:
    """
    Applies, removes, and queries labels on recorded governance integrity
    audits.
    """

    def __init__(
        self,
        label_repository: GovernanceIntegrityAuditLabelRepository,
        history_repository: GovernanceIntegrityAuditHistoryRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._label_repository = label_repository

        self._history_repository = history_repository

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

    def add(
        self,
        audit_id: str,
        label: str,
    ) -> GovernanceIntegrityAuditLabel:
        """
        Apply a label to an existing audit.

        Raises LookupError if the audit does not exist, and ValueError if
        this label is already applied to the audit.
        """

        if self._history_repository.get_by_audit_id(audit_id) is None:
            raise LookupError(
                f"governance integrity audit '{audit_id}' was not found"
            )

        if label in self._label_repository.labels(audit_id):
            raise ValueError(
                f"label '{label}' is already applied to "
                f"audit '{audit_id}'"
            )

        record = GovernanceIntegrityAuditLabel(
            audit_id=audit_id,
            label=label,
            created_at=self._clock(),
        )

        return self._label_repository.add(record)

    def remove(
        self,
        audit_id: str,
        label: str,
    ) -> None:
        """
        Remove a label from an audit. Raises KeyError if it is not
        applied.
        """

        self._label_repository.remove(audit_id, label)

    def labels(
        self,
        audit_id: str,
    ) -> tuple[str, ...]:
        return self._label_repository.labels(audit_id)

    def audits(
        self,
        label: str,
    ) -> tuple[str, ...]:
        return self._label_repository.audits(label)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityAuditLabel,
        ...
    ]:
        return self._label_repository.list()
