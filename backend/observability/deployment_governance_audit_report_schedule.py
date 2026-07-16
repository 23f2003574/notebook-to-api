from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from typing import TYPE_CHECKING, Callable, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .deployment_governance_audit_report_templates import (
        GovernanceIntegrityAuditReportTemplateService,
    )


class GovernanceIntegrityReportScheduleFrequency(
    str,
    Enum,
):
    """
    How often a report template is intended to be executed.
    """

    DAILY = "daily"

    WEEKLY = "weekly"

    MONTHLY = "monthly"


@dataclass(frozen=True)
class GovernanceIntegrityAuditReportSchedule:
    """
    A named execution plan for a report template.

    This layer only manages schedules and execution metadata -- no
    background worker actually runs a schedule yet; `due_schedules()`
    is a placeholder for that future integration.
    """

    name: str

    template_name: str

    frequency: GovernanceIntegrityReportScheduleFrequency

    enabled: bool

    created_at: datetime

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError(
                "name must not be empty"
            )

        if not self.template_name.strip():
            raise ValueError(
                "template_name must not be empty"
            )

        if self.created_at.tzinfo is None:
            raise ValueError(
                "created_at must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "template_name": self.template_name,
            "frequency": self.frequency.value,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat(),
        }


class GovernanceIntegrityAuditReportScheduleError(
    RuntimeError
):
    """
    Base error for governance audit report schedule persistence
    failures.
    """


class GovernanceIntegrityAuditReportScheduleAlreadyExistsError(
    GovernanceIntegrityAuditReportScheduleError
):
    """
    Raised when a schedule with the same name already exists.
    """


@runtime_checkable
class GovernanceIntegrityAuditReportScheduleRepository(Protocol):
    """
    Persistence contract for named governance audit report schedules.
    """

    def save(
        self,
        schedule: GovernanceIntegrityAuditReportSchedule,
    ) -> GovernanceIntegrityAuditReportSchedule:
        """
        Persist one schedule. Raises if the name already exists.
        """

    def get(
        self,
        name: str,
    ) -> GovernanceIntegrityAuditReportSchedule | None:
        """
        Return one schedule by name, or None if it does not exist.
        """

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityAuditReportSchedule,
        ...
    ]:
        """
        Return every schedule, ordered by name.
        """

    def delete(
        self,
        name: str,
    ) -> None:
        """
        Delete one schedule by name. Raises KeyError if it does not
        exist.
        """

    def exists(
        self,
        name: str,
    ) -> bool:
        """
        Return whether a schedule with this name exists.
        """

    def update(
        self,
        schedule: GovernanceIntegrityAuditReportSchedule,
    ) -> GovernanceIntegrityAuditReportSchedule:
        """
        Replace an existing schedule's stored state. Raises KeyError if
        it does not exist.
        """


class InMemoryGovernanceIntegrityAuditReportScheduleRepository:
    """
    Thread-safe in-memory implementation of governance audit report
    schedule storage.
    """

    def __init__(self) -> None:
        self._schedules: dict[
            str,
            GovernanceIntegrityAuditReportSchedule,
        ] = {}

        self._lock = RLock()

    def save(
        self,
        schedule: GovernanceIntegrityAuditReportSchedule,
    ) -> GovernanceIntegrityAuditReportSchedule:
        with self._lock:
            if schedule.name in self._schedules:
                raise (
                    GovernanceIntegrityAuditReportScheduleAlreadyExistsError(
                        f"report schedule '{schedule.name}' "
                        "already exists"
                    )
                )

            self._schedules[schedule.name] = schedule

            return schedule

    def get(
        self,
        name: str,
    ) -> GovernanceIntegrityAuditReportSchedule | None:
        normalized_name = self._normalize_name(name)

        with self._lock:
            return self._schedules.get(normalized_name)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityAuditReportSchedule,
        ...
    ]:
        with self._lock:
            return tuple(
                sorted(
                    self._schedules.values(),
                    key=lambda schedule: schedule.name,
                )
            )

    def delete(
        self,
        name: str,
    ) -> None:
        normalized_name = self._normalize_name(name)

        with self._lock:
            if normalized_name not in self._schedules:
                raise KeyError(
                    f"report schedule '{normalized_name}' "
                    "was not found"
                )

            del self._schedules[normalized_name]

    def exists(
        self,
        name: str,
    ) -> bool:
        normalized_name = self._normalize_name(name)

        with self._lock:
            return normalized_name in self._schedules

    def update(
        self,
        schedule: GovernanceIntegrityAuditReportSchedule,
    ) -> GovernanceIntegrityAuditReportSchedule:
        with self._lock:
            if schedule.name not in self._schedules:
                raise KeyError(
                    f"report schedule '{schedule.name}' was not found"
                )

            self._schedules[schedule.name] = schedule

            return schedule

    @staticmethod
    def _normalize_name(name: str) -> str:
        normalized_name = name.strip()

        if not normalized_name:
            raise ValueError(
                "name must not be empty"
            )

        return normalized_name


class GovernanceIntegrityAuditReportScheduleService:
    """
    Creates and manages execution plans (schedules) for report
    templates.
    """

    def __init__(
        self,
        repository: GovernanceIntegrityAuditReportScheduleRepository,
        template_service: "GovernanceIntegrityAuditReportTemplateService",
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository

        self._template_service = template_service

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

    def create(
        self,
        name: str,
        template_name: str,
        frequency: GovernanceIntegrityReportScheduleFrequency,
    ) -> GovernanceIntegrityAuditReportSchedule:
        """
        Create a new, uniquely named schedule, enabled by default.

        Raises ValueError if a schedule with this name already exists,
        and LookupError if the referenced template does not exist.
        """

        if self._repository.exists(name):
            raise ValueError(
                f"report schedule '{name}' already exists"
            )

        if self._template_service.get(template_name) is None:
            raise LookupError(
                f"report template '{template_name}' was not found"
            )

        schedule = GovernanceIntegrityAuditReportSchedule(
            name=name,
            template_name=template_name,
            frequency=frequency,
            enabled=True,
            created_at=self._clock(),
        )

        return self._repository.save(schedule)

    def enable(
        self,
        name: str,
    ) -> GovernanceIntegrityAuditReportSchedule:
        """
        Enable a schedule. Raises KeyError if it does not exist.
        """

        return self._set_enabled(name, True)

    def disable(
        self,
        name: str,
    ) -> GovernanceIntegrityAuditReportSchedule:
        """
        Disable a schedule. Raises KeyError if it does not exist.
        """

        return self._set_enabled(name, False)

    def delete(
        self,
        name: str,
    ) -> None:
        """
        Delete a schedule by name. Raises KeyError if it does not
        exist.
        """

        self._repository.delete(name)

    def get(
        self,
        name: str,
    ) -> GovernanceIntegrityAuditReportSchedule | None:
        return self._repository.get(name)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityAuditReportSchedule,
        ...
    ]:
        return self._repository.list()

    def due_schedules(
        self,
    ) -> tuple[
        GovernanceIntegrityAuditReportSchedule,
        ...
    ]:
        """
        Return every enabled schedule.

        No background worker exists yet and no time calculations are
        performed here: "due" currently means simply "enabled". A
        future scheduling layer can narrow this by frequency and last
        run time without changing this method's contract.
        """

        return tuple(
            schedule
            for schedule in self._repository.list()
            if schedule.enabled
        )

    def _set_enabled(
        self,
        name: str,
        enabled: bool,
    ) -> GovernanceIntegrityAuditReportSchedule:
        schedule = self._repository.get(name)

        if schedule is None:
            raise KeyError(
                f"report schedule '{name}' was not found"
            )

        return self._repository.update(
            dataclasses.replace(schedule, enabled=enabled)
        )
