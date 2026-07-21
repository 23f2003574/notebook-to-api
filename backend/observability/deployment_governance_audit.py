from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Callable

# The hash a chain's first record's previous_hash points at: there is
# no real predecessor, so a fixed, obviously-synthetic sentinel (64
# zeros, the same length as a real SHA-256 digest) stands in for it
# rather than an empty string, keeping every record's previous_hash
# the same shape.
GENESIS_HASH = "0" * 64

_HASH_LENGTH = 64


def _is_sha256_hex(value: str) -> bool:
    if len(value) != _HASH_LENGTH:
        return False

    try:
        int(value, 16)

    except ValueError:
        return False

    return True


def _compute_record_hash(
    *,
    sequence: int,
    action: str,
    actor: str,
    resource: str,
    outcome: str,
    occurred_at: datetime,
    metadata: "dict[str, Any]",
    previous_hash: str,
) -> str:
    """
    Deterministically serialize a record's fields and return the
    SHA-256 hex digest.

    Sorted keys and fixed separators mean two records with identical
    field values always hash identically, regardless of the order
    metadata's keys happened to be constructed in — this is what
    "deterministic serialization before hashing" means in practice.
    """

    canonical = json.dumps(
        {
            "sequence": sequence,
            "action": action,
            "actor": actor,
            "resource": resource,
            "outcome": outcome,
            "occurred_at": occurred_at.isoformat(),
            "metadata": dict(metadata),
            "previous_hash": previous_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    )

    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuditRecord:
    """
    One immutable, hash-chained record of a high-value governance
    action.

    record_hash is computed over every other field, including
    previous_hash: tampering with any field of any earlier record
    changes that record's hash, which changes this record's
    previous_hash, which changes this record's own hash — the tamper
    cannot be contained to just the record it touches.
    """

    sequence: int

    action: str

    actor: str

    resource: str

    outcome: str

    occurred_at: datetime

    metadata: "dict[str, Any]"

    previous_hash: str

    record_hash: str

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("sequence must be >= 1")

        for field_name in ("action", "actor", "resource", "outcome"):
            if not getattr(self, field_name):
                raise ValueError(f"{field_name} must not be empty")

        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")

        if not _is_sha256_hex(self.previous_hash):
            raise ValueError(
                "previous_hash must be a 64-character hexadecimal "
                "SHA-256 digest"
            )

        if not _is_sha256_hex(self.record_hash):
            raise ValueError(
                "record_hash must be a 64-character hexadecimal "
                "SHA-256 digest"
            )

        object.__setattr__(
            self, "metadata", MappingProxyType(dict(self.metadata))
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "action": self.action,
            "actor": self.actor,
            "resource": self.resource,
            "outcome": self.outcome,
            "occurred_at": self.occurred_at.isoformat(),
            "metadata": dict(self.metadata),
            "previous_hash": self.previous_hash,
            "record_hash": self.record_hash,
        }


@dataclass(frozen=True)
class AuditQuery:
    """
    Filter criteria for querying audit records. Every field is
    optional except limit: an unfiltered AuditQuery() matches every
    record, capped at the default limit.
    """

    action: "str | None" = None

    actor: "str | None" = None

    resource: "str | None" = None

    limit: int = 100

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ValueError("limit must be greater than zero")

    def matches(self, record: AuditRecord) -> bool:
        if self.action is not None and record.action != self.action:
            return False

        if self.actor is not None and record.actor != self.actor:
            return False

        if self.resource is not None and record.resource != self.resource:
            return False

        return True


@dataclass(frozen=True)
class AuditChainVerification:
    """
    The outcome of verifying an audit trail's hash chain.
    """

    valid: bool

    checked: int

    first_broken_sequence: "int | None"

    reason: "str | None"

    def __post_init__(self) -> None:
        if self.valid:
            if self.first_broken_sequence is not None or self.reason is not None:
                raise ValueError(
                    "first_broken_sequence and reason must not be "
                    "set when valid is True"
                )

        else:
            if self.first_broken_sequence is None or self.reason is None:
                raise ValueError(
                    "first_broken_sequence and reason must be set "
                    "when valid is False"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "checked": self.checked,
            "first_broken_sequence": self.first_broken_sequence,
            "reason": self.reason,
        }


class GovernanceAuditService:
    """
    A tamper-evident, append-only audit trail of high-value governance
    actions (lifecycle transitions, route changes, configuration
    reloads, manual event replay, and so on).

    Unlike GovernanceEventHistory (which stores every runtime event
    for debugging), this records only the actions a caller explicitly
    submits via record() — a deliberately narrower, higher-signal log
    intended for security auditing rather than general-purpose
    troubleshooting.
    """

    def __init__(
        self,
        *,
        clock: "Callable[[], datetime] | None" = None,
    ) -> None:
        self._records: "dict[int, AuditRecord]" = {}

        self._next_sequence = 1

        self._last_hash = GENESIS_HASH

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

    def record(
        self,
        *,
        action: str,
        actor: str,
        resource: str,
        outcome: str,
        metadata: "dict[str, Any] | None" = None,
    ) -> AuditRecord:
        """
        Append a new audit record, chained onto the previous one.

        Sequence numbers are monotonically increasing and never
        reused, even across purge() calls.
        """

        sequence = self._next_sequence

        occurred_at = self._clock()

        metadata = metadata or {}

        previous_hash = self._last_hash

        record_hash = _compute_record_hash(
            sequence=sequence,
            action=action,
            actor=actor,
            resource=resource,
            outcome=outcome,
            occurred_at=occurred_at,
            metadata=metadata,
            previous_hash=previous_hash,
        )

        record = AuditRecord(
            sequence=sequence,
            action=action,
            actor=actor,
            resource=resource,
            outcome=outcome,
            occurred_at=occurred_at,
            metadata=metadata,
            previous_hash=previous_hash,
            record_hash=record_hash,
        )

        self._records[sequence] = record

        self._next_sequence += 1

        self._last_hash = record_hash

        return record

    def get(self, sequence: int) -> AuditRecord:
        """
        Return the audit record with this exact sequence number.

        Raises LookupError if no record has this sequence (never
        existed, or removed via purge()).
        """

        try:
            return self._records[sequence]

        except KeyError:
            raise LookupError(
                f"no audit record with sequence {sequence}"
            ) from None

    def query(
        self,
        query: "AuditQuery | None" = None,
    ) -> "tuple[AuditRecord, ...]":
        """
        Return every audit record matching query, newest first,
        capped at query.limit (or the default limit if query is
        omitted).
        """

        query = query or AuditQuery()

        matches = [
            record
            for record in self._records.values()
            if query.matches(record)
        ]

        matches.sort(key=lambda record: record.sequence, reverse=True)

        return tuple(matches[: query.limit])

    def latest(self, limit: int = 10) -> "tuple[AuditRecord, ...]":
        """
        Return the most recently recorded audit records, newest
        first, capped at limit.
        """

        return self.query(AuditQuery(limit=limit))

    def verify_chain(self) -> AuditChainVerification:
        """
        Walk every audit record in ascending sequence order,
        recomputing each one's expected hash from its own stored
        fields plus the actual preceding record's record_hash (not
        the tampered record's own claimed previous_hash — a rewritten
        previous_hash is exactly what tampering would try, and this
        still catches it), and stop at the first mismatch.

        The oldest currently stored record is a special case: if it
        is sequence 1, its previous_hash must be GENESIS_HASH like
        any other fresh chain's first record; otherwise, purge() has
        removed everything before it, and there is no surviving data
        to check its previous_hash against, so it is trusted as the
        anchor for the rest of the chain rather than reported broken
        for a linkage this service can no longer verify one way or
        the other.

        Records an "audit_verification" record of its own once
        finished, documenting that a verification was performed and
        what it found — this call is not read-only.
        """

        expected_previous = None

        checked = 0

        result: "AuditChainVerification | None" = None

        for index, record in enumerate(
            sorted(self._records.values(), key=lambda r: r.sequence)
        ):
            checked += 1

            if index == 0 and record.sequence == 1:
                expected_previous = GENESIS_HASH

            if (
                expected_previous is not None
                and record.previous_hash != expected_previous
            ):
                result = AuditChainVerification(
                    valid=False,
                    checked=checked,
                    first_broken_sequence=record.sequence,
                    reason=(
                        f"record {record.sequence}'s previous_hash "
                        "does not match the preceding record's hash"
                    ),
                )
                break

            expected_hash = _compute_record_hash(
                sequence=record.sequence,
                action=record.action,
                actor=record.actor,
                resource=record.resource,
                outcome=record.outcome,
                occurred_at=record.occurred_at,
                metadata=record.metadata,
                previous_hash=record.previous_hash,
            )

            if expected_hash != record.record_hash:
                result = AuditChainVerification(
                    valid=False,
                    checked=checked,
                    first_broken_sequence=record.sequence,
                    reason=(
                        f"record {record.sequence}'s hash does not "
                        "match its recorded fields"
                    ),
                )
                break

            expected_previous = record.record_hash

        if result is None:
            result = AuditChainVerification(
                valid=True,
                checked=checked,
                first_broken_sequence=None,
                reason=None,
            )

        self.record(
            action="audit_verification",
            actor="system",
            resource="audit_chain",
            outcome="success" if result.valid else "failure",
            metadata={"checked": result.checked, "valid": result.valid},
        )

        return result

    def purge(self) -> int:
        """
        Remove every audit record, returning how many were removed.

        Deliberately does not reset the sequence counter or the hash
        chain's last hash: resetting either would let a purge quietly
        restart the chain as if nothing had come before, which is
        exactly the kind of history-hiding a tamper-evident audit
        trail exists to prevent. The next recorded action still
        chains onto the (now-invisible) history that came before it.
        """

        count = len(self._records)

        self._records.clear()

        return count

    def size(self) -> int:
        """
        Return the number of currently stored audit records.
        """

        return len(self._records)


# Shared for the lifetime of the process: every governance action
# recorded by the lifecycle manager, event router, and event history
# singletons needs to reach the same audit trail so
# GET /governance/audit (and friends) can see it, regardless of which
# request happened to trigger the action.
_audit_service = GovernanceAuditService()


def get_audit_service() -> GovernanceAuditService:
    """
    Return the process-wide governance audit service.
    """

    return _audit_service
