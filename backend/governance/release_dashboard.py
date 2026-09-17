from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

from .artifact_registry import ArtifactRegistry, get_artifact_registry
from .release_analytics import ReleaseAnalyticsService, get_release_analytics_service
from .release_manager import ReleaseManager, get_release_manager
from .release_verification import (
    NoVerificationError,
    ReleaseVerificationEngine,
    get_release_verification_engine,
)


class ReleaseDashboardAPI:
    """Read-only aggregation of release, artifact, verification, and analytics data."""

    def __init__(
        self,
        release_manager: Optional[ReleaseManager] = None,
        registry: Optional[ArtifactRegistry] = None,
        verification_engine: Optional[ReleaseVerificationEngine] = None,
        analytics_service: Optional[ReleaseAnalyticsService] = None,
    ) -> None:
        self._release_manager = release_manager or get_release_manager()
        self._registry = registry or get_artifact_registry()
        self._verification_engine = verification_engine or get_release_verification_engine()
        self._analytics_service = analytics_service or get_release_analytics_service()

    def release_status(self) -> dict:
        releases = self._release_manager.history()
        by_state: dict = {}
        for release in releases:
            by_state[release.state] = by_state.get(release.state, 0) + 1

        return {
            "total": len(releases),
            "by_state": by_state,
            "latest": releases[-1].to_dict() if releases else None,
        }

    def artifact_status(self) -> dict:
        artifacts = self._registry.search()
        archived = sum(1 for artifact in artifacts if artifact.archived_at is not None)

        return {
            "total": len(artifacts),
            "archived": archived,
            "active": len(artifacts) - archived,
            "distinct_names": len({artifact.name for artifact in artifacts}),
        }

    def verification_summary(self) -> dict:
        releases = self._release_manager.history()
        verified = passed = failed = not_verified = 0

        for release in releases:
            try:
                report = self._verification_engine.result(release.release_id)
            except NoVerificationError:
                not_verified += 1
                continue
            verified += 1
            if report.passed:
                passed += 1
            else:
                failed += 1

        return {
            "total_releases": len(releases),
            "verified": verified,
            "passed": passed,
            "failed": failed,
            "not_verified": not_verified,
        }

    def analytics(self) -> dict:
        return self._analytics_service.summary().to_dict()

    def overview(self) -> dict:
        return {
            "releases": self.release_status(),
            "artifacts": self.artifact_status(),
            "verifications": self.verification_summary(),
            "analytics": self.analytics(),
            "recent_activity": [
                release.to_dict() for release in self._analytics_service.recent_activity()
            ],
        }


_dashboard_api = ReleaseDashboardAPI()


def get_release_dashboard_api() -> ReleaseDashboardAPI:
    return _dashboard_api


router = APIRouter(prefix="/governance", tags=["governance-release-dashboard"])


@router.get("/dashboard")
def get_dashboard_overview() -> dict:
    return get_release_dashboard_api().overview()


@router.get("/dashboard/releases")
def get_dashboard_releases() -> dict:
    return get_release_dashboard_api().release_status()


@router.get("/dashboard/artifacts")
def get_dashboard_artifacts() -> dict:
    return get_release_dashboard_api().artifact_status()


@router.get("/dashboard/verification")
def get_dashboard_verification() -> dict:
    return get_release_dashboard_api().verification_summary()


@router.get("/dashboard/analytics")
def get_dashboard_analytics() -> dict:
    return get_release_dashboard_api().analytics()
