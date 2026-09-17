from __future__ import annotations

from dataclasses import dataclass

from .deployment_governance_audit_history import (
    GovernanceIntegrityAuditRecord,
)
from .deployment_governance_audit_replay import (
    GovernanceIntegrityAuditReplayService,
)

# Only operational fields are compared. audit_id and timestamps are
# identity/provenance, not audit outcome, so they are deliberately
# excluded from the diff.
_COMPARABLE_FIELDS: tuple[str, ...] = (
    "healthy",
    "total_records",
    "valid_records",
    "invalid_records",
    "integrity_mismatches",
    "missing_integrity_metadata",
    "invalid_integrity_metadata",
    "invalid_persisted_records",
)


@dataclass(frozen=True)
class GovernanceIntegrityAuditFieldDiff:
    """
    One operational field that differs between two replayed audits.
    """

    field: str

    previous: object

    current: object

    def to_dict(self) -> dict[str, object]:
        return {
            "field": self.field,
            "previous": self.previous,
            "current": self.current,
        }


@dataclass(frozen=True)
class GovernanceIntegrityAuditReplayDiff:
    """
    Structured comparison of two replayed governance integrity audits.
    """

    previous_audit_id: str

    current_audit_id: str

    changed: bool

    field_diffs: tuple[
        GovernanceIntegrityAuditFieldDiff,
        ...
    ]

    def __post_init__(self) -> None:
        if self.changed != bool(self.field_diffs):
            raise ValueError(
                "changed must match whether field_diffs is non-empty"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "previous_audit_id": self.previous_audit_id,
            "current_audit_id": self.current_audit_id,
            "changed": self.changed,
            "field_diffs": [
                field_diff.to_dict()
                for field_diff in self.field_diffs
            ],
        }


def _build_replay_diff(
    *,
    previous: GovernanceIntegrityAuditRecord,
    current: GovernanceIntegrityAuditRecord,
) -> GovernanceIntegrityAuditReplayDiff:
    field_diffs = tuple(
        GovernanceIntegrityAuditFieldDiff(
            field=field_name,
            previous=getattr(previous, field_name),
            current=getattr(current, field_name),
        )
        for field_name in _COMPARABLE_FIELDS
        if getattr(previous, field_name)
        != getattr(current, field_name)
    )

    return GovernanceIntegrityAuditReplayDiff(
        previous_audit_id=previous.audit_id,
        current_audit_id=current.audit_id,
        changed=bool(field_diffs),
        field_diffs=field_diffs,
    )


class GovernanceIntegrityAuditReplayDiffService:
    """
    Compares two replayed governance integrity audits.

    Built on top of GovernanceIntegrityAuditReplayService rather than a
    repository directly, so a diff is always the result of replaying
    stored history, never a fresh audit execution.
    """

    def __init__(
        self,
        replay_service: GovernanceIntegrityAuditReplayService,
    ) -> None:
        self._replay_service = replay_service

    def compare(
        self,
        previous_audit_id: str,
        current_audit_id: str,
    ) -> GovernanceIntegrityAuditReplayDiff:
        """
        Replay and compare two audits by identifier.

        Raises KeyError if either audit cannot be found.
        """

        previous_replay = self._replay_service.replay(
            previous_audit_id
        )

        current_replay = self._replay_service.replay(
            current_audit_id
        )

        return _build_replay_diff(
            previous=previous_replay.record,
            current=current_replay.record,
        )

    def compare_latest(
        self,
    ) -> GovernanceIntegrityAuditReplayDiff:
        """
        Compare the most recent audit against the one immediately
        preceding it.

        Raises LookupError if fewer than two audits have been recorded.
        """

        replays = self._replay_service.replay_recent(limit=2)

        if len(replays) < 2:
            raise LookupError(
                "at least two governance integrity audits are "
                "required to compare the latest two"
            )

        current_replay, previous_replay = replays

        return _build_replay_diff(
            previous=previous_replay.record,
            current=current_replay.record,
        )
