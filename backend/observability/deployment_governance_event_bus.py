from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Callable, Iterable
from uuid import uuid4

# The well-known governance runtime event types, published by the
# lifecycle manager, health service, and metrics bootstrap. Not
# enforced by the bus itself (publish() accepts any event_type
# string), but this is the vocabulary GET /governance/events/types
# advertises and every current publisher uses.
GOVERNANCE_EVENT_TYPES: "tuple[str, ...]" = (
    "component_started",
    "component_stopped",
    "component_failed",
    "health_check_completed",
    "readiness_check_completed",
    "lifecycle_completed",
    "metrics_snapshot_created",
)


@dataclass(frozen=True)
class GovernanceEvent:
    """
    An immutable record of one thing that happened in the governance
    runtime.

    payload is stored as a read-only mapping rather than a plain
    dict: a frozen dataclass only blocks reassigning its fields, not
    mutating a dict stored in one, so wrapping it is what actually
    makes "immutable event objects" true rather than merely
    documented.
    """

    event_id: str

    event_type: str

    source: str

    payload: "dict[str, Any]"

    occurred_at: datetime

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValueError("event_id must not be empty")

        if not self.event_type:
            raise ValueError("event_type must not be empty")

        if not self.source:
            raise ValueError("source must not be empty")

        if self.occurred_at.tzinfo is None:
            raise ValueError(
                "occurred_at must be timezone-aware"
            )

        object.__setattr__(
            self, "payload", MappingProxyType(dict(self.payload))
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "source": self.source,
            "payload": dict(self.payload),
            "occurred_at": self.occurred_at.isoformat(),
        }


@dataclass(frozen=True)
class EventSubscription:
    """
    One handler's subscription to an event type.
    """

    event_type: str

    handler: "Callable[[GovernanceEvent], None]"

    def to_dict(self) -> dict[str, object]:
        return {
            "event_type": self.event_type,
            "handler": getattr(
                self.handler, "__name__", repr(self.handler)
            ),
        }


class GovernanceEventBus:
    """
    An internal publish/subscribe bus decoupling governance
    components: a publisher does not need to know who (if anyone) is
    listening, and a subscriber does not need to know who published.

    Dispatch is synchronous (a handler runs, and finishes, inside the
    publish() call that triggered it) and dispatched in the exact
    order handlers were subscribed, for every event type. A handler
    that raises is isolated: publish() catches the exception, moves
    on to the remaining handlers for that event, and still returns
    the published event to the caller.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._subscriptions: "dict[str, list[EventSubscription]]" = {}

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

    def subscribe(
        self,
        event_type: str,
        handler: "Callable[[GovernanceEvent], None]",
    ) -> EventSubscription:
        """
        Register handler to be called, synchronously, whenever an
        event of event_type is published.

        Multiple subscribers may register for the same event_type;
        each is dispatched in the order it was subscribed.
        """

        if not event_type:
            raise ValueError("event_type must not be empty")

        subscription = EventSubscription(
            event_type=event_type, handler=handler
        )

        self._subscriptions.setdefault(event_type, []).append(
            subscription
        )

        return subscription

    def unsubscribe(self, subscription: EventSubscription) -> None:
        """
        Remove a previously returned subscription.

        Raises ValueError if subscription is not currently
        registered.
        """

        subscriptions = self._subscriptions.get(
            subscription.event_type
        )

        if not subscriptions or subscription not in subscriptions:
            raise ValueError(
                "subscription is not registered for event type "
                f"'{subscription.event_type}'"
            )

        subscriptions.remove(subscription)

    def publish(
        self,
        event_type: str,
        source: str,
        payload: "dict[str, Any] | None" = None,
    ) -> GovernanceEvent:
        """
        Construct and dispatch one event: a fresh UUID event_id and
        the current UTC time are assigned here, not by the caller.

        Every subscriber currently registered for event_type is
        called, in subscription order. A handler that raises is
        isolated — logged nowhere (this bus has no logging
        dependency of its own) but never allowed to stop the
        remaining handlers or propagate back to the publisher.
        """

        event = GovernanceEvent(
            event_id=str(uuid4()),
            event_type=event_type,
            source=source,
            payload=payload or {},
            occurred_at=self._clock(),
        )

        for subscription in self._subscriptions.get(event_type, ()):
            try:
                subscription.handler(event)

            except Exception:
                pass

        return event

    def publish_batch(
        self,
        events: "Iterable[tuple[str, str, dict[str, Any] | None]]",
    ) -> "tuple[GovernanceEvent, ...]":
        """
        Publish a sequence of (event_type, source, payload) events,
        in order, returning every resulting GovernanceEvent in the
        same order.
        """

        return tuple(
            self.publish(event_type, source, payload)
            for event_type, source, payload in events
        )

    def subscribers(
        self,
        event_type: "str | None" = None,
    ) -> "tuple[EventSubscription, ...]":
        """
        Return every subscription for event_type, in subscription
        order.

        If event_type is None, return every subscription across every
        event type instead, ordered by event type name and then
        subscription order within each.
        """

        if event_type is not None:
            return tuple(self._subscriptions.get(event_type, ()))

        return tuple(
            subscription
            for registered_type in sorted(self._subscriptions)
            for subscription in self._subscriptions[registered_type]
        )

    def clear(self) -> None:
        """
        Remove every subscription.
        """

        self._subscriptions.clear()


# Shared for the lifetime of the process: publishers (the lifecycle
# manager singleton, health/metrics services that opt in) and
# subscribers need to reach the same bus, and the
# GET /governance/events/subscribers endpoint reflects whatever is
# currently registered on it.
_event_bus = GovernanceEventBus()


def get_event_bus() -> GovernanceEventBus:
    """
    Return the process-wide governance event bus.
    """

    return _event_bus
