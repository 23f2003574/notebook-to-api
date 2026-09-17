from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Callable, Optional
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Query


class EventType(str, Enum):
    PLUGIN_LOADED = "PluginLoaded"
    PLUGIN_ENABLED = "PluginEnabled"
    REQUEST_STARTED = "RequestStarted"
    REQUEST_COMPLETED = "RequestCompleted"
    COMPILATION_FINISHED = "CompilationFinished"


class UnknownHookError(KeyError):
    pass


class HookAlreadyRegisteredError(ValueError):
    pass


class UnknownSubscriptionError(KeyError):
    pass


def _event_type_value(event_type) -> str:
    return event_type.value if isinstance(event_type, EventType) else event_type


@dataclass(frozen=True)
class PluginEvent:
    """A single occurrence of a runtime event, published through the event bus."""

    event_type: str
    payload: dict
    source: Optional[str]
    timestamp: datetime

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "payload": dict(self.payload),
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass(frozen=True)
class HookRegistration:
    """A plugin's subscription to an event type."""

    hook_id: str
    event_type: str
    plugin: str
    handler: Callable
    priority: int = 0
    filter_fn: Optional[Callable] = field(default=None, compare=False)

    def to_dict(self) -> dict:
        return {
            "hook_id": self.hook_id,
            "event_type": self.event_type,
            "plugin": self.plugin,
            "priority": self.priority,
        }


class HookEventSystem:
    """A publish/subscribe event bus that lets plugins react to runtime events.

    Subscriptions are ordered by ``priority`` (lower runs first). Handlers may
    be plain callables or coroutine functions; :meth:`emit` dispatches sync
    handlers directly and best-effort schedules async ones, while
    :meth:`aemit` awaits both kinds properly from async code.
    """

    def __init__(self) -> None:
        self._hooks: dict = {}
        self._subscriptions: dict = {}
        self._by_event_type: dict = {}
        self._event_log: list = []
        self._lock = Lock()

    def _is_known_event_type(self, event_type: str) -> bool:
        return event_type in {member.value for member in EventType} or event_type in self._hooks

    def register_hook(self, name: str, description: str = "") -> dict:
        with self._lock:
            if name in self._hooks:
                raise HookAlreadyRegisteredError(name)
            self._hooks[name] = {"name": name, "description": description}
            return dict(self._hooks[name])

    def unregister_hook(self, name: str) -> None:
        with self._lock:
            if name not in self._hooks:
                raise UnknownHookError(name)
            del self._hooks[name]

    def subscribe(
        self,
        event_type,
        plugin: str,
        handler: Callable,
        *,
        priority: int = 0,
        filter_fn: Optional[Callable] = None,
    ) -> HookRegistration:
        event_type_value = _event_type_value(event_type)
        if not self._is_known_event_type(event_type_value):
            raise UnknownHookError(event_type_value)
        registration = HookRegistration(
            hook_id=uuid4().hex,
            event_type=event_type_value,
            plugin=plugin,
            handler=handler,
            priority=priority,
            filter_fn=filter_fn,
        )
        with self._lock:
            self._subscriptions[registration.hook_id] = registration
            self._by_event_type.setdefault(event_type_value, []).append(registration.hook_id)
        return registration

    def unsubscribe(self, hook_id: str) -> None:
        with self._lock:
            registration = self._subscriptions.pop(hook_id, None)
            if registration is None:
                raise UnknownSubscriptionError(hook_id)
            ids = self._by_event_type.get(registration.event_type)
            if ids and hook_id in ids:
                ids.remove(hook_id)

    def _matching_registrations(self, event: PluginEvent) -> list:
        with self._lock:
            hook_ids = list(self._by_event_type.get(event.event_type, ()))
            registrations = [self._subscriptions[hid] for hid in hook_ids if hid in self._subscriptions]
        registrations.sort(key=lambda registration: registration.priority)
        return [
            registration
            for registration in registrations
            if registration.filter_fn is None or registration.filter_fn(event)
        ]

    def _record_event(self, event_type, payload, source) -> PluginEvent:
        event = PluginEvent(
            event_type=_event_type_value(event_type),
            payload=dict(payload or {}),
            source=source,
            timestamp=datetime.now(timezone.utc),
        )
        with self._lock:
            self._event_log.append(event)
        return event

    def emit(self, event_type, payload: Optional[dict] = None, source: Optional[str] = None) -> PluginEvent:
        """Publish an event synchronously. Async handlers are dispatched best-effort."""
        event = self._record_event(event_type, payload, source)
        for registration in self._matching_registrations(event):
            if inspect.iscoroutinefunction(registration.handler):
                self._dispatch_async_best_effort(registration.handler, event)
            else:
                registration.handler(event)
        return event

    def _dispatch_async_best_effort(self, handler: Callable, event: PluginEvent) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(handler(event))
        else:
            loop.create_task(handler(event))

    async def aemit(self, event_type, payload: Optional[dict] = None, source: Optional[str] = None) -> PluginEvent:
        """Publish an event and await every handler, sync or async, before returning."""
        event = self._record_event(event_type, payload, source)
        for registration in self._matching_registrations(event):
            if inspect.iscoroutinefunction(registration.handler):
                await registration.handler(event)
            else:
                registration.handler(event)
        return event

    def list_events(
        self,
        event_type: Optional[str] = None,
        source: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list:
        with self._lock:
            events = list(self._event_log)
        if event_type is not None:
            events = [event for event in events if event.event_type == event_type]
        if source is not None:
            events = [event for event in events if event.source == source]
        if limit is not None:
            events = events[-limit:]
        return events

    def list_subscriptions(self, event_type: Optional[str] = None) -> list:
        with self._lock:
            if event_type is not None:
                hook_ids = list(self._by_event_type.get(event_type, ()))
                return [self._subscriptions[hid] for hid in hook_ids if hid in self._subscriptions]
            return list(self._subscriptions.values())


_hook_event_system = HookEventSystem()


def get_hook_event_system() -> HookEventSystem:
    return _hook_event_system


router = APIRouter(prefix="/plugins", tags=["plugins-events"])


@router.post("/hooks", status_code=201)
def register_hook_endpoint(
    payload: dict = Body(default={}),
    system: HookEventSystem = Depends(get_hook_event_system),
) -> dict:
    name = payload.get("name", "")
    if not name:
        raise HTTPException(status_code=422, detail="hook name is required")
    try:
        return system.register_hook(name, payload.get("description", ""))
    except HookAlreadyRegisteredError as exc:
        raise HTTPException(status_code=409, detail=f"hook '{exc}' is already registered")


@router.delete("/hooks/{hook}", status_code=204)
def unregister_hook_endpoint(
    hook: str,
    system: HookEventSystem = Depends(get_hook_event_system),
) -> None:
    try:
        system.unregister_hook(hook)
    except UnknownHookError:
        raise HTTPException(status_code=404, detail="unknown hook")


@router.get("/events")
def list_events_endpoint(
    event_type: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    limit: Optional[int] = Query(default=None),
    system: HookEventSystem = Depends(get_hook_event_system),
) -> list:
    return [event.to_dict() for event in system.list_events(event_type=event_type, source=source, limit=limit)]
