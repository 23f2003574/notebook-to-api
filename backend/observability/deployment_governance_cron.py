from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from datetime import timezone as _dt_timezone
from typing import TYPE_CHECKING, Callable
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if TYPE_CHECKING:
    from .deployment_governance_event_bus import GovernanceEventBus
    from .deployment_governance_job_registry import GovernanceJobRegistry

# Inclusive value bounds for each of the 5 standard cron fields, in
# field order: minute, hour, day of month, month, day of week. Day of
# week accepts both 0 and 7 for Sunday (the two conventions in common
# use); _normalize_weekdays folds 7 into 0 after parsing.
_FIELD_BOUNDS: "tuple[tuple[int, int], ...]" = (
    (0, 59),
    (0, 23),
    (1, 31),
    (1, 12),
    (0, 7),
)

_FIELD_NAMES: "tuple[str, ...]" = (
    "minute", "hour", "day of month", "month", "day of week",
)


@dataclass(frozen=True)
class _ParsedCron:
    minutes: "frozenset[int]"
    hours: "frozenset[int]"
    days: "frozenset[int]"
    months: "frozenset[int]"
    weekdays: "frozenset[int]"


@dataclass(frozen=True)
class CronTrigger:
    """
    A single registered cron trigger's identity and current scheduling
    state.
    """

    trigger_id: str

    job_id: str

    expression: str

    timezone: str

    enabled: bool

    next_run: "datetime | None"

    def __post_init__(self) -> None:
        if not self.trigger_id:
            raise ValueError("trigger_id must not be empty")

        if not self.job_id:
            raise ValueError("job_id must not be empty")

        if not self.expression:
            raise ValueError("expression must not be empty")

        if not self.timezone:
            raise ValueError("timezone must not be empty")

        if self.next_run is not None and self.next_run.tzinfo is None:
            raise ValueError(
                "next_run must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "trigger_id": self.trigger_id,
            "job_id": self.job_id,
            "expression": self.expression,
            "timezone": self.timezone,
            "enabled": self.enabled,
            "next_run": (
                self.next_run.isoformat()
                if self.next_run is not None
                else None
            ),
        }


@dataclass(frozen=True)
class CronEvaluation:
    """
    The immutable outcome of evaluating one cron trigger at one point
    in time: whether it matched (was due), plus a preview of when it
    would next be due after that. Evaluation never mutates the
    trigger's own stored next_run — that only changes via
    reschedule().
    """

    trigger_id: str

    matched: bool

    evaluated_at: datetime

    next_run: "datetime | None"

    def __post_init__(self) -> None:
        if not self.trigger_id:
            raise ValueError("trigger_id must not be empty")

        if self.evaluated_at.tzinfo is None:
            raise ValueError(
                "evaluated_at must be timezone-aware"
            )

        if self.next_run is not None and self.next_run.tzinfo is None:
            raise ValueError(
                "next_run must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "trigger_id": self.trigger_id,
            "matched": self.matched,
            "evaluated_at": self.evaluated_at.isoformat(),
            "next_run": (
                self.next_run.isoformat()
                if self.next_run is not None
                else None
            ),
        }


class GovernanceCronScheduler:
    """
    Calendar-based ("cron") scheduling, extending the trigger vocabulary
    the Trigger Engine already covers (interval/one_shot/manual/
    immediate) without changing any of it: this is a fully independent
    registry and evaluator for the 5-field standard cron format
    (minute hour day-of-month month day-of-week), supporting `*`,
    numeric values, comma-separated lists, ranges (`1-5`), and step
    values (`*/5`).

    Expressions are validated at registration time (validate() runs
    the same parser register() does) — an invalid expression is
    rejected before a trigger is ever stored, never discovered later
    at evaluation time.

    next_run is computed and stored in UTC, but the matching itself is
    done against the trigger's own configured timezone (UTC by
    default): a "0 9 * * *" trigger in "America/New_York" fires at
    9am US Eastern, whatever that is in UTC on a given day.

    Thread-safe: every mutation of the trigger registry is guarded by
    an internal lock.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        event_bus: "GovernanceEventBus | None" = None,
        job_registry: "GovernanceJobRegistry | None" = None,
    ) -> None:
        self._lock = threading.Lock()

        self._triggers: "dict[str, CronTrigger]" = {}

        self._parsed: "dict[str, _ParsedCron]" = {}

        self._clock = clock or (
            lambda: datetime.now(_dt_timezone.utc)
        )

        self._event_bus = event_bus

        self._job_registry = job_registry

    def register(
        self,
        job_id: str,
        *,
        expression: str,
        timezone: str = "UTC",
        enabled: bool = True,
    ) -> CronTrigger:
        """
        Register a new cron trigger for job_id under a fresh, unique
        trigger_id.

        Raises ValueError if job_id is not a registered job (only
        checked when this scheduler was constructed with a
        job_registry), if expression fails to parse, or if timezone
        is not a recognized IANA timezone name.
        """

        if (
            self._job_registry is not None
            and not self._job_registry.exists(job_id)
        ):
            raise ValueError(
                f"job '{job_id}' is not registered"
            )

        parsed = self._parse(expression)
        tzinfo = self._resolve_timezone(timezone)

        now = self._clock()

        next_run = (
            self._compute_next_run(parsed, tzinfo, now)
            if enabled
            else None
        )

        trigger = CronTrigger(
            trigger_id=str(uuid4()),
            job_id=job_id,
            expression=expression,
            timezone=timezone,
            enabled=enabled,
            next_run=next_run,
        )

        with self._lock:
            self._triggers[trigger.trigger_id] = trigger
            self._parsed[trigger.trigger_id] = parsed

        self._publish(
            "cron_registered",
            trigger.trigger_id,
            {"job_id": job_id, "expression": expression},
        )

        return trigger

    def remove(self, trigger_id: str) -> None:
        """
        Remove a registered cron trigger.

        Raises KeyError if trigger_id is not registered.
        """

        with self._lock:
            trigger = self._triggers.pop(trigger_id, None)

            if trigger is None:
                raise KeyError(
                    f"cron trigger '{trigger_id}' is not registered"
                )

            self._parsed.pop(trigger_id, None)

        self._publish(
            "cron_removed", trigger_id, {"job_id": trigger.job_id}
        )

    def validate(self, expression: str) -> bool:
        """
        Return whether expression is a valid 5-field cron expression,
        without registering anything.
        """

        try:
            self._parse(expression)
            return True

        except ValueError:
            return False

    def evaluate(
        self, trigger_id: str, *, at: "datetime | None" = None
    ) -> CronEvaluation:
        """
        Evaluate whether trigger_id is due at at (default now),
        without mutating its stored next_run.

        Publishes "cron_triggered" when matched is True.

        Raises KeyError if trigger_id is not registered.
        """

        at = at or self._clock()

        with self._lock:
            trigger = self._triggers.get(trigger_id)

            if trigger is None:
                raise KeyError(
                    f"cron trigger '{trigger_id}' is not registered"
                )

            parsed = self._parsed[trigger_id]

        matched = (
            trigger.enabled
            and trigger.next_run is not None
            and at >= trigger.next_run
        )

        tzinfo = self._resolve_timezone(trigger.timezone)
        upcoming = self._compute_next_run(parsed, tzinfo, at)

        if matched:
            self._publish(
                "cron_triggered", trigger_id, {"job_id": trigger.job_id}
            )

        return CronEvaluation(
            trigger_id=trigger_id,
            matched=matched,
            evaluated_at=at,
            next_run=upcoming,
        )

    def next_run(self, trigger_id: str) -> "datetime | None":
        """
        Return trigger_id's currently stored next_run.

        Raises KeyError if trigger_id is not registered.
        """

        with self._lock:
            trigger = self._triggers.get(trigger_id)

            if trigger is None:
                raise KeyError(
                    f"cron trigger '{trigger_id}' is not registered"
                )

            return trigger.next_run

    def reschedule(
        self, trigger_id: str, *, at: "datetime | None" = None
    ) -> CronTrigger:
        """
        Recompute and store trigger_id's next_run as the soonest match
        strictly after at (default now).

        Raises KeyError if trigger_id is not registered.
        """

        at = at or self._clock()

        with self._lock:
            trigger = self._triggers.get(trigger_id)

            if trigger is None:
                raise KeyError(
                    f"cron trigger '{trigger_id}' is not registered"
                )

            parsed = self._parsed[trigger_id]

        tzinfo = self._resolve_timezone(trigger.timezone)
        next_run = self._compute_next_run(parsed, tzinfo, at)

        with self._lock:
            updated = replace(trigger, next_run=next_run)
            self._triggers[trigger_id] = updated

        self._publish(
            "cron_rescheduled",
            trigger_id,
            {"next_run": next_run.isoformat()},
        )

        return updated

    def list(self) -> "tuple[CronTrigger, ...]":
        """
        Return every registered cron trigger, ordered by next_run
        (triggers with no next_run sort last) and then trigger_id, for
        deterministic output.
        """

        with self._lock:
            triggers = list(self._triggers.values())

        sentinel = datetime.max.replace(tzinfo=_dt_timezone.utc)

        return tuple(
            sorted(
                triggers,
                key=lambda trigger: (
                    trigger.next_run or sentinel, trigger.trigger_id
                ),
            )
        )

    def clear(self) -> None:
        """
        Remove every registered cron trigger.
        """

        with self._lock:
            self._triggers.clear()
            self._parsed.clear()

    def _parse(self, expression: str) -> _ParsedCron:
        if not expression or not expression.strip():
            raise ValueError("cron expression must not be empty")

        fields = expression.split()

        if len(fields) != 5:
            raise ValueError(
                "cron expression must have exactly 5 fields (minute "
                f"hour day month weekday), got {len(fields)}: "
                f"'{expression}'"
            )

        parsed_fields = []

        for field, (min_value, max_value), name in zip(
            fields, _FIELD_BOUNDS, _FIELD_NAMES
        ):
            try:
                parsed_fields.append(
                    self._parse_field(field, min_value, max_value)
                )

            except ValueError as exc:
                raise ValueError(
                    f"invalid {name} field '{field}' in cron "
                    f"expression '{expression}': {exc}"
                ) from exc

        minutes, hours, days, months, weekdays_raw = parsed_fields

        weekdays = frozenset(
            0 if value == 7 else value for value in weekdays_raw
        )

        return _ParsedCron(
            minutes=minutes,
            hours=hours,
            days=days,
            months=months,
            weekdays=weekdays,
        )

    def _parse_field(
        self, field: str, min_value: int, max_value: int
    ) -> "frozenset[int]":
        values: "set[int]" = set()

        for part in field.split(","):
            part = part.strip()

            if not part:
                raise ValueError("empty field segment")

            step = 1
            base = part

            if "/" in part:
                base, step_text = part.split("/", 1)

                try:
                    step = int(step_text)

                except ValueError:
                    raise ValueError(
                        f"invalid step value '{step_text}'"
                    ) from None

                if step <= 0:
                    raise ValueError(
                        f"step value must be > 0, got {step}"
                    )

            if base == "*":
                start, end = min_value, max_value

            elif "-" in base:
                start_text, end_text = base.split("-", 1)

                try:
                    start, end = int(start_text), int(end_text)

                except ValueError:
                    raise ValueError(
                        f"invalid range '{base}'"
                    ) from None

            else:
                try:
                    start = end = int(base)

                except ValueError:
                    raise ValueError(
                        f"invalid value '{base}'"
                    ) from None

            if start > end:
                raise ValueError(
                    f"range start must be <= end in '{base}'"
                )

            if start < min_value or end > max_value:
                raise ValueError(
                    f"value out of bounds [{min_value}, {max_value}] "
                    f"in '{part}'"
                )

            values.update(range(start, end + 1, step))

        if not values:
            raise ValueError("field matched no values")

        return frozenset(values)

    def _matches(self, parsed: _ParsedCron, candidate: datetime) -> bool:
        weekday = candidate.isoweekday() % 7

        return (
            candidate.minute in parsed.minutes
            and candidate.hour in parsed.hours
            and candidate.day in parsed.days
            and candidate.month in parsed.months
            and weekday in parsed.weekdays
        )

    def _compute_next_run(
        self,
        parsed: _ParsedCron,
        tzinfo: "ZoneInfo",
        after_utc: datetime,
    ) -> datetime:
        local_after = after_utc.astimezone(tzinfo)

        candidate = (
            local_after + timedelta(minutes=1)
        ).replace(second=0, microsecond=0)

        horizon = local_after + timedelta(days=365 * 4)

        while candidate <= horizon:
            if self._matches(parsed, candidate):
                return candidate.astimezone(_dt_timezone.utc)

            candidate += timedelta(minutes=1)

        raise ValueError(
            "cron expression has no matching time within the search "
            "horizon"
        )

    def _resolve_timezone(self, name: str) -> "ZoneInfo":
        try:
            return ZoneInfo(name)

        except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
            raise ValueError(f"unknown timezone '{name}'") from exc

    def _publish(
        self,
        event_type: str,
        source: str,
        payload: "dict[str, object] | None" = None,
    ) -> None:
        if self._event_bus is None:
            return

        self._event_bus.publish(
            event_type, source=source, payload=payload
        )


def build_default_governance_cron_scheduler() -> GovernanceCronScheduler:
    """
    Build the process-wide governance cron scheduler, wired to the
    process-wide governance event bus and job registry.
    """

    from .deployment_governance_event_bus import get_event_bus
    from .deployment_governance_job_registry import get_job_registry

    return GovernanceCronScheduler(
        event_bus=get_event_bus(), job_registry=get_job_registry()
    )


# Shared for the lifetime of the process: cron triggers registered
# through the API need to be visible to whatever queries the scheduler
# directly, which a persistence runtime built fresh per request cannot
# provide on its own.
_cron_scheduler = build_default_governance_cron_scheduler()


def get_cron_scheduler() -> GovernanceCronScheduler:
    """
    Return the process-wide governance cron scheduler.
    """

    return _cron_scheduler
