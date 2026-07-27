from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Iterable, Optional

from fastapi import APIRouter, Body, HTTPException

from .release_manager import ReleaseManager, UnknownReleaseError, get_release_manager

SECTION_TYPES = ("Features", "Bug Fixes", "Breaking Changes", "Dependencies", "Known Issues")

_DEFAULT_TEMPLATE = "Release Notes for {release_id}"

_CATEGORY_PREFIXES = {
    "Features": ("feat",),
    "Bug Fixes": ("fix",),
    "Dependencies": ("chore(deps)", "deps", "dependency"),
}


def _new_id() -> str:
    return uuid.uuid4().hex


class NoReleaseNotesError(KeyError):
    pass


def _categorize(commit_message: str) -> Optional[str]:
    normalized = commit_message.strip().lower()
    if not normalized:
        return None
    if normalized.startswith("breaking") or "breaking change" in normalized:
        return "Breaking Changes"
    for category, prefixes in _CATEGORY_PREFIXES.items():
        if any(normalized.startswith(prefix) for prefix in prefixes):
            return category
    return None


@dataclass(frozen=True)
class ReleaseSection:
    """One categorized section of a generated release notes document."""

    title: str
    entries: tuple = ()

    def to_dict(self) -> dict:
        return {"title": self.title, "entries": list(self.entries)}

    def to_markdown(self) -> str:
        if not self.entries:
            return ""
        lines = [f"## {self.title}", ""]
        lines.extend(f"- {entry}" for entry in self.entries)
        return "\n".join(lines)


@dataclass(frozen=True)
class ReleaseNotes:
    """An immutable, renderable set of release notes for one release."""

    notes_id: Optional[str]
    release_id: str
    title: str
    sections: tuple = ()
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "notes_id": self.notes_id,
            "release_id": self.release_id,
            "title": self.title,
            "sections": [section.to_dict() for section in self.sections],
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def to_markdown(self) -> str:
        blocks = [f"# {self.title}"]
        blocks.extend(
            section.to_markdown() for section in self.sections if section.entries
        )
        return "\n\n".join(block for block in blocks if block)


class ReleaseNotesGenerator:
    """Aggregates commits into templated, exportable release notes."""

    def __init__(self, release_manager: Optional[ReleaseManager] = None) -> None:
        self._release_manager = release_manager or get_release_manager()
        self._history: dict[str, list[ReleaseNotes]] = {}
        self._lock = Lock()

    def _build(
        self,
        release_id: str,
        *,
        commits: Iterable[str],
        overrides: Optional[dict],
        template: Optional[str],
        notes_id: Optional[str],
        timestamp: Optional[datetime],
    ) -> ReleaseNotes:
        sections_map: dict[str, list[str]] = {title: [] for title in SECTION_TYPES}

        for commit in commits:
            category = _categorize(commit)
            if category:
                sections_map[category].append(commit)

        for title, entries in (overrides or {}).items():
            if title not in SECTION_TYPES:
                raise ValueError(f"unknown section '{title}'")
            sections_map[title].extend(entries)

        sections = tuple(
            ReleaseSection(title=title, entries=tuple(sections_map[title]))
            for title in SECTION_TYPES
        )
        title = (template or _DEFAULT_TEMPLATE).format(release_id=release_id)

        return ReleaseNotes(
            notes_id=notes_id,
            release_id=release_id,
            title=title,
            sections=sections,
            created_at=timestamp,
        )

    def generate(
        self,
        release_id: str,
        *,
        commits: Iterable[str] = (),
        overrides: Optional[dict] = None,
        template: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ) -> ReleaseNotes:
        self._release_manager.get(release_id)
        now = timestamp or datetime.now(timezone.utc)

        notes = self._build(
            release_id,
            commits=commits,
            overrides=overrides,
            template=template,
            notes_id=_new_id(),
            timestamp=now,
        )
        with self._lock:
            self._history.setdefault(release_id, []).append(notes)
        self._release_manager.attach_notes(release_id, notes.notes_id)
        return notes

    def preview(
        self,
        release_id: str,
        *,
        commits: Iterable[str] = (),
        overrides: Optional[dict] = None,
        template: Optional[str] = None,
    ) -> ReleaseNotes:
        self._release_manager.get(release_id)
        return self._build(
            release_id,
            commits=commits,
            overrides=overrides,
            template=template,
            notes_id=None,
            timestamp=None,
        )

    def export(self, release_id: str) -> str:
        history = self.history(release_id)
        if not history:
            raise NoReleaseNotesError(release_id)
        return history[-1].to_markdown()

    def history(self, release_id: str) -> list[ReleaseNotes]:
        with self._lock:
            return list(self._history.get(release_id, []))


_notes_generator = ReleaseNotesGenerator()


def get_release_notes_generator() -> ReleaseNotesGenerator:
    return _notes_generator


router = APIRouter(prefix="/governance", tags=["governance-release-notes"])


@router.post("/releases/{release}/notes")
def generate_release_notes(release: str, payload: dict = Body(default={})) -> dict:
    try:
        notes = get_release_notes_generator().generate(
            release,
            commits=payload.get("commits", ()),
            overrides=payload.get("overrides"),
            template=payload.get("template"),
        )
    except UnknownReleaseError:
        raise HTTPException(status_code=404, detail="unknown release")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return notes.to_dict()


@router.get("/releases/{release}/notes")
def get_release_notes(release: str) -> dict:
    history = get_release_notes_generator().history(release)
    if not history:
        raise HTTPException(status_code=404, detail="no release notes generated")
    return history[-1].to_dict()
