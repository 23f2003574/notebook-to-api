from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_artifact_integrity import (
        DeploymentIntegrityVerifier,
    )
    from .deployment_governance_event_bus import GovernanceEventBus
    from .deployment_governance_secret_vault import DeploymentSecretVault

# The severities a finding is classified into — "severity
# classification". Documented vocabulary, enforced by
# SecurityFinding.__post_init__ (unlike BUILT_IN_SCANNER_TYPES below,
# this one *is* a closed set: an unrecognized severity string would
# make critical_finding_detected's own "severity == CRITICAL" check
# meaningless).
SEVERITY_LEVELS: "tuple[str, ...]" = ("LOW", "MEDIUM", "HIGH", "CRITICAL")

# The statuses one scanner's run against one deployment can conclude
# with: PASSED (ran, nothing HIGH/CRITICAL found), FAILED (ran, found
# at least one HIGH/CRITICAL finding), or ERROR (the plugin itself
# raised — a scanner that cannot run is not the same thing as a
# scanner that ran and found nothing).
SCAN_STATUSES: "tuple[str, ...]" = ("PASSED", "FAILED", "ERROR")

# The built-in scanner types this framework ships with, selectable by
# name via register_scanner()'s scanner_type parameter — the same
# plug-in shape BUILT_IN_ROLLOUT_POLICIES established, so a real
# scanner (SBOM, a CVE database, SAST, ...) can be added later via
# register_scanner()'s plugin parameter without modifying this class.
BUILT_IN_SCANNER_TYPES: "tuple[str, ...]" = (
    "Secret Detection",
    "Configuration Validation",
    "Dependency Check",
    "Container Image Check",
)


class SecurityScannerPlugin(Protocol):
    """
    The common interface every scanner — built-in or a future real
    plugin (SBOM, a CVE database, SAST, ...) — implements: given a
    deployment and its context, return every SecurityFinding it turned
    up (empty if none).
    """

    def scan(
        self, deployment_id: str, context: "dict[str, Any]"
    ) -> "Iterable[SecurityFinding]":
        ...


class _CallableScannerPlugin:
    """
    Adapts a bare (deployment_id, context) -> findings callable to the
    SecurityScannerPlugin interface — how each built-in scanner (a
    bound method) is registered, so scan()'s plugin.scan(...) call
    works identically whether plugin is a real object implementing the
    interface directly (a custom plugin) or one of these callable
    adapters.
    """

    def __init__(
        self,
        func: "Callable[[str, dict[str, Any]], Iterable[SecurityFinding]]",
    ) -> None:
        self._func = func

    def scan(
        self, deployment_id: str, context: "dict[str, Any]"
    ) -> "Iterable[SecurityFinding]":
        return self._func(deployment_id, context)


@dataclass(frozen=True)
class SecurityFinding:
    """
    One immutable finding one scanner produced for one deployment.
    """

    severity: str

    category: str

    description: str

    def __post_init__(self) -> None:
        if self.severity not in SEVERITY_LEVELS:
            raise ValueError(
                f"severity must be one of {SEVERITY_LEVELS}"
            )

        if not self.category:
            raise ValueError("category must not be empty")

        if not self.description:
            raise ValueError("description must not be empty")

    def to_dict(self) -> dict[str, object]:
        return {
            "severity": self.severity,
            "category": self.category,
            "description": self.description,
        }


@dataclass(frozen=True)
class ScanResult:
    """
    The immutable outcome of one scanner's run against one deployment
    — its status and how many findings it produced (see
    SecurityFinding for the findings themselves).
    """

    scanner: str

    status: str

    findings: int

    def __post_init__(self) -> None:
        if not self.scanner:
            raise ValueError("scanner must not be empty")

        if self.status not in SCAN_STATUSES:
            raise ValueError(f"status must be one of {SCAN_STATUSES}")

        if self.findings < 0:
            raise ValueError("findings must not be negative")

    def to_dict(self) -> dict[str, object]:
        return {
            "scanner": self.scanner,
            "status": self.status,
            "findings": self.findings,
        }


@dataclass(frozen=True)
class SecurityScanSummary:
    """
    An immutable, point-in-time aggregate over every cached scan
    result across every deployment this scanner has scanned.
    """

    total_scanners: int

    total_deployments_scanned: int

    total_findings: int

    critical_findings: int

    def __post_init__(self) -> None:
        for field_name in (
            "total_scanners", "total_deployments_scanned",
            "total_findings", "critical_findings",
        ):
            if getattr(self, field_name) < 0:
                raise ValueError(f"{field_name} must not be negative")

        if self.critical_findings > self.total_findings:
            raise ValueError(
                "critical_findings must not exceed total_findings"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "total_scanners": self.total_scanners,
            "total_deployments_scanned": (
                self.total_deployments_scanned
            ),
            "total_findings": self.total_findings,
            "critical_findings": self.critical_findings,
        }


class DeploymentSecurityScanner:
    """
    A pluggable framework that validates deployment artifacts before
    release: every registered scanner (BUILT_IN_SCANNER_TYPES, or a
    custom SecurityScannerPlugin) runs against a deployment, in
    deterministic (name) order, each producing zero or more
    SecurityFinding and one summarizing ScanResult.

    A scanner plugin raising during its own run does not abort the
    rest of scan() — that scanner's ScanResult is reported as
    status="ERROR" (findings=0) and every other registered scanner
    still runs — one broken or misbehaving plugin should not prevent
    every other scanner from reporting.

    Individual real scanners (SBOM, CVE databases, SAST, ...) are out
    of scope here — this is the framework and execution pipeline they
    will eventually plug into.

    Thread-safe: the scanner registry and the per-deployment result/
    finding caches are guarded by an internal lock.
    """

    def __init__(
        self,
        *,
        clock: "Callable[[], datetime] | None" = None,
        event_bus: "GovernanceEventBus | None" = None,
        secret_vault: "DeploymentSecretVault | None" = None,
        integrity_verifier: "DeploymentIntegrityVerifier | None" = None,
    ) -> None:
        self._lock = threading.Lock()

        self._scanners: "dict[str, SecurityScannerPlugin]" = {}

        self._results: "dict[str, tuple[ScanResult, ...]]" = {}

        self._findings: "dict[str, tuple[SecurityFinding, ...]]" = {}

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

        self._secret_vault = secret_vault

        self._integrity_verifier = integrity_verifier

    def register_scanner(
        self,
        name: str,
        *,
        scanner_type: "str | None" = None,
        plugin: "SecurityScannerPlugin | None" = None,
    ) -> None:
        """
        Register a new named scanner.

        If plugin is given, it is used directly (a custom scanner).
        Otherwise, if scanner_type names one of BUILT_IN_SCANNER_TYPES,
        that built-in scanner is used.

        Raises ValueError if name is already registered, if neither
        plugin nor scanner_type is given, or if scanner_type is given
        but not a recognized built-in.
        """

        if not name:
            raise ValueError("name must not be empty")

        with self._lock:
            if name in self._scanners:
                raise ValueError(
                    f"scanner '{name}' is already registered"
                )

            if plugin is None and scanner_type is not None:
                plugin = self._built_in_scanners().get(scanner_type)

                if plugin is None:
                    raise ValueError(
                        f"unknown built-in scanner type "
                        f"'{scanner_type}'"
                    )

            if plugin is None:
                raise ValueError(
                    "either scanner_type or plugin must be given"
                )

            self._scanners[name] = plugin

    def unregister_scanner(self, name: str) -> None:
        """
        Remove a registered scanner.

        Raises KeyError if name is not registered.
        """

        with self._lock:
            if name not in self._scanners:
                raise KeyError(f"scanner '{name}' is not registered")

            del self._scanners[name]

    def scan(
        self, deployment_id: str, context: "dict[str, Any] | None" = None
    ) -> "tuple[ScanResult, ...]":
        """
        Run every registered scanner against deployment_id, in name
        order, returning one ScanResult per scanner.

        Raises ValueError if deployment_id is empty.
        """

        if not deployment_id:
            raise ValueError("deployment_id must not be empty")

        context = context or {}

        with self._lock:
            scanner_names = sorted(self._scanners)

        self._publish("security_scan_started", deployment_id, {})

        results = []
        all_findings: "list[SecurityFinding]" = []

        for name in scanner_names:
            with self._lock:
                plugin = self._scanners.get(name)

            if plugin is None:
                continue

            try:
                findings = tuple(plugin.scan(deployment_id, context))

            except Exception as exc:
                result = ScanResult(scanner=name, status="ERROR", findings=0)

                self._publish(
                    "security_scan_failed", deployment_id,
                    {"scanner": name, "error": str(exc)},
                )

            else:
                status = (
                    "FAILED"
                    if any(
                        finding.severity in ("HIGH", "CRITICAL")
                        for finding in findings
                    )
                    else "PASSED"
                )

                result = ScanResult(
                    scanner=name, status=status,
                    findings=len(findings),
                )

                all_findings.extend(findings)

                self._publish(
                    "security_scan_completed", deployment_id,
                    result.to_dict(),
                )

                for finding in findings:
                    if finding.severity == "CRITICAL":
                        self._publish(
                            "critical_finding_detected", deployment_id,
                            {"scanner": name, **finding.to_dict()},
                        )

            results.append(result)

        with self._lock:
            self._results[deployment_id] = tuple(results)
            self._findings[deployment_id] = tuple(all_findings)

        return tuple(results)

    def scan_all(
        self,
        contexts: "dict[str, dict[str, Any]] | None" = None,
    ) -> "dict[str, tuple[ScanResult, ...]]":
        """
        Scan every deployment_id in contexts (a deployment_id ->
        context mapping), in deployment_id order, returning a
        deployment_id -> its ScanResult tuple mapping.
        """

        contexts = contexts or {}

        return {
            deployment_id: self.scan(
                deployment_id, contexts[deployment_id]
            )
            for deployment_id in sorted(contexts)
        }

    def results(self, deployment_id: str) -> "tuple[ScanResult, ...]":
        """
        Return deployment_id's most recent scan() results.

        Raises KeyError if deployment_id has never been scanned.
        """

        with self._lock:
            results = self._results.get(deployment_id)

            if results is None:
                raise KeyError(
                    f"deployment '{deployment_id}' has not been "
                    "scanned"
                )

            return results

    def findings(
        self, deployment_id: str
    ) -> "tuple[SecurityFinding, ...]":
        """
        Return every SecurityFinding from deployment_id's most recent
        scan(), across every scanner.

        Raises KeyError if deployment_id has never been scanned.
        """

        with self._lock:
            findings = self._findings.get(deployment_id)

            if findings is None:
                raise KeyError(
                    f"deployment '{deployment_id}' has not been "
                    "scanned"
                )

            return findings

    def summary(self) -> SecurityScanSummary:
        """
        Return an aggregate over every cached scan result across
        every deployment this scanner has scanned.
        """

        with self._lock:
            total_scanners = len(self._scanners)
            all_findings = [
                finding
                for findings in self._findings.values()
                for finding in findings
            ]
            total_deployments_scanned = len(self._results)

        return SecurityScanSummary(
            total_scanners=total_scanners,
            total_deployments_scanned=total_deployments_scanned,
            total_findings=len(all_findings),
            critical_findings=sum(
                1
                for finding in all_findings
                if finding.severity == "CRITICAL"
            ),
        )

    def integrity_failed(self, artifact_id: str) -> bool:
        """
        Return whether artifact_id's most recent integrity
        verification failed, delegating to a wired
        DeploymentIntegrityVerifier's own history(). False if no
        integrity_verifier is wired, or artifact_id has never been
        verified — an artifact this scanner cannot get an opinion on
        is not the same thing as one confirmed to have failed.
        Introduced for DeploymentRiskEngine's "integrity_failures"
        default risk factor, which consults this rather than
        depending on the integrity verifier directly.
        """

        if self._integrity_verifier is None:
            return False

        try:
            history = self._integrity_verifier.history(artifact_id)

        except KeyError:
            return False

        if not history:
            return False

        return not history[-1].verified

    def has_critical_finding(self, deployment_id: str) -> bool:
        """
        Return whether any of deployment_id's most recent scan()
        findings is CRITICAL severity. False if deployment_id has
        never been scanned. Introduced for
        DeploymentIncidentResponseEngine's "critical_security_finding"
        default trigger, sparing it from fetching and filtering
        findings() itself.
        """

        try:
            findings = self.findings(deployment_id)

        except KeyError:
            return False

        return any(finding.severity == "CRITICAL" for finding in findings)

    def clear(self) -> None:
        """
        Remove every registered scanner and every cached scan
        result/finding.
        """

        with self._lock:
            self._scanners.clear()
            self._results.clear()
            self._findings.clear()

    def _built_in_scanners(
        self,
    ) -> "dict[str, SecurityScannerPlugin]":
        return {
            "Secret Detection": _CallableScannerPlugin(
                self._scan_secret_detection
            ),
            "Configuration Validation": _CallableScannerPlugin(
                self._scan_configuration_validation
            ),
            "Dependency Check": _CallableScannerPlugin(
                self._scan_dependency_check
            ),
            "Container Image Check": _CallableScannerPlugin(
                self._scan_container_image_check
            ),
        }

    def _scan_secret_detection(
        self, deployment_id: str, context: "dict[str, Any]"
    ) -> "tuple[SecurityFinding, ...]":
        files = context.get("files") or {}
        findings = []

        if self._secret_vault is not None:
            for name in self._secret_vault.names():
                try:
                    value = self._secret_vault.fetch(name)

                except KeyError:
                    continue

                if not value:
                    continue

                for filename, content in files.items():
                    if value in content:
                        findings.append(
                            SecurityFinding(
                                severity="CRITICAL",
                                category="secret_exposure",
                                description=(
                                    f"secret '{name}' found exposed "
                                    f"in '{filename}'"
                                ),
                            )
                        )

        else:
            patterns = (
                "password=", "secret=", "api_key=",
                "-----begin private key-----",
            )

            for filename, content in files.items():
                lowered = content.lower()

                if any(pattern in lowered for pattern in patterns):
                    findings.append(
                        SecurityFinding(
                            severity="HIGH",
                            category="secret_exposure",
                            description=(
                                "possible hardcoded secret pattern in "
                                f"'{filename}'"
                            ),
                        )
                    )

        return tuple(findings)

    def _scan_configuration_validation(
        self, deployment_id: str, context: "dict[str, Any]"
    ) -> "tuple[SecurityFinding, ...]":
        configuration = context.get("configuration") or {}
        findings = []

        if configuration.get("debug"):
            findings.append(
                SecurityFinding(
                    severity="HIGH", category="insecure_configuration",
                    description="debug mode is enabled",
                )
            )

        if configuration.get("tls_enabled") is False:
            findings.append(
                SecurityFinding(
                    severity="CRITICAL",
                    category="insecure_configuration",
                    description="TLS is disabled",
                )
            )

        return tuple(findings)

    def _scan_dependency_check(
        self, deployment_id: str, context: "dict[str, Any]"
    ) -> "tuple[SecurityFinding, ...]":
        dependencies = context.get("dependencies") or []
        findings = []

        for dependency in dependencies:
            if not dependency.get("known_vulnerable"):
                continue

            severity = dependency.get("severity", "HIGH")
            name = dependency.get("name", "unknown")

            findings.append(
                SecurityFinding(
                    severity=severity, category="vulnerable_dependency",
                    description=(
                        f"dependency '{name}' has a known vulnerability"
                    ),
                )
            )

        return tuple(findings)

    def _scan_container_image_check(
        self, deployment_id: str, context: "dict[str, Any]"
    ) -> "tuple[SecurityFinding, ...]":
        image = context.get("container_image") or {}
        unpatched_cves = image.get("unpatched_cves", 0)

        if unpatched_cves <= 0:
            return ()

        severity = "CRITICAL" if unpatched_cves >= 5 else "HIGH"

        return (
            SecurityFinding(
                severity=severity, category="container_vulnerability",
                description=(
                    f"container image has {unpatched_cves} unpatched "
                    "CVEs"
                ),
            ),
        )

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


def build_default_governance_security_scanner() -> (
    DeploymentSecurityScanner
):
    """
    Build the process-wide deployment security scanner, wired to the
    process-wide governance event bus, secret vault, and integrity
    verifier.
    """

    from .deployment_governance_artifact_integrity import (
        get_artifact_integrity_verifier,
    )
    from .deployment_governance_event_bus import get_event_bus
    from .deployment_governance_secret_vault import get_secret_vault

    return DeploymentSecurityScanner(
        event_bus=get_event_bus(), secret_vault=get_secret_vault(),
        integrity_verifier=get_artifact_integrity_verifier(),
    )


# Shared for the lifetime of the process: scanners registered through
# the API need to run identically for every caller, which a
# persistence runtime built fresh per request cannot provide on its
# own.
_security_scanner = build_default_governance_security_scanner()


def get_security_scanner() -> DeploymentSecurityScanner:
    """
    Return the process-wide deployment security scanner.
    """

    return _security_scanner
