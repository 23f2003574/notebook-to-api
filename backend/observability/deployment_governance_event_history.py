from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, TYPE_CHECKING

from .deployment_governance_event_bus import (
    GovernanceEvent,
    GovernanceEventBus,
)

if TYPE_CHECKING:
    from .deployment_governance_event_router import EventRoute
    from .deployment_governance_audit import GovernanceAuditService


@dataclass(frozen=True)
class StoredGovernanceEvent:
    """
    One immutable, persisted record of a governance event: its
    assigned sequence number plus the original event.
    """

    sequence: int

    event: GovernanceEvent

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("sequence must be >= 1")

    def to_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "event": self.event.to_dict(),
        }


@dataclass(frozen=True)
class EventQuery:
    """
    Filter criteria for querying stored governance events. Every
    field is optional except limit: an unfiltered EventQuery() matches
    every stored event, capped at the default limit.
    """

    event_type: "str | None" = None

    source: "str | None" = None

    start_time: "datetime | None" = None

    end_time: "datetime | None" = None

    limit: int = 100

    def __post_init__(self) -> None:
        if self.start_time is not None and self.start_time.tzinfo is None:
            raise ValueError("start_time must be timezone-aware")

        if self.end_time is not None and self.end_time.tzinfo is None:
            raise ValueError("end_time must be timezone-aware")

        if self.limit <= 0:
            raise ValueError("limit must be greater than zero")

    def matches(self, stored: StoredGovernanceEvent) -> bool:
        event = stored.event

        if self.event_type is not None and event.event_type != self.event_type:
            return False

        if self.source is not None and event.source != self.source:
            return False

        if self.start_time is not None and event.occurred_at < self.start_time:
            return False

        if self.end_time is not None and event.occurred_at > self.end_time:
            return False

        return True


class GovernanceEventHistory:
    """
    Append-only, in-memory store of every governance event published
    on the event bus, with filtered querying and deterministic
    replay.

    Storage is strictly additive: there is no way to modify or delete
    an individual stored entry, only append new ones or purge()
    everything at once. A configurable retention limit automatically
    evicts the oldest entries once exceeded — an aging-out policy,
    not a caller-initiated deletion.
    """

    def __init__(
        self,
        *,
        max_entries: "int | None" = None,
        clock: "Callable[[], datetime] | None" = None,
        audit_service: "GovernanceAuditService | None" = None,
    ) -> None:
        self._entries: "dict[int, StoredGovernanceEvent]" = {}

        self._next_sequence = 1

        self._max_entries = max_entries

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._audit_service = audit_service

        # Guards against replay() re-persisting the events it
        # re-dispatches: _handle_bus_event (the bus subscriber this
        # history wires itself up with) checks this flag and skips
        # appending while a replay is in progress.
        self._replaying = False

    def append(self, event: GovernanceEvent) -> StoredGovernanceEvent:
        """
        Persist event, assigning it the next sequence number.

        Sequence numbers are monotonically increasing and never
        reused, even across purge() calls: the counter is never reset
        by anything other than constructing a brand new history.
        """

        stored = StoredGovernanceEvent(
            sequence=self._next_sequence, event=event
        )

        self._next_sequence += 1

        self._entries[stored.sequence] = stored

        if self._max_entries is not None:
            while len(self._entries) > self._max_entries:
                oldest_sequence = next(iter(self._entries))
                del self._entries[oldest_sequence]

        return stored

    def get(self, sequence: int) -> StoredGovernanceEvent:
        """
        Return the stored event with this exact sequence number.

        Raises LookupError if no stored event has this sequence
        (never existed, or aged out via retention/purge()).
        """

        try:
            return self._entries[sequence]

        except KeyError:
            raise LookupError(
                f"no stored event with sequence {sequence}"
            ) from None

    def query(
        self,
        query: "EventQuery | None" = None,
    ) -> "tuple[StoredGovernanceEvent, ...]":
        """
        Return every stored event matching query, newest first,
        capped at query.limit (or the default limit if query is
        omitted).
        """

        query = query or EventQuery()

        matches = [
            stored
            for stored in self._entries.values()
            if query.matches(stored)
        ]

        matches.sort(key=lambda stored: stored.sequence, reverse=True)

        return tuple(matches[: query.limit])

    def latest(self, limit: int = 10) -> "tuple[StoredGovernanceEvent, ...]":
        """
        Return the most recently stored events, newest first, capped
        at limit.
        """

        return self.query(EventQuery(limit=limit))

    def matching(
        self, route: "EventRoute"
    ) -> "tuple[StoredGovernanceEvent, ...]":
        """
        Return every stored event whose event_type/source match
        route's criteria (honoring the "*" wildcard the same way
        GovernanceEventRouter.route() does), newest first.

        Ignores route.enabled: this asks what route's criteria would
        have selected historically, which is meaningful regardless of
        whether the route happens to be active right now.
        """

        from .deployment_governance_event_router import route_matches

        matches = [
            stored
            for stored in self._entries.values()
            if route_matches(
                route,
                event_type=stored.event.event_type,
                source=stored.event.source,
            )
        ]

        matches.sort(key=lambda stored: stored.sequence, reverse=True)

        return tuple(matches)

    def replay(
        self,
        query: "EventQuery | None",
        bus: GovernanceEventBus,
    ) -> "tuple[GovernanceEvent, ...]":
        """
        Re-dispatch every stored event matching query to bus's
        current subscribers, in ascending sequence order (the order
        the events originally happened in), reusing bus.dispatch()
        rather than bus.publish() so no new event is minted.

        Does not modify history: this history's own bus subscription
        is suppressed for the duration of the call, so replayed
        events are dispatched to every other current subscriber
        without being appended again.

        Records an "event_replay" audit entry, if this history was
        constructed with an audit_service: a manual replay is exactly
        the kind of high-value administrative action the audit trail
        exists to capture.
        """

        query = query or EventQuery()

        matches = sorted(
            (
                stored
                for stored in self._entries.values()
                if query.matches(stored)
            ),
            key=lambda stored: stored.sequence,
        )

        self._replaying = True

        try:
            for stored in matches:
                bus.dispatch(stored.event)

        finally:
            self._replaying = False

        replayed = tuple(stored.event for stored in matches)

        if self._audit_service is not None:
            self._audit_service.record(
                action="event_replay",
                actor="system",
                resource="event_history",
                outcome="success",
                metadata={
                    "count": len(replayed),
                    "event_type": query.event_type,
                    "source": query.source,
                },
            )

        return replayed

    def purge(self) -> int:
        """
        Remove every stored event, returning how many were removed.

        Does not reset the sequence counter: the next appended event
        continues numbering from where it left off, so a sequence
        number is never reused.
        """

        count = len(self._entries)

        self._entries.clear()

        return count

    def size(self) -> int:
        """
        Return the number of currently stored events.
        """

        return len(self._entries)

    def _handle_bus_event(self, event: GovernanceEvent) -> None:
        """
        The subscriber callback this history wires itself up with via
        bus.subscribe_all(). Not part of the public API: callers
        needing to persist an event directly should use append().
        """

        if self._replaying:
            return

        self.append(event)


# Shared for the lifetime of the process: every event published on
# the process-wide event bus needs to reach the same history so
# GET /governance/events (and friends) can see it, regardless of
# which request happened to publish it. Wired to the process-wide
# audit service (a plain top-level import, not deferred:
# deployment_governance_audit has no dependency on this module, so
# there is no circular import to avoid) so replay() records an
# "event_replay" audit entry.
from .deployment_governance_audit import get_audit_service  # noqa: E402

_event_history = GovernanceEventHistory(audit_service=get_audit_service())


def get_event_history() -> GovernanceEventHistory:
    """
    Return the process-wide governance event history, (re-)wiring it
    to the process-wide event bus on every access if its subscription
    is not currently present.

    Checked on every call, rather than assumed permanent after a
    first wiring, because GovernanceEventBus.clear() (e.g. in test
    teardown) removes every subscription including this one — an
    identity check on "have I ever wired this bus" would otherwise
    leave persistence silently broken after any clear().
    """

    from .deployment_governance_event_bus import get_event_bus

    bus = get_event_bus()

    already_wired = any(
        subscription.handler == _event_history._handle_bus_event
        for subscription in bus.subscribers(bus.WILDCARD_EVENT_TYPE)
    )

    if not already_wired:
        bus.subscribe_all(_event_history._handle_bus_event)

    return _event_history
