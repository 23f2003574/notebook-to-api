from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_event_bus import GovernanceEventBus

# The built-in verification checks this verifier ships with,
# selectable by name via register_rule()'s algorithm parameter when no
# custom evaluator is given — the same plug-in shape
# BUILT_IN_ROLLOUT_POLICIES established. Named "algorithm" (matching
# IntegrityRule's own field) even though two of these (Artifact Size,
# Manifest Consistency) are not cryptographic hash algorithms — that
# is simply the vocabulary this verifier's rules are scoped to.
# Additional signature or attestation mechanisms can be plugged in
# later via register_rule()'s evaluator parameter, without modifying
# this class.
BUILT_IN_VERIFICATION_ALGORITHMS: "tuple[str, ...]" = (
    "SHA-256",
    "SHA-512",
    "Artifact Size",
    "Manifest Consistency",
)

# A rule's evaluator decides whether an artifact's content passes it,
# given context — a bare bool, matching RiskRuleEvaluator's own shape
# (no per-rule reason: IntegrityReport carries no per-rule breakdown,
# only an aggregate verified flag).
IntegrityRuleEvaluator = Callable[
    ["IntegrityRule", str, "dict[str, Any]"], bool
]


def _to_bytes(content: "str | bytes") -> bytes:
    return content.encode("utf-8") if isinstance(content, str) else content


@dataclass(frozen=True)
class IntegrityReport:
    """
    The immutable outcome of one verify() call: whether artifact_id
    passed every enabled rule, and its canonical SHA-256 checksum —
    always computed, independent of which rules happen to be
    registered, so a report always has a stable identifying checksum
    even with zero rules configured.
    """

    artifact_id: str

    verified: bool

    checksum: str

    def __post_init__(self) -> None:
        if not self.artifact_id:
            raise ValueError("artifact_id must not be empty")

        if not self.checksum:
            raise ValueError("checksum must not be empty")

    def to_dict(self) -> dict[str, object]:
        return {
            "artifact_id": self.artifact_id,
            "verified": self.verified,
            "checksum": self.checksum,
        }


@dataclass(frozen=True)
class IntegrityRule:
    """
    A named verification rule, scoped to an algorithm
    (BUILT_IN_VERIFICATION_ALGORITHMS or a custom one) and
    independently enabled/disabled — "configurable verification
    rules".
    """

    name: str

    algorithm: str

    enabled: bool

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name must not be empty")

        if not self.algorithm:
            raise ValueError("algorithm must not be empty")

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "algorithm": self.algorithm,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class IntegrityVerificationSummary:
    """
    An immutable, point-in-time aggregate over the verification rule
    registry and every cached report across every artifact this
    verifier has verified.
    """

    total_rules: int

    enabled_rules: int

    disabled_rules: int

    total_artifacts_verified: int

    total_verifications: int

    failed_verifications: int

    def __post_init__(self) -> None:
        if self.enabled_rules + self.disabled_rules != self.total_rules:
            raise ValueError(
                "enabled_rules + disabled_rules must equal total_rules"
            )

        if self.failed_verifications > self.total_verifications:
            raise ValueError(
                "failed_verifications must not exceed "
                "total_verifications"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "total_rules": self.total_rules,
            "enabled_rules": self.enabled_rules,
            "disabled_rules": self.disabled_rules,
            "total_artifacts_verified": self.total_artifacts_verified,
            "total_verifications": self.total_verifications,
            "failed_verifications": self.failed_verifications,
        }


class DeploymentIntegrityVerifier:
    """
    Validates deployment artifacts before execution: every enabled
    rule (BUILT_IN_VERIFICATION_ALGORITHMS, or a custom evaluator) is
    checked against an artifact's content, in deterministic (name)
    order, and an artifact is verified only if every enabled rule
    passes. Disabled rules are skipped entirely.

    Deliberately limited to verification and validation — signing and
    key management are out of scope here; register_rule()'s evaluator
    parameter is the extension point a future signature or
    attestation mechanism would use instead.

    Every verify() call is appended to that artifact's history
    (history()) rather than only keeping the latest — "immutable
    verification reports" that are never discarded, only added to.

    Thread-safe: the rule registry and the per-artifact history are
    guarded by an internal lock.
    """

    def __init__(
        self,
        *,
        clock: "Callable[[], datetime] | None" = None,
        event_bus: "GovernanceEventBus | None" = None,
    ) -> None:
        self._lock = threading.Lock()

        self._rules: "dict[str, IntegrityRule]" = {}

        self._evaluators: "dict[str, IntegrityRuleEvaluator | None]" = {}

        self._history: "dict[str, list[IntegrityReport]]" = {}

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

    def register_rule(
        self,
        name: str,
        algorithm: str,
        *,
        enabled: bool = True,
        evaluator: "IntegrityRuleEvaluator | None" = None,
    ) -> IntegrityRule:
        """
        Register a new named verification rule.

        If evaluator is given, it is used directly (a custom rule —
        e.g. a future signature or attestation check), and algorithm
        is accepted as given without validation. Otherwise, algorithm
        must name one of BUILT_IN_VERIFICATION_ALGORITHMS, whose
        built-in check is used.

        Raises ValueError if name is already registered, or if
        evaluator is not given and algorithm is not a recognized
        built-in.
        """

        with self._lock:
            if name in self._rules:
                raise ValueError(
                    f"verification rule '{name}' is already registered"
                )

            if evaluator is None:
                evaluator = self._built_in_algorithms().get(algorithm)

                if evaluator is None:
                    raise ValueError(
                        f"unknown built-in verification algorithm "
                        f"'{algorithm}'"
                    )

            rule = IntegrityRule(
                name=name, algorithm=algorithm, enabled=enabled,
            )

            self._rules[name] = rule
            self._evaluators[name] = evaluator

        self._publish(
            "verification_rule_registered", name, rule.to_dict()
        )

        return rule

    def remove_rule(self, name: str) -> None:
        """
        Remove a registered verification rule.

        Raises KeyError if name is not registered.
        """

        with self._lock:
            if name not in self._rules:
                raise KeyError(
                    f"verification rule '{name}' is not registered"
                )

            del self._rules[name]
            self._evaluators.pop(name, None)

    def verify(
        self,
        artifact_id: str,
        content: "str | bytes",
        context: "dict[str, Any] | None" = None,
    ) -> IntegrityReport:
        """
        Verify artifact_id's content against every enabled rule
        (disabled rules skipped), in name order. Passes only if every
        enabled rule passes (an artifact with zero enabled rules
        trivially passes). checksum is always content's SHA-256 hex
        digest, regardless of which rules are registered.

        Raises ValueError if artifact_id is empty.
        """

        if not artifact_id:
            raise ValueError("artifact_id must not be empty")

        context = context or {}
        checksum = hashlib.sha256(_to_bytes(content)).hexdigest()
        verified = True

        for rule in self.list():
            if not rule.enabled:
                continue

            evaluator = self._evaluators.get(rule.name)

            if evaluator is not None and not evaluator(
                rule, content, context
            ):
                verified = False

        report = IntegrityReport(
            artifact_id=artifact_id, verified=verified, checksum=checksum,
        )

        with self._lock:
            self._history.setdefault(artifact_id, []).append(report)

        event_type = "integrity_verified" if verified else "integrity_failed"

        self._publish(event_type, artifact_id, report.to_dict())

        return report

    def verify_all(
        self,
        artifacts: "dict[str, dict[str, Any]] | None" = None,
    ) -> "dict[str, IntegrityReport]":
        """
        Verify every artifact_id in artifacts (a artifact_id -> {
        "content": ..., "context": {...}} mapping — "context" is
        optional), in artifact_id order, returning an artifact_id ->
        its IntegrityReport mapping.
        """

        artifacts = artifacts or {}

        return {
            artifact_id: self.verify(
                artifact_id,
                artifacts[artifact_id].get("content", ""),
                artifacts[artifact_id].get("context"),
            )
            for artifact_id in sorted(artifacts)
        }

    def history(self, artifact_id: str) -> "tuple[IntegrityReport, ...]":
        """
        Return every IntegrityReport ever recorded for artifact_id, in
        the order verify() produced them.

        Raises KeyError if artifact_id has never been verified.
        """

        with self._lock:
            reports = self._history.get(artifact_id)

            if reports is None:
                raise KeyError(
                    f"artifact '{artifact_id}' has not been verified"
                )

            return tuple(reports)

    def list(self) -> "tuple[IntegrityRule, ...]":
        """
        Return every registered verification rule, ordered
        deterministically by name.
        """

        with self._lock:
            rules = list(self._rules.values())

        return tuple(sorted(rules, key=lambda rule: rule.name))

    def summary(self) -> IntegrityVerificationSummary:
        """
        Return a point-in-time aggregate over the rule registry and
        every cached report across every artifact verified so far.
        """

        rules = self.list()
        enabled_rules = [rule for rule in rules if rule.enabled]

        with self._lock:
            all_reports = [
                report
                for reports in self._history.values()
                for report in reports
            ]
            total_artifacts_verified = len(self._history)

        return IntegrityVerificationSummary(
            total_rules=len(rules),
            enabled_rules=len(enabled_rules),
            disabled_rules=len(rules) - len(enabled_rules),
            total_artifacts_verified=total_artifacts_verified,
            total_verifications=len(all_reports),
            failed_verifications=sum(
                1 for report in all_reports if not report.verified
            ),
        )

    def clear(self) -> None:
        """
        Remove every registered rule and every cached verification
        history.
        """

        with self._lock:
            self._rules.clear()
            self._evaluators.clear()
            self._history.clear()

    def _built_in_algorithms(
        self,
    ) -> "dict[str, IntegrityRuleEvaluator]":
        return {
            "SHA-256": self._verify_sha256,
            "SHA-512": self._verify_sha512,
            "Artifact Size": self._verify_artifact_size,
            "Manifest Consistency": self._verify_manifest_consistency,
        }

    def _verify_sha256(
        self,
        rule: IntegrityRule,
        content: "str | bytes",
        context: "dict[str, Any]",
    ) -> bool:
        expected = context.get("expected_sha256")

        if expected is None:
            return True

        return hashlib.sha256(_to_bytes(content)).hexdigest() == expected

    def _verify_sha512(
        self,
        rule: IntegrityRule,
        content: "str | bytes",
        context: "dict[str, Any]",
    ) -> bool:
        expected = context.get("expected_sha512")

        if expected is None:
            return True

        return hashlib.sha512(_to_bytes(content)).hexdigest() == expected

    def _verify_artifact_size(
        self,
        rule: IntegrityRule,
        content: "str | bytes",
        context: "dict[str, Any]",
    ) -> bool:
        max_size_bytes = context.get("max_size_bytes")

        if max_size_bytes is None:
            return True

        return len(_to_bytes(content)) <= max_size_bytes

    def _verify_manifest_consistency(
        self,
        rule: IntegrityRule,
        content: "str | bytes",
        context: "dict[str, Any]",
    ) -> bool:
        manifest = context.get("manifest")

        if not manifest:
            return True

        files = context.get("files") or {}

        for filename, expected_hash in manifest.items():
            actual_content = files.get(filename)

            if actual_content is None:
                return False

            actual_hash = hashlib.sha256(
                _to_bytes(actual_content)
            ).hexdigest()

            if actual_hash != expected_hash:
                return False

        return True

    def _publish(
        self,
        event_type: str,
        source: str,
        payload: "dict[str, object] | None" = None,
    ) -> None:
        if self._event_bus is None:
            return

        self._event_bus.publish(
            event_type, source=source, payload=payload
        )


def build_default_governance_artifact_integrity_verifier() -> (
    DeploymentIntegrityVerifier
):
    """
    Build the process-wide deployment integrity verifier, wired to the
    process-wide governance event bus.
    """

    from .deployment_governance_event_bus import get_event_bus

    return DeploymentIntegrityVerifier(event_bus=get_event_bus())


# Shared for the lifetime of the process: rules registered through the
# API need to be enforced identically by every caller, and history()
# needs to reflect every verify() call regardless of which request
# made it, which a persistence runtime built fresh per request cannot
# provide on its own.
_artifact_integrity_verifier = (
    build_default_governance_artifact_integrity_verifier()
)


def get_artifact_integrity_verifier() -> DeploymentIntegrityVerifier:
    """
    Return the process-wide deployment integrity verifier.
    """

    return _artifact_integrity_verifier
