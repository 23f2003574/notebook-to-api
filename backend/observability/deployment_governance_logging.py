from __future__ import annotations

import logging
import sys
import traceback
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Callable, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_log_repository import (
        GovernanceLogRepository,
    )
    from .deployment_governance_log_redaction import (
        GovernanceLogRedactionService,
    )
    from .deployment_governance_log_context import (
        GovernanceLogContextService,
    )

_VALID_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")

_LEVEL_TO_STDLIB = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}

DEFAULT_LOG_BUFFER_SIZE = 1000


@dataclass(frozen=True)
class GovernanceLogEntry:
    """
    One immutable structured log record emitted by a governance
    component.
    """

    timestamp: datetime

    level: str

    component: str

    event: str

    fields: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError(
                "timestamp must be timezone-aware"
            )

        if self.level not in _VALID_LEVELS:
            raise ValueError(
                f"level must be one of {', '.join(_VALID_LEVELS)}"
            )

        if not self.component:
            raise ValueError(
                "component must not be empty"
            )

        if not self.event:
            raise ValueError(
                "event must not be empty"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "level": self.level,
            "component": self.component,
            "event": self.event,
            "fields": dict(self.fields),
        }


class GovernanceIntegrityLogger:
    """
    Centralized structured logger for governance components.

    Every call produces an immutable GovernanceLogEntry: a UTC
    timestamp, a level, the emitting component's name, an event
    name, and structured key/value fields. Entries are kept in a
    bounded in-memory buffer (so a CLI or dashboard can tail recent
    activity) and forwarded to a standard library logging.Logger for
    real output, rather than ever calling print() directly. Safe to
    share across concurrent governance services: every mutating call
    is guarded by a single lock.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        buffer_size: int = DEFAULT_LOG_BUFFER_SIZE,
        sink: logging.Logger | None = None,
        repository: "GovernanceLogRepository | None" = None,
        redaction_service: (
            "GovernanceLogRedactionService | None"
        ) = None,
        context_service: (
            "GovernanceLogContextService | None"
        ) = None,
    ) -> None:
        if buffer_size < 1:
            raise ValueError(
                "buffer_size must be at least 1"
            )

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._sink = sink or logging.getLogger(
            "notebook2api.governance"
        )

        self._lock = Lock()

        self._entries: "deque[GovernanceLogEntry]" = deque(
            maxlen=buffer_size
        )

        self._repository = repository

        self._redaction_service = redaction_service

        self._context_service = context_service

    def debug(
        self,
        component: str,
        event: str,
        **fields: Any,
    ) -> GovernanceLogEntry:
        """
        Record one DEBUG-level structured entry.
        """

        return self._log("DEBUG", component, event, fields)

    def info(
        self,
        component: str,
        event: str,
        **fields: Any,
    ) -> GovernanceLogEntry:
        """
        Record one INFO-level structured entry.
        """

        return self._log("INFO", component, event, fields)

    def warning(
        self,
        component: str,
        event: str,
        **fields: Any,
    ) -> GovernanceLogEntry:
        """
        Record one WARNING-level structured entry.
        """

        return self._log("WARNING", component, event, fields)

    def error(
        self,
        component: str,
        event: str,
        **fields: Any,
    ) -> GovernanceLogEntry:
        """
        Record one ERROR-level structured entry.
        """

        return self._log("ERROR", component, event, fields)

    def exception(
        self,
        component: str,
        event: str,
        **fields: Any,
    ) -> GovernanceLogEntry:
        """
        Record one ERROR-level entry that also captures the
        currently handled exception's traceback, if any.

        Intended to be called from inside an except block, mirroring
        logging.Logger.exception(). Outside of an except block (no
        exception currently being handled), behaves exactly like
        error().
        """

        exc_type, exc_value, exc_tb = sys.exc_info()

        if exc_type is not None:
            fields = {
                **fields,
                "exception": "".join(
                    traceback.format_exception(
                        exc_type, exc_value, exc_tb
                    )
                ).strip(),
            }

        return self._log("ERROR", component, event, fields)

    def entries(
        self,
        limit: int | None = None,
        level: str | None = None,
        *,
        component: str | None = None,
        event: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> tuple[GovernanceLogEntry, ...]:
        """
        Return buffered log entries, newest first, optionally
        filtered by level, component, and/or event (filters combine
        with AND), and/or restricted to an inclusive [since, until]
        time range, and/or capped to the most recent limit entries.

        This filters the logger's own in-process buffer; it does not
        consult a configured repository, so it only reflects recent
        activity in this process. For durable, indexed search across
        the full persisted history, use GovernanceLogSearchService
        against the repository instead.
        """

        if level is not None and level not in _VALID_LEVELS:
            raise ValueError(
                f"level must be one of {', '.join(_VALID_LEVELS)}"
            )

        with self._lock:
            snapshot = list(self._entries)

        snapshot.reverse()

        if level is not None:
            snapshot = [
                entry
                for entry in snapshot
                if entry.level == level
            ]

        if component is not None:
            snapshot = [
                entry
                for entry in snapshot
                if entry.component == component
            ]

        if event is not None:
            snapshot = [
                entry
                for entry in snapshot
                if entry.event == event
            ]

        if since is not None:
            snapshot = [
                entry
                for entry in snapshot
                if entry.timestamp >= since
            ]

        if until is not None:
            snapshot = [
                entry
                for entry in snapshot
                if entry.timestamp <= until
            ]

        if limit is not None:
            snapshot = snapshot[:limit]

        return tuple(snapshot)

    def clear(self) -> None:
        """
        Discard every buffered entry, and every entry stored in the
        configured repository, if one is attached. Does not affect
        the standard library sink.
        """

        with self._lock:
            self._entries.clear()

            repository = self._repository

        if repository is not None:
            repository.clear()

    def set_repository(
        self, repository: "GovernanceLogRepository | None"
    ) -> None:
        """
        Attach (or detach) a GovernanceLogRepository after
        construction, without recreating the logger.
        """

        with self._lock:
            self._repository = repository

    def set_redaction_service(
        self,
        redaction_service: "GovernanceLogRedactionService | None",
    ) -> None:
        """
        Attach (or detach) a GovernanceLogRedactionService after
        construction, without recreating the logger.
        """

        with self._lock:
            self._redaction_service = redaction_service

    def set_context_service(
        self,
        context_service: "GovernanceLogContextService | None",
    ) -> None:
        """
        Attach (or detach) a GovernanceLogContextService after
        construction, without recreating the logger.
        """

        with self._lock:
            self._context_service = context_service

    def _log(
        self,
        level: str,
        component: str,
        event: str,
        fields: Mapping[str, Any],
    ) -> GovernanceLogEntry:
        with self._lock:
            context_service = self._context_service

        merged_fields = dict(fields)

        if context_service is not None:
            context = context_service.current()

            if context is not None:
                # Explicit fields win: only fill in a context value
                # for a key the caller did not already supply.
                for key, value in context.to_dict().items():
                    if value is None:
                        continue

                    merged_fields.setdefault(key, value)

        entry = GovernanceLogEntry(
            timestamp=self._clock(),
            level=level,
            component=component,
            event=event,
            fields=merged_fields,
        )

        with self._lock:
            redaction_service = self._redaction_service

        if redaction_service is not None:
            # Redacted before it ever reaches the in-memory buffer or
            # a configured repository: neither should ever hold a
            # sensitive value, even transiently.
            entry = redaction_service.redact(entry)

        with self._lock:
            self._entries.append(entry)

            repository = self._repository

        if repository is not None:
            repository.append(entry)

        self._sink.log(
            _LEVEL_TO_STDLIB[level],
            "%s: %s",
            event,
            entry.fields,
        )

        return entry
