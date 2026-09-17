from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import Protocol, runtime_checkable

from .deployment_governance_delivery_engine import (
    GovernanceIntegrityDeliveryEngine,
    GovernanceIntegrityDeliveryResult,
    GovernanceIntegrityDeliveryStatus,
)


@dataclass(frozen=True)
class GovernanceIntegrityDeliveryHistoryRecord:
    """
    A permanent, immutable record of one delivery attempt, preserved
    for auditing after the delivery engine has run.
    """

    delivery_id: str

    dispatch_id: str

    channel_name: str

    status: GovernanceIntegrityDeliveryStatus

    delivered_at: datetime

    error: str | None

    def __post_init__(self) -> None:
        if not self.delivery_id.strip():
            raise ValueError(
                "delivery_id must not be empty"
            )

        if not self.dispatch_id.strip():
            raise ValueError(
                "dispatch_id must not be empty"
            )

        if not self.channel_name.strip():
            raise ValueError(
                "channel_name must not be empty"
            )

        if self.delivered_at.tzinfo is None:
            raise ValueError(
                "delivered_at must be timezone-aware"
            )

        if self.status is GovernanceIntegrityDeliveryStatus.SUCCESS:
            if self.error is not None:
                raise ValueError(
                    "error must not be set when status is SUCCESS"
                )

        else:
            if self.error is None:
                raise ValueError(
                    "error must be set when status is FAILED"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "delivery_id": self.delivery_id,
            "dispatch_id": self.dispatch_id,
            "channel_name": self.channel_name,
            "status": self.status.value,
            "delivered_at": self.delivered_at.isoformat(),
            "error": self.error,
        }


@runtime_checkable
class GovernanceIntegrityDeliveryHistoryRepository(Protocol):
    """
    Persistence contract for immutable governance audit delivery
    history records.
    """

    def save(
        self,
        record: GovernanceIntegrityDeliveryHistoryRecord,
    ) -> GovernanceIntegrityDeliveryHistoryRecord:
        """
        Persist one history record, replacing any existing record
        with the same delivery_id.
        """

    def get(
        self,
        delivery_id: str,
    ) -> GovernanceIntegrityDeliveryHistoryRecord | None:
        """
        Return one history record by id, or None if it does not
        exist.
        """

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityDeliveryHistoryRecord,
        ...
    ]:
        """
        Return every history record, newest to oldest.
        """

    def clear(
        self,
    ) -> None:
        """
        Remove every history record.
        """


class InMemoryGovernanceIntegrityDeliveryHistoryRepository:
    """
    Thread-safe in-memory implementation of governance audit delivery
    history storage.
    """

    def __init__(self) -> None:
        self._records: dict[
            str,
            GovernanceIntegrityDeliveryHistoryRecord,
        ] = {}

        self._lock = RLock()

    def save(
        self,
        record: GovernanceIntegrityDeliveryHistoryRecord,
    ) -> GovernanceIntegrityDeliveryHistoryRecord:
        with self._lock:
            self._records[record.delivery_id] = record

            return record

    def get(
        self,
        delivery_id: str,
    ) -> GovernanceIntegrityDeliveryHistoryRecord | None:
        normalized_id = self._normalize(delivery_id)

        with self._lock:
            return self._records.get(normalized_id)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityDeliveryHistoryRecord,
        ...
    ]:
        with self._lock:
            return tuple(
                sorted(
                    self._records.values(),
                    key=lambda record: (
                        record.delivered_at,
                        record.delivery_id,
                    ),
                    reverse=True,
                )
            )

    def clear(
        self,
    ) -> None:
        with self._lock:
            self._records.clear()

    @staticmethod
    def _normalize(delivery_id: str) -> str:
        normalized_id = delivery_id.strip()

        if not normalized_id:
            raise ValueError(
                "delivery_id must not be empty"
            )

        return normalized_id


class GovernanceIntegrityDeliveryHistoryService:
    """
    Delivers governance audit notification dispatches and permanently
    records the outcome for auditing.
    """

    def __init__(
        self,
        delivery_engine: GovernanceIntegrityDeliveryEngine,
        repository: GovernanceIntegrityDeliveryHistoryRepository,
    ) -> None:
        self._delivery_engine = delivery_engine

        self._repository = repository

    def deliver(
        self,
        dispatch_id: str,
    ) -> GovernanceIntegrityDeliveryHistoryRecord:
        """
        Deliver one dispatch through the delivery engine and record
        the outcome.
        """

        result = self._delivery_engine.deliver(dispatch_id)

        return self.record(result)

    def deliver_all(
        self,
    ) -> tuple[
        GovernanceIntegrityDeliveryHistoryRecord,
        ...
    ]:
        """
        Deliver every currently queued dispatch through the delivery
        engine and record each outcome.
        """

        results = self._delivery_engine.deliver_all()

        return tuple(self.record(result) for result in results)

    def record(
        self,
        result: GovernanceIntegrityDeliveryResult,
    ) -> GovernanceIntegrityDeliveryHistoryRecord:
        """
        Permanently record one delivery outcome.

        One history record exists per delivered dispatch: raises
        ValueError if a record already exists for this result's
        dispatch.
        """

        if self._repository.get(result.dispatch_id) is not None:
            raise ValueError(
                "delivery history already contains a record for "
                f"dispatch '{result.dispatch_id}'"
            )

        record = GovernanceIntegrityDeliveryHistoryRecord(
            delivery_id=result.dispatch_id,
            dispatch_id=result.dispatch_id,
            channel_name=result.channel_name,
            status=result.status,
            delivered_at=result.delivered_at,
            error=result.error,
        )

        return self._repository.save(record)

    def get(
        self,
        delivery_id: str,
    ) -> GovernanceIntegrityDeliveryHistoryRecord | None:
        return self._repository.get(delivery_id)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityDeliveryHistoryRecord,
        ...
    ]:
        return self._repository.list()

    def clear(
        self,
    ) -> None:
        """
        Remove every history record.
        """

        self._repository.clear()
