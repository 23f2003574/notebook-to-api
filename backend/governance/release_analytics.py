from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from .release_manager import Release, ReleaseManager, get_release_manager

_DEFAULT_BUCKET_SECONDS = 86400.0


@dataclass(frozen=True)
class ReleaseMetrics:
    """An aggregated snapshot of release activity over an optional time window."""

    releases_created: int = 0
    successful_publications: int = 0
    failed_releases: int = 0
    verification_pass_rate: Optional[float] = None
    average_release_duration_seconds: Optional[float] = None
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "releases_created": self.releases_created,
            "successful_publications": self.successful_publications,
            "failed_releases": self.failed_releases,
            "verification_pass_rate": self.verification_pass_rate,
            "average_release_duration_seconds": self.average_release_duration_seconds,
            "window_start": self.window_start.isoformat() if self.window_start else None,
            "window_end": self.window_end.isoformat() if self.window_end else None,
        }


@dataclass(frozen=True)
class ReleaseTrend:
    """One bucketed period of release activity within a trend series."""

    period_start: datetime
    period_end: datetime
    releases_created: int = 0
    successful_publications: int = 0
    failed_releases: int = 0

    def to_dict(self) -> dict:
        return {
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "releases_created": self.releases_created,
            "successful_publications": self.successful_publications,
            "failed_releases": self.failed_releases,
        }


class ReleaseAnalyticsService:
    """Collects release lifecycle and verification events into reportable metrics."""

    def __init__(self, release_manager: Optional[ReleaseManager] = None) -> None:
        self._release_manager = release_manager or get_release_manager()
        self._releases: dict[str, Release] = {}
        self._verifications: list = []
        self._lock = Lock()

    def record_release(self, release_id: str, *, timestamp: Optional[datetime] = None) -> None:
        release = self._release_manager.get(release_id)
        with self._lock:
            self._releases[release_id] = release

    def record_verification(
        self, release_id: str, passed: bool, *, timestamp: Optional[datetime] = None
    ) -> None:
        self._release_manager.get(release_id)
        with self._lock:
            self._verifications.append(
                (release_id, bool(passed), timestamp or datetime.now(timezone.utc))
            )

    def releases(self) -> list:
        with self._lock:
            return list(self._releases.values())

    def recent_activity(self, limit: int = 10) -> list:
        releases = sorted(
            self.releases(),
            key=lambda release: release.created_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return releases[:limit]

    def summary(
        self,
        *,
        window_start: Optional[datetime] = None,
        window_end: Optional[datetime] = None,
    ) -> ReleaseMetrics:
        def _in_window(moment: Optional[datetime]) -> bool:
            if moment is None:
                return False
            if window_start is not None and moment < window_start:
                return False
            if window_end is not None and moment > window_end:
                return False
            return True

        releases = [
            release for release in self.releases() if _in_window(release.created_at)
        ]
        verifications = [
            entry for entry in self._verifications if _in_window(entry[2])
        ]

        published = [release for release in releases if release.state == "PUBLISHED"]
        failed = [release for release in releases if release.state == "FAILED"]

        durations = [
            (release.published_at - release.created_at).total_seconds()
            for release in published
            if release.created_at is not None and release.published_at is not None
        ]
        average_duration = sum(durations) / len(durations) if durations else None

        pass_rate = (
            sum(1 for _, passed, _ in verifications if passed) / len(verifications)
            if verifications
            else None
        )

        return ReleaseMetrics(
            releases_created=len(releases),
            successful_publications=len(published),
            failed_releases=len(failed),
            verification_pass_rate=pass_rate,
            average_release_duration_seconds=average_duration,
            window_start=window_start,
            window_end=window_end,
        )

    def trends(self, *, bucket_seconds: float = _DEFAULT_BUCKET_SECONDS) -> list:
        if bucket_seconds <= 0:
            raise ValueError("bucket_seconds must be positive")

        releases = [release for release in self.releases() if release.created_at is not None]
        if not releases:
            return []

        start = min(release.created_at for release in releases)
        end = max(release.created_at for release in releases)
        delta = timedelta(seconds=bucket_seconds)

        trends = []
        bucket_start = start
        while bucket_start <= end:
            bucket_end = bucket_start + delta
            bucket_releases = [
                release
                for release in releases
                if bucket_start <= release.created_at < bucket_end
            ]
            trends.append(
                ReleaseTrend(
                    period_start=bucket_start,
                    period_end=bucket_end,
                    releases_created=len(bucket_releases),
                    successful_publications=sum(
                        1 for release in bucket_releases if release.state == "PUBLISHED"
                    ),
                    failed_releases=sum(
                        1 for release in bucket_releases if release.state == "FAILED"
                    ),
                )
            )
            bucket_start = bucket_end
        return trends


_analytics_service = ReleaseAnalyticsService()


def get_release_analytics_service() -> ReleaseAnalyticsService:
    return _analytics_service


router = APIRouter(prefix="/governance", tags=["governance-release-analytics"])


@router.get("/analytics/releases")
def list_analytics_releases() -> list:
    return [release.to_dict() for release in get_release_analytics_service().releases()]


@router.get("/analytics/releases/summary")
def get_analytics_summary(
    window_start: Optional[str] = Query(default=None),
    window_end: Optional[str] = Query(default=None),
) -> dict:
    try:
        parsed_start = datetime.fromisoformat(window_start) if window_start else None
        parsed_end = datetime.fromisoformat(window_end) if window_end else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    metrics = get_release_analytics_service().summary(
        window_start=parsed_start, window_end=parsed_end
    )
    return metrics.to_dict()


@router.get("/analytics/releases/trends")
def get_analytics_trends(bucket_seconds: float = Query(default=_DEFAULT_BUCKET_SECONDS)) -> list:
    try:
        trends = get_release_analytics_service().trends(bucket_seconds=bucket_seconds)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return [trend.to_dict() for trend in trends]
