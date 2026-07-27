from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, HTTPException

from .release_manager import ReleaseManager, UnknownReleaseError, get_release_manager

CHANNEL_KINDS = ("Alpha", "Beta", "Stable", "LTS")


def _new_id() -> str:
    return uuid.uuid4().hex


class ChannelAlreadyExistsError(ValueError):
    pass


class UnknownChannelError(KeyError):
    pass


class NoDefaultChannelError(RuntimeError):
    pass


class NoAssignmentError(RuntimeError):
    pass


class ChannelPolicyError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseChannel:
    """An immutable, named distribution channel pinned to one release tier."""

    channel_id: str
    name: str
    kind: str
    is_default: bool = False
    metadata: dict = field(default_factory=dict)
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "channel_id": self.channel_id,
            "name": self.name,
            "kind": self.kind,
            "is_default": self.is_default,
            "metadata": dict(self.metadata),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass(frozen=True)
class ChannelAssignment:
    """An immutable record of a release's current channel placement."""

    release_id: str
    channel_id: str
    kind: str
    assigned_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "release_id": self.release_id,
            "channel_id": self.channel_id,
            "kind": self.kind,
            "assigned_at": self.assigned_at.isoformat() if self.assigned_at else None,
        }


class ReleaseChannelManager:
    """Routes releases through named Alpha -> Beta -> Stable -> LTS channels."""

    def __init__(self, release_manager: Optional[ReleaseManager] = None) -> None:
        self._release_manager = release_manager or get_release_manager()
        self._channels: dict[str, ReleaseChannel] = {}
        self._by_name: dict[str, str] = {}
        self._assignments: dict[str, ChannelAssignment] = {}
        self._lock = Lock()

    def create_channel(
        self,
        name: str,
        kind: str,
        *,
        is_default: bool = False,
        metadata: Optional[dict] = None,
        timestamp: Optional[datetime] = None,
    ) -> ReleaseChannel:
        if not name:
            raise ValueError("channel name is required")
        if kind not in CHANNEL_KINDS:
            raise ValueError(f"unknown channel kind '{kind}'")

        channel = ReleaseChannel(
            channel_id=_new_id(),
            name=name,
            kind=kind,
            is_default=is_default,
            metadata=dict(metadata or {}),
            created_at=timestamp or datetime.now(timezone.utc),
        )
        with self._lock:
            if name in self._by_name:
                raise ChannelAlreadyExistsError(f"channel '{name}' already exists")
            if is_default:
                for existing_id, existing in self._channels.items():
                    if existing.is_default:
                        self._channels[existing_id] = replace(existing, is_default=False)
            self._channels[channel.channel_id] = channel
            self._by_name[name] = channel.channel_id
        return channel

    def _resolve(self, channel_name: str) -> ReleaseChannel:
        channel_id = self._by_name.get(channel_name)
        if channel_id is None:
            raise UnknownChannelError(channel_name)
        return self._channels[channel_id]

    def _default_channel(self) -> ReleaseChannel:
        for channel in self._channels.values():
            if channel.is_default:
                return channel
        raise NoDefaultChannelError("no default channel configured")

    def _place(
        self, release_id: str, channel: ReleaseChannel, *, timestamp: Optional[datetime]
    ) -> ChannelAssignment:
        self._release_manager.get(release_id)

        assignment = ChannelAssignment(
            release_id=release_id,
            channel_id=channel.channel_id,
            kind=channel.kind,
            assigned_at=timestamp or datetime.now(timezone.utc),
        )
        with self._lock:
            self._assignments[release_id] = assignment
        self._release_manager.attach_channel(release_id, channel.channel_id)
        return assignment

    def assign(
        self,
        release_id: str,
        channel_name: Optional[str] = None,
        *,
        timestamp: Optional[datetime] = None,
    ) -> ChannelAssignment:
        with self._lock:
            channel = self._resolve(channel_name) if channel_name else self._default_channel()
        return self._place(release_id, channel, timestamp=timestamp)

    def promote(
        self,
        release_id: str,
        *,
        target_channel: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ) -> ChannelAssignment:
        current = self._assignments.get(release_id)
        if current is None:
            raise NoAssignmentError(f"'{release_id}' has not been assigned to a channel")

        with self._lock:
            current_channel = self._channels[current.channel_id]
            current_idx = CHANNEL_KINDS.index(current_channel.kind)
            if current_idx == len(CHANNEL_KINDS) - 1:
                raise ChannelPolicyError(
                    f"'{release_id}' is already on the highest channel tier "
                    f"'{CHANNEL_KINDS[-1]}'"
                )
            next_kind = CHANNEL_KINDS[current_idx + 1]

            if target_channel is not None:
                channel = self._resolve(target_channel)
                if channel.kind != next_kind:
                    raise ChannelPolicyError(
                        f"cannot promote '{release_id}' directly to '{channel.kind}'; "
                        f"next tier is '{next_kind}'"
                    )
            else:
                candidates = [c for c in self._channels.values() if c.kind == next_kind]
                if not candidates:
                    raise UnknownChannelError(f"no channel configured for tier '{next_kind}'")
                channel = next((c for c in candidates if c.is_default), candidates[0])

        return self._place(release_id, channel, timestamp=timestamp)

    def list(self) -> list[ReleaseChannel]:
        with self._lock:
            return list(self._channels.values())


_channel_manager = ReleaseChannelManager()


def get_release_channel_manager() -> ReleaseChannelManager:
    return _channel_manager


router = APIRouter(prefix="/governance", tags=["governance-release-channels"])


@router.post("/release-channels")
def create_release_channel(payload: dict = Body(...)) -> dict:
    name = payload.get("name")
    kind = payload.get("kind")
    if not name or not kind:
        raise HTTPException(status_code=422, detail="name and kind are required")

    try:
        channel = get_release_channel_manager().create_channel(
            name,
            kind,
            is_default=payload.get("is_default", False),
            metadata=payload.get("metadata"),
        )
    except ChannelAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return channel.to_dict()


@router.get("/release-channels")
def list_release_channels() -> list[dict]:
    return [channel.to_dict() for channel in get_release_channel_manager().list()]


@router.post("/releases/{release}/channel")
def set_release_channel(release: str, payload: dict = Body(default={})) -> dict:
    manager = get_release_channel_manager()
    try:
        if payload.get("promote"):
            assignment = manager.promote(release, target_channel=payload.get("target_channel"))
        else:
            assignment = manager.assign(release, payload.get("channel"))
    except UnknownReleaseError:
        raise HTTPException(status_code=404, detail="unknown release")
    except UnknownChannelError:
        raise HTTPException(status_code=404, detail="unknown channel")
    except (NoDefaultChannelError, NoAssignmentError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ChannelPolicyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return assignment.to_dict()
