from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, HTTPException

from .artifact_registry import ArtifactRegistry, UnknownArtifactError, get_artifact_registry
from .artifact_replication import ArtifactReplicationService, get_artifact_replication_service
from .release_manager import Release, ReleaseManager, UnknownReleaseError, get_release_manager

VERIFICATION_PROFILES = {
    "default": (
        "Artifact Exists",
        "Checksum Valid",
        "Policy Passed",
        "Release Metadata Complete",
    ),
    "quick": ("Artifact Exists", "Policy Passed"),
}


def _new_id() -> str:
    return uuid.uuid4().hex


class UnknownProfileError(ValueError):
    pass


class UnknownCheckError(KeyError):
    pass


class NoVerificationError(KeyError):
    pass


@dataclass(frozen=True)
class VerificationCheck:
    """The outcome of one named check run against a release."""

    name: str
    passed: bool
    message: Optional[str] = None

    def to_dict(self) -> dict:
        return {"name": self.name, "passed": self.passed, "message": self.message}


@dataclass(frozen=True)
class VerificationReport:
    """An immutable snapshot of one verification pass over a release."""

    report_id: str
    release_id: str
    passed: bool
    checks: tuple = ()
    profile: str = "default"
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "report_id": self.report_id,
            "release_id": self.release_id,
            "passed": self.passed,
            "checks": [check.to_dict() for check in self.checks],
            "profile": self.profile,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ReleaseVerificationEngine:
    """Runs a configurable pipeline of checks against a release before publication."""

    def __init__(
        self,
        release_manager: Optional[ReleaseManager] = None,
        registry: Optional[ArtifactRegistry] = None,
        replication_service: Optional[ArtifactReplicationService] = None,
    ) -> None:
        self._release_manager = release_manager or get_release_manager()
        self._registry = registry or get_artifact_registry()
        self._replication_service = replication_service or get_artifact_replication_service()
        self._history: dict[str, list[VerificationReport]] = {}
        self._lock = Lock()

    def _check_artifact_exists(self, release: Release) -> VerificationCheck:
        for artifact in release.artifacts:
            try:
                self._registry.find(artifact["name"], artifact["version"])
            except UnknownArtifactError:
                return VerificationCheck(
                    name="Artifact Exists",
                    passed=False,
                    message=f"'{artifact['name']}@{artifact['version']}' not found in registry",
                )
        return VerificationCheck(name="Artifact Exists", passed=True)

    def _check_checksum_valid(self, release: Release) -> VerificationCheck:
        for artifact in release.artifacts:
            try:
                record = self._registry.find(artifact["name"], artifact["version"])
            except UnknownArtifactError:
                return VerificationCheck(
                    name="Checksum Valid",
                    passed=False,
                    message=f"'{artifact['name']}@{artifact['version']}' not found in registry",
                )
            replicas = self._replication_service.status(record.artifact_id)
            if any(not replica.checksum_verified for replica in replicas):
                return VerificationCheck(
                    name="Checksum Valid",
                    passed=False,
                    message=f"'{artifact['name']}@{artifact['version']}' has an unverified replica",
                )
        return VerificationCheck(name="Checksum Valid", passed=True)

    def _check_policy_passed(self, release: Release) -> VerificationCheck:
        passed = release.policy_passed is True
        return VerificationCheck(
            name="Policy Passed",
            passed=passed,
            message=None if passed else "release has not passed policy validation",
        )

    def _check_release_metadata_complete(self, release: Release) -> VerificationCheck:
        missing = []
        if release.notes_id is None:
            missing.append("release notes")
        if release.channel_id is None:
            missing.append("channel assignment")
        return VerificationCheck(
            name="Release Metadata Complete",
            passed=not missing,
            message=None if not missing else f"missing {', '.join(missing)}",
        )

    def _run_check(self, name: str, release: Release) -> VerificationCheck:
        if name == "Artifact Exists":
            return self._check_artifact_exists(release)
        if name == "Checksum Valid":
            return self._check_checksum_valid(release)
        if name == "Policy Passed":
            return self._check_policy_passed(release)
        if name == "Release Metadata Complete":
            return self._check_release_metadata_complete(release)
        raise UnknownCheckError(name)

    def run_checks(self, release_id: str, *, profile: str = "default") -> list:
        release = self._release_manager.get(release_id)
        check_names = VERIFICATION_PROFILES.get(profile)
        if check_names is None:
            raise UnknownProfileError(f"unknown verification profile '{profile}'")

        return [self._run_check(name, release) for name in check_names]

    def verify(
        self,
        release_id: str,
        *,
        profile: str = "default",
        timestamp: Optional[datetime] = None,
    ) -> VerificationReport:
        checks = self.run_checks(release_id, profile=profile)
        now = timestamp or datetime.now(timezone.utc)

        report = VerificationReport(
            report_id=_new_id(),
            release_id=release_id,
            passed=all(check.passed for check in checks),
            checks=tuple(checks),
            profile=profile,
            created_at=now,
        )
        with self._lock:
            self._history.setdefault(release_id, []).append(report)
        self._release_manager.mark_verified(release_id, report.passed, timestamp=now)
        return report

    def result(self, release_id: str) -> VerificationReport:
        history = self.history(release_id)
        if not history:
            raise NoVerificationError(release_id)
        return history[-1]

    def history(self, release_id: str) -> list:
        with self._lock:
            return list(self._history.get(release_id, []))


_verification_engine = ReleaseVerificationEngine()


def get_release_verification_engine() -> ReleaseVerificationEngine:
    return _verification_engine


router = APIRouter(prefix="/governance", tags=["governance-release-verification"])


@router.post("/releases/{release}/verify")
def verify_release(release: str, payload: dict = Body(default={})) -> dict:
    profile = payload.get("profile", "default")
    try:
        report = get_release_verification_engine().verify(release, profile=profile)
    except UnknownReleaseError:
        raise HTTPException(status_code=404, detail="unknown release")
    except UnknownProfileError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return report.to_dict()


@router.get("/releases/{release}/verification")
def get_verification_result(release: str) -> dict:
    try:
        report = get_release_verification_engine().result(release)
    except NoVerificationError:
        raise HTTPException(status_code=404, detail="release has not been verified")
    return report.to_dict()


@router.get("/releases/{release}/verification/history")
def get_verification_history(release: str) -> list:
    return [report.to_dict() for report in get_release_verification_engine().history(release)]
