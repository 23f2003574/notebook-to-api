from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass

_correlation_var: (
    "ContextVar[GovernanceCorrelationContext | None]"
) = ContextVar("governance_correlation_context", default=None)


@dataclass(frozen=True)
class GovernanceCorrelationContext:
    """
    One correlation identity: a random UUID4 identifying a single
    logical operation, and (for a non-root correlation) the UUID4 of
    the correlation it was created under.

    A dispatch's whole lifecycle -- its first delivery attempt and
    every retry -- shares one root GovernanceCorrelationContext
    (parent_correlation_id=None); a specific provider invocation
    within an attempt gets its own child correlation
    (parent_correlation_id=the root's correlation_id), so entries
    from that one invocation can be picked out from the rest of the
    dispatch's history while still being traceable back to it.
    """

    correlation_id: uuid.UUID

    parent_correlation_id: uuid.UUID | None

    def __post_init__(self) -> None:
        if self.parent_correlation_id == self.correlation_id:
            raise ValueError(
                "parent_correlation_id must not equal "
                "correlation_id"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "correlation_id": str(self.correlation_id),
            "parent_correlation_id": (
                None
                if self.parent_correlation_id is None
                else str(self.parent_correlation_id)
            ),
        }


class GovernanceCorrelationService:
    """
    Tracks the currently active GovernanceCorrelationContext so
    GovernanceIntegrityLogger can automatically attach correlation_id
    and parent_correlation_id to every log entry, letting every
    entry produced across the scheduler, worker, delivery engine,
    and providers for one logical operation be traced together.

    Backed by a contextvars.ContextVar, like
    GovernanceLogContextService, so the active correlation stays
    correctly isolated per logical execution flow rather than
    leaking between concurrent callers on the same thread. Unlike
    GovernanceLogContextService's nested push/pop stack, there is
    only ever one active correlation at a time: create()/child()/
    attach() each replace it outright, so a nested operation that
    does not explicitly start a new correlation automatically
    inherits whatever correlation its caller is already using.
    """

    def create(self) -> GovernanceCorrelationContext:
        """
        Start a new root correlation (a fresh UUID4, no parent),
        make it the active correlation, and return it. Intended for
        the start of a new logical operation, e.g. a dispatch's
        first delivery attempt.
        """

        context = GovernanceCorrelationContext(
            correlation_id=uuid.uuid4(),
            parent_correlation_id=None,
        )

        _correlation_var.set(context)

        return context

    def child(self) -> GovernanceCorrelationContext:
        """
        Start a new correlation (a fresh UUID4) whose parent is the
        currently active correlation, make it the active correlation,
        and return it. If no correlation is currently active, this
        behaves like create(): the new correlation becomes a root.
        """

        current = _correlation_var.get()

        context = GovernanceCorrelationContext(
            correlation_id=uuid.uuid4(),
            parent_correlation_id=(
                None if current is None else current.correlation_id
            ),
        )

        _correlation_var.set(context)

        return context

    def current(self) -> GovernanceCorrelationContext | None:
        """
        Return the currently active correlation, or None if none is
        active.
        """

        return _correlation_var.get()

    def attach(
        self, context: GovernanceCorrelationContext
    ) -> None:
        """
        Make context the active correlation outright, without
        deriving it from whatever was previously active. Intended
        for reusing a previously created root correlation across a
        retry, so every attempt at delivering the same dispatch
        shares one correlation_id.
        """

        _correlation_var.set(context)

    def clear(self) -> None:
        """
        Clear the active correlation. Later log calls stop having
        correlation_id/parent_correlation_id attached until create(),
        child(), or attach() is called again.
        """

        _correlation_var.set(None)
