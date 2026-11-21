from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_policy import (
        GovernancePolicyEngine,
        PolicyDecision,
    )
    from .deployment_governance_rules import RuleEvaluationResult
    from .deployment_governance_recovery import RecoveryResult

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

    outcome: "str | None" = None

    # Only records older than this sequence (a cursor for paging newest-first), and only actions
    # starting with this prefix (e.g. "notebook." for compile and deploy).
    before_sequence: "int | None" = None

    action_prefix: "str | None" = None

    # Match any of these resources (a notebook's current and former names); combines with resource.
    resources: "tuple[str, ...] | None" = None

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ValueError("limit must be greater than zero")

    def matches(self, record: AuditRecord) -> bool:
        if self.outcome is not None and record.outcome != self.outcome:
            return False

        if self.before_sequence is not None and record.sequence >= self.before_sequence:
            return False

        if self.action_prefix is not None and not record.action.startswith(self.action_prefix):
            return False

        if self.action is not None and record.action != self.action:
            return False

        if self.actor is not None and record.actor != self.actor:
            return False

        if self.resource is not None and record.resource != self.resource:
            return False

        if self.resources is not None and record.resource not in self.resources:
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

    # "unkeyed": SHA-256 chain only. It detects accidental and partial tampering but NOT a rewrite
    # by someone who can write the database (they can recompute every hash). "keyed": every
    # record and the head were checked against HMAC tags only the key holder can make.
    # "keyed_unverified": the database is keyed but this process has no matching key.
    integrity: str = "unkeyed"

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
        policy_engine: "GovernancePolicyEngine | None" = None,
        store: "Any | None" = None,
    ) -> None:
        self._records: "dict[int, AuditRecord]" = {}

        # A durable store (SqliteAuditStore) makes every read and write go to the shared database,
        # so records survive restarts and concurrent processes cannot fork the chain.
        self._store = store

        self._lock = threading.RLock()

        self._next_sequence = 1

        self._last_hash = GENESIS_HASH

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._policy_engine = policy_engine

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

        return self._record_impl(
            action=action, actor=actor, resource=resource, outcome=outcome,
            metadata=metadata, idempotency_key=None,
        )[0]

    def record_once(
        self,
        *,
        action: str,
        actor: str,
        resource: str,
        outcome: str,
        idempotency_key: str,
        metadata: "dict[str, Any] | None" = None,
    ) -> "tuple[AuditRecord, bool]":
        """
        Like record(), but at most one record exists per (action, idempotency_key): a retry of the
        same request returns the record already written, with created=False, instead of a second
        one. The key is part of the record's hashed metadata.
        """

        return self._record_impl(
            action=action, actor=actor, resource=resource, outcome=outcome,
            metadata={**(metadata or {}), "idempotency_key": idempotency_key},
            idempotency_key=idempotency_key,
        )

    def _record_impl(
        self,
        *,
        action: str,
        actor: str,
        resource: str,
        outcome: str,
        metadata: "dict[str, Any] | None",
        idempotency_key: "str | None",
    ) -> "tuple[AuditRecord, bool]":

        occurred_at = self._clock()

        metadata = metadata or {}

        def build(sequence: int, previous_hash: str) -> AuditRecord:
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

            return AuditRecord(
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

        if self._store is not None:
            try:
                if idempotency_key is None:
                    written, created = self._store.append(build), True
                else:
                    written, created = self._store.append_once(
                        build, action, idempotency_key
                    )

            except Exception as exc:
                from .deployment_governance_audit_health import audit_health

                audit_health.record_failure(exc)

                exc.audit_failure_counted = True

                raise

            from .deployment_governance_audit_health import audit_health

            audit_health.record_success()

            if not created:
                written = AuditRecord(**written)

            return written, created

        with self._lock:
            if idempotency_key is not None:
                for existing in self._records.values():
                    if (
                        existing.action == action
                        and existing.metadata.get("idempotency_key") == idempotency_key
                    ):
                        return existing, False

            record = build(self._next_sequence, self._last_hash)

            self._records[record.sequence] = record

            self._next_sequence += 1

            self._last_hash = record.record_hash

        from .deployment_governance_audit_health import audit_health

        audit_health.record_success()

        return record, True

    def _all_records(self, sequence: "int | None" = None) -> "dict[int, AuditRecord]":
        if self._store is None:
            with self._lock:
                return dict(self._records)

        return {
            row["sequence"]: AuditRecord(**row)
            for row in self._store.rows(sequence)
        }

    def get(self, sequence: int) -> AuditRecord:
        """
        Return the audit record with this exact sequence number.

        Raises LookupError if no record has this sequence (never
        existed, or removed via purge()).
        """

        try:
            return self._all_records(sequence)[sequence]

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

        if self._store is not None:
            return tuple(
                AuditRecord(**row) for row in self._store.search(query)
            )

        matches = [
            record
            for record in self._all_records().values()
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

    def verify_chain(
        self,
        *,
        record: bool = True,
        idempotency_key: "str | None" = None,
    ) -> AuditChainVerification:
        """
        Walk every audit record in ascending sequence order, recomputing each one's expected hash
        from its own stored fields plus the actual preceding record's record_hash (not the
        claimed previous_hash: a rewritten previous_hash is exactly what tampering would try),
        and stop at the first mismatch. Records are streamed in batches, so memory stays bounded
        however long the history is.

        The oldest stored record is a special case: if it is sequence 1, its previous_hash must be
        GENESIS_HASH; otherwise purge() removed everything before it and it anchors the rest.

        With record=True (the default for library callers) an "audit_verification" record
        documenting the check is appended, once per idempotency_key when one is given. Pass
        record=False for a strictly read-only check.
        """

        expected_previous = None
        checked = 0
        first = last = None
        result: "AuditChainVerification | None" = None
        keyring = self._store.keyring if self._store is not None else None
        keyed_from = self._store.head()["keyed_from"] if self._store is not None else 0
        unverifiable = False

        for current, mac in self._iter_records(with_mac=True):
            checked += 1
            first = first or current
            last = current

            if checked == 1 and current.sequence == 1:
                expected_previous = GENESIS_HASH

            if expected_previous is not None and current.previous_hash != expected_previous:
                result = AuditChainVerification(
                    valid=False,
                    checked=checked,
                    first_broken_sequence=current.sequence,
                    reason=(
                        f"record {current.sequence}'s previous_hash "
                        "does not match the preceding record's hash"
                    ),
                )
                break

            expected_hash = _compute_record_hash(
                sequence=current.sequence,
                action=current.action,
                actor=current.actor,
                resource=current.resource,
                outcome=current.outcome,
                occurred_at=current.occurred_at,
                metadata=current.metadata,
                previous_hash=current.previous_hash,
            )

            if expected_hash != current.record_hash:
                result = AuditChainVerification(
                    valid=False,
                    checked=checked,
                    first_broken_sequence=current.sequence,
                    reason=(
                        f"record {current.sequence}'s hash does not "
                        "match its recorded fields"
                    ),
                )
                break

            if keyed_from and current.sequence >= keyed_from:
                tag = None if keyring is None else (keyring.check_record(mac, current.record_hash) if mac else False)

                if tag is False:
                    result = AuditChainVerification(
                        valid=False,
                        checked=checked,
                        first_broken_sequence=current.sequence,
                        reason=(
                            f"record {current.sequence} failed keyed integrity (rewritten, missing its "
                            "tag, or written with a different key)"
                        ),
                        integrity="keyed",
                    )
                    break

                if tag is None:
                    unverifiable = True

            expected_previous = current.record_hash

        if result is None and self._store is not None:
            result = self._missing_records(first, last, checked)

        if result is None and keyed_from and not unverifiable:
            head = self._store.head()
            tag = head["keyed_from"] == keyed_from and keyring.check_head(
                head["head_mac"], head["last_sequence"], head["last_hash"], head["purged_through"], keyed_from
            ) if head["head_mac"] else False

            if tag is False:
                result = AuditChainVerification(
                    valid=False,
                    checked=checked,
                    first_broken_sequence=head["last_sequence"],
                    reason="the chain head failed keyed integrity (the head or purge point was rewritten)",
                    integrity="keyed",
                )
            elif tag is None:
                unverifiable = True

        if result is None:
            result = AuditChainVerification(
                valid=True,
                checked=checked,
                first_broken_sequence=None,
                reason=None,
                integrity=("keyed_unverified" if unverifiable else "keyed") if keyed_from else "unkeyed",
            )

        if record:
            fields = dict(
                action="audit_verification",
                actor="system",
                resource="audit_chain",
                outcome="success" if result.valid else "failure",
                metadata={"checked": result.checked, "valid": result.valid},
            )

            if idempotency_key is None:
                self.record(**fields)
            else:
                self.record_once(idempotency_key=idempotency_key, **fields)

        return result

    def _iter_records(self, with_mac: bool = False):
        """Every record in ascending sequence order, without materializing the history. With
        with_mac the stored integrity tag comes along as (record, tag); otherwise just records."""

        if self._store is None:
            with self._lock:
                snapshot = sorted(self._records.values(), key=lambda r: r.sequence)

            for record in snapshot:
                yield (record, "") if with_mac else record
            return

        for row in self._store.iter_rows(with_mac=with_mac):
            mac = row.pop("mac", "")
            record = AuditRecord(**row)
            yield (record, mac) if with_mac else record

    def _missing_records(
        self, first: "AuditRecord | None", last: "AuditRecord | None", checked: int
    ) -> "AuditChainVerification | None":
        """
        Compare the first and last stored rows with the chain head and purge point the store
        remembers outside them: a deleted first, middle or last record changes neither a surviving
        record's own hash nor (for the tail) its neighbour's link, so only this comparison can
        notice it.
        """

        head = self._store.head()

        def broken(sequence: int, reason: str) -> AuditChainVerification:
            return AuditChainVerification(
                valid=False,
                checked=checked,
                first_broken_sequence=sequence,
                reason=reason,
            )

        if first is None:
            if head["last_sequence"] != head["purged_through"]:
                return broken(
                    head["purged_through"] + 1,
                    f"records {head['purged_through'] + 1} to "
                    f"{head['last_sequence']} are missing",
                )

            return None

        if first.sequence != head["purged_through"] + 1:
            return broken(
                head["purged_through"] + 1,
                f"records {head['purged_through'] + 1} to "
                f"{first.sequence - 1} are missing",
            )

        if last.sequence != head["last_sequence"] or last.record_hash != head["last_hash"]:
            return broken(
                last.sequence + 1,
                f"records after {last.sequence} are missing "
                f"(the chain should end at {head['last_sequence']})",
            )

        return None

    def head(self) -> dict:
        """The chain head, for anchoring outside this database (a log, a ticket, a signed
        timestamp): record that last_sequence has last_hash, and compare later. It carries the
        keyed tag when the chain is keyed, never the key."""

        if self._store is None:
            with self._lock:
                return {"last_sequence": self._next_sequence - 1, "last_hash": self._last_hash,
                        "purged_through": 0, "integrity": "unkeyed", "keyed_from": None, "head_mac": None}

        head = self._store.head()

        return {
            "last_sequence": head["last_sequence"],
            "last_hash": head["last_hash"],
            "purged_through": head["purged_through"],
            "integrity": "keyed" if head["keyed_from"] else "unkeyed",
            "keyed_from": head["keyed_from"] or None,
            "head_mac": head["head_mac"] or None,
        }

    def purge(self) -> int:
        """
        Remove every audit record, returning how many were removed.

        Deliberately does not reset the sequence counter or the hash
        chain's last hash: resetting either would let a purge quietly
        restart the chain as if nothing had come before, which is
        exactly the kind of history-hiding a tamper-evident audit
        trail exists to prevent. The next recorded action still
        chains onto the (now-invisible) history that came before it.

        If this service was constructed with a policy_engine, purging
        is itself a policy-protected operation ("audit_purge"): a
        denying policy raises GovernancePolicyViolation before
        anything is removed. Either way the decision is recorded —
        on denial, immediately (nothing was cleared, so it simply
        appends); on approval, after the clear completes, so the
        record documenting that this purge was authorized is not
        itself wiped by the purge it authorized.
        """

        decision = None

        if self._policy_engine is not None:
            from .deployment_governance_policy import (
                GovernancePolicyViolation,
            )

            decision = self._policy_engine.evaluate("audit_purge", {})

            if not decision.allowed:
                record_policy_decision(self, "audit_purge", decision)

                raise GovernancePolicyViolation(decision)

        if self._store is not None:
            count = self._store.clear()

        else:
            with self._lock:
                count = len(self._records)

                self._records.clear()

        if decision is not None:
            record_policy_decision(self, "audit_purge", decision)

        return count

    def size(self) -> int:
        """
        Return the number of currently stored audit records.
        """

        if self._store is not None:
            return self._store.count()

        return len(self._records)


def record_policy_decision(
    audit_service: GovernanceAuditService,
    operation: str,
    decision: "PolicyDecision",
) -> None:
    """
    Record a PolicyDecision as a "policy_evaluation" audit entry.

    Shared by every component that enforces
    GovernancePolicyEngine.evaluate() results through this audit
    trail (the lifecycle manager, the event router, and this
    service's own purge()), so the same decision is always recorded
    in the same shape regardless of which operation it came from.
    """

    audit_service.record(
        action="policy_evaluation",
        actor="system",
        resource=f"policy:{operation}",
        outcome="success" if decision.allowed else "denied",
        metadata={
            "operation": operation,
            "policy": decision.policy,
            "reason": decision.reason,
            "allowed": decision.allowed,
        },
    )


def record_rule_evaluation(
    audit_service: GovernanceAuditService,
    result: "RuleEvaluationResult",
) -> None:
    """
    Record a RuleEvaluationResult as a "rule_evaluation" audit entry.

    GovernanceRuleEngine has no audit dependency of its own (it stays
    a standalone, reusable predicate engine); callers that want a
    rule's outcome captured in the tamper-evident audit trail — the
    GET/POST /governance/rules/evaluate endpoint, or a policy
    consulting a named rule — call this explicitly with their own
    audit_service, the same way record_policy_decision is called by
    whichever component enforces a policy rather than by the policy
    engine itself.
    """

    audit_service.record(
        action="rule_evaluation",
        actor="system",
        resource=f"rule:{result.rule}",
        outcome="success" if result.passed else "failure",
        metadata=result.to_dict(),
    )


def record_recovery_result(
    audit_service: GovernanceAuditService,
    action: str,
    result: "RecoveryResult",
) -> None:
    """
    Record a RecoveryResult as an audit entry under action (typically
    "recovery_succeeded", "recovery_failed", or "recovery_aborted" —
    passed explicitly rather than derived from result.success, since
    an aborted recovery and a failed one are both success=False but
    are meaningfully different outcomes worth distinguishing in the
    audit trail).

    Mirrors record_policy_decision and record_rule_evaluation: a
    shared, reusable recording shape rather than each caller building
    its own metadata dict.
    """

    audit_service.record(
        action=action,
        actor="system",
        resource=f"component:{result.component}",
        outcome="success" if result.success else "failure",
        metadata=result.to_dict(),
    )


# Shared for the lifetime of the process: every governance action
# recorded by the lifecycle manager, event router, and event history
# singletons needs to reach the same audit trail so
# GET /governance/audit (and friends) can see it, regardless of which
# request happened to trigger the action. Wired to the process-wide
# policy engine (a plain top-level import, not deferred:
# deployment_governance_policy has no dependency on this module, so
# there is no circular import to avoid) so purge() is itself
# policy-protected.
from .deployment_governance_policy import get_policy_engine  # noqa: E402

_audit_service: "GovernanceAuditService | None" = None


def _production_environment(environ) -> bool:
    return environ.get("NOTEBOOK2API_ENVIRONMENT", "").strip().lower() in {"production", "prod"}


def build_audit_service_from_env(environ=None) -> "GovernanceAuditService":
    """Build the audit service the environment asks for.

    In production the backend must be chosen explicitly: an unset backend is a startup error rather
    than a silent fall back to process-local memory. "memory" stays available when it is set on
    purpose (development and tests); "sqlite" gives a durable, multi-process-safe chain."""

    import os

    from .deployment_governance_audit_store import (
        GovernanceAuditConfigurationError as GovernanceAuditStorageError,
        SqliteAuditStore,
    )

    environ = os.environ if environ is None else environ
    backend = environ.get("NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "").strip().lower()

    if not backend and _production_environment(environ):
        raise GovernanceAuditStorageError(
            "NOTEBOOK2API_ENVIRONMENT=production requires NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND "
            "to be set explicitly: use 'sqlite' (with NOTEBOOK2API_GOVERNANCE_DATABASE_PATH on a "
            "persistent volume), or 'memory' to knowingly accept that audit records are lost on restart"
        )

    if backend not in ("", "memory", "sqlite"):
        raise GovernanceAuditStorageError(
            f"unsupported NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND {backend!r}; expected 'memory' or 'sqlite'"
        )

    key = environ.get("NOTEBOOK2API_GOVERNANCE_AUDIT_HMAC_KEY", "").strip()

    if key and backend != "sqlite":
        raise GovernanceAuditStorageError(
            "NOTEBOOK2API_GOVERNANCE_AUDIT_HMAC_KEY needs NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND=sqlite: "
            "keyed integrity protects a stored chain, and a process-local one has nothing to protect"
        )

    if backend == "sqlite":
        from .deployment_governance_audit_store import AuditKeyring

        path = environ.get("NOTEBOOK2API_GOVERNANCE_DATABASE_PATH", "data/notebook2api.db")
        keyring = None
        if key:
            previous = {}
            for entry in environ.get("NOTEBOOK2API_GOVERNANCE_AUDIT_HMAC_PREVIOUS_KEYS", "").split(","):
                if entry.strip():
                    epoch, _, old_key = entry.strip().partition(":")
                    previous[epoch] = old_key
            keyring = AuditKeyring(
                key, environ.get("NOTEBOOK2API_GOVERNANCE_AUDIT_HMAC_KEY_EPOCH", "").strip() or "1", previous
            )
        allow_loss = environ.get("NOTEBOOK2API_GOVERNANCE_AUDIT_ALLOW_KEY_LOSS", "").strip().lower() in {"1", "true", "yes"}
        return GovernanceAuditService(
            policy_engine=get_policy_engine(),
            store=SqliteAuditStore(path, keyring=keyring, allow_key_loss=allow_loss),
        )

    return GovernanceAuditService(policy_engine=get_policy_engine())


def get_audit_service() -> GovernanceAuditService:
    """
    Return the process-wide governance audit service (created on first use from the environment).
    """

    global _audit_service

    if _audit_service is None:
        _audit_service = build_audit_service_from_env()

    return _audit_service


def reset_audit_service() -> None:
    """Forget the process-wide service so the next get_audit_service() re-reads the environment."""

    global _audit_service

    _audit_service = None
