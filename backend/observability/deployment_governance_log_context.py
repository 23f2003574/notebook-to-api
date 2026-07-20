from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

_context_stack: "ContextVar[tuple[GovernanceLogContext, ...]]" = (
    ContextVar("governance_log_context_stack", default=())
)


@dataclass(frozen=True)
class GovernanceLogContext:
    """
    One immutable scope of execution context (which dispatch,
    provider, and/or inbound request a log entry was produced
    during), automatically merged into every GovernanceLogEntry's
    fields while the scope that pushed it is active.
    """

    request_id: str | None

    dispatch_id: str | None

    provider: str | None

    component: str

    def __post_init__(self) -> None:
        if not self.component:
            raise ValueError(
                "component must not be empty"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "dispatch_id": self.dispatch_id,
            "provider": self.provider,
            "component": self.component,
        }


class GovernanceLogContextService:
    """
    Tracks the active stack of GovernanceLogContext scopes so
    GovernanceIntegrityLogger can automatically attach execution
    context (which dispatch, provider, etc. a log call happened
    during) to every entry without every caller threading it
    through explicitly.

    Backed by a contextvars.ContextVar rather than thread-local
    storage, so scopes stay correctly isolated per logical
    execution flow (including across async tasks that share an
    event loop thread) instead of leaking between concurrent
    callers on the same thread. Scopes nest: push() layers a new
    scope on top of whatever is already active, and current()
    always returns the innermost (most recently pushed, not yet
    popped) one. Popping past the top of an empty stack is a
    harmless no-op, so a caller's own try/finally cleanup can never
    raise even if a push failed partway through.
    """

    def current(self) -> GovernanceLogContext | None:
        """
        Return the innermost active context, or None if no scope is
        currently active.
        """

        stack = _context_stack.get()

        return stack[-1] if stack else None

    def push(self, context: GovernanceLogContext) -> None:
        """
        Push a new context scope on top of whatever is currently
        active. Pair with a later pop() (typically in a try/finally)
        to remove it once the scope's work is done.
        """

        stack = _context_stack.get()

        _context_stack.set(stack + (context,))

    def pop(self) -> GovernanceLogContext | None:
        """
        Remove and return the innermost context scope, restoring
        whatever scope (if any) was active before it was pushed. A
        no-op returning None if no scope is currently active.
        """

        stack = _context_stack.get()

        if not stack:
            return None

        _context_stack.set(stack[:-1])

        return stack[-1]

    def clear(self) -> None:
        """
        Discard every active context scope, at every nesting level.
        """

        _context_stack.set(())
