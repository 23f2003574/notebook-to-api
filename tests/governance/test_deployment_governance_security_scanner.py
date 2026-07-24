from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_event_bus import (
    GovernanceEventBus,
)
from backend.observability.deployment_governance_security_scanner import (
    BUILT_IN_SCANNER_TYPES,
    SCAN_STATUSES,
    SEVERITY_LEVELS,
    DeploymentSecurityScanner,
    ScanResult,
    SecurityFinding,
    SecurityScanSummary,
    get_security_scanner,
)

BASE_TIME = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


def _scanner(**kwargs) -> DeploymentSecurityScanner:
    return DeploymentSecurityScanner(clock=_clock, **kwargs)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """
    The security scanner is a process-wide singleton; most tests below
    construct their own fresh scanner instead (see _scanner), and only
    the singleton and API tests touch the shared instance, matching
    test_deployment_governance_risk.py's own fixture.
    """

    def _reset():
        get_security_scanner().clear()

    _reset()
    yield
    _reset()


# --- Models --------------------------------------------------------------


class TestSecurityFinding:

    def test_rejects_invalid_severity(self):
        with pytest.raises(ValueError, match="severity must be one of"):
            SecurityFinding(
                severity="BOGUS", category="c", description="d"
            )

    def test_rejects_empty_category(self):
        with pytest.raises(
            ValueError, match="category must not be empty"
        ):
            SecurityFinding(severity="LOW", category="", description="d")

    def test_rejects_empty_description(self):
        with pytest.raises(
            ValueError, match="description must not be empty"
        ):
            SecurityFinding(severity="LOW", category="c", description="")

    def test_to_dict(self):
        finding = SecurityFinding(
            severity="HIGH", category="secret_exposure",
            description="found a secret",
        )

        assert finding.to_dict() == {
            "severity": "HIGH", "category": "secret_exposure",
            "description": "found a secret",
        }


class TestScanResult:

    def test_rejects_empty_scanner(self):
        with pytest.raises(ValueError, match="scanner must not be empty"):
            ScanResult(scanner="", status="PASSED", findings=0)

    def test_rejects_invalid_status(self):
        with pytest.raises(ValueError, match="status must be one of"):
            ScanResult(scanner="s", status="BOGUS", findings=0)

    def test_rejects_negative_findings(self):
        with pytest.raises(
            ValueError, match="findings must not be negative"
        ):
            ScanResult(scanner="s", status="PASSED", findings=-1)

    def test_to_dict(self):
        result = ScanResult(scanner="s", status="FAILED", findings=2)

        assert result.to_dict() == {
            "scanner": "s", "status": "FAILED", "findings": 2,
        }


class TestSecurityScanSummary:

    def test_rejects_critical_exceeding_total(self):
        with pytest.raises(
            ValueError, match="critical_findings must not exceed"
        ):
            SecurityScanSummary(
                total_scanners=1, total_deployments_scanned=1,
                total_findings=1, critical_findings=2,
            )

    def test_to_dict(self):
        summary = SecurityScanSummary(
            total_scanners=2, total_deployments_scanned=1,
            total_findings=3, critical_findings=1,
        )

        assert summary.to_dict() == {
            "total_scanners": 2, "total_deployments_scanned": 1,
            "total_findings": 3, "critical_findings": 1,
        }


class TestConstants:

    def test_severity_levels(self):
        assert SEVERITY_LEVELS == ("LOW", "MEDIUM", "HIGH", "CRITICAL")

    def test_scan_statuses(self):
        assert SCAN_STATUSES == ("PASSED", "FAILED", "ERROR")

    def test_built_in_scanner_types(self):
        assert set(BUILT_IN_SCANNER_TYPES) == {
            "Secret Detection", "Configuration Validation",
            "Dependency Check", "Container Image Check",
        }


# --- Scanner registration --------------------------------------------------


class TestScannerRegistration:

    def test_register_with_built_in_type(self):
        scanner = _scanner()

        scanner.register_scanner(
            "config", scanner_type="Configuration Validation"
        )

        results = scanner.scan("d1")

        assert results[0].scanner == "config"

    def test_register_with_custom_plugin(self):
        scanner = _scanner()

        class _Plugin:
            def scan(self, deployment_id, context):
                return ()

        scanner.register_scanner("custom", plugin=_Plugin())

        results = scanner.scan("d1")

        assert results[0].scanner == "custom"

    def test_register_duplicate_raises(self):
        scanner = _scanner()
        scanner.register_scanner(
            "config", scanner_type="Configuration Validation"
        )

        with pytest.raises(ValueError, match="already registered"):
            scanner.register_scanner(
                "config", scanner_type="Configuration Validation"
            )

    def test_register_unknown_type_raises(self):
        scanner = _scanner()

        with pytest.raises(ValueError, match="unknown built-in"):
            scanner.register_scanner("x", scanner_type="does-not-exist")

    def test_register_without_type_or_plugin_raises(self):
        scanner = _scanner()

        with pytest.raises(
            ValueError, match="either scanner_type or plugin"
        ):
            scanner.register_scanner("x")

    def test_unregister_scanner(self):
        scanner = _scanner()
        scanner.register_scanner(
            "config", scanner_type="Configuration Validation"
        )

        scanner.unregister_scanner("config")

        results = scanner.scan("d1")

        assert results == ()

    def test_unregister_unknown_raises(self):
        scanner = _scanner()

        with pytest.raises(KeyError):
            scanner.unregister_scanner("does-not-exist")


# --- Successful scan --------------------------------------------------


class TestSuccessfulScan:

    def test_scan_with_no_findings_passes(self):
        scanner = _scanner()
        scanner.register_scanner(
            "config", scanner_type="Configuration Validation"
        )

        results = scanner.scan("d1", {"configuration": {}})

        assert results[0].status == "PASSED"
        assert results[0].findings == 0

    def test_scan_runs_in_deterministic_name_order(self):
        scanner = _scanner()
        scanner.register_scanner(
            "zeta", scanner_type="Configuration Validation"
        )
        scanner.register_scanner(
            "alpha", scanner_type="Configuration Validation"
        )

        results = scanner.scan("d1")

        assert [r.scanner for r in results] == ["alpha", "zeta"]

    def test_rejects_empty_deployment_id(self):
        scanner = _scanner()

        with pytest.raises(
            ValueError, match="deployment_id must not be empty"
        ):
            scanner.scan("")

    def test_publishes_scan_started_and_completed(self):
        bus = GovernanceEventBus()
        started = []
        completed = []
        bus.subscribe("security_scan_started", started.append)
        bus.subscribe("security_scan_completed", completed.append)
        scanner = _scanner(event_bus=bus)
        scanner.register_scanner(
            "config", scanner_type="Configuration Validation"
        )

        scanner.scan("d1")

        assert len(started) == 1
        assert len(completed) == 1

    def test_scan_all(self):
        scanner = _scanner()
        scanner.register_scanner(
            "config", scanner_type="Configuration Validation"
        )

        results = scanner.scan_all(
            {"d2": {}, "d1": {"configuration": {"debug": True}}}
        )

        assert list(results.keys()) == ["d1", "d2"]
        assert results["d1"][0].status == "FAILED"
        assert results["d2"][0].status == "PASSED"

    def test_results_and_findings_cached(self):
        scanner = _scanner()
        scanner.register_scanner(
            "config", scanner_type="Configuration Validation"
        )

        scanner.scan("d1", {"configuration": {"debug": True}})

        assert scanner.results("d1")[0].status == "FAILED"
        assert len(scanner.findings("d1")) == 1

    def test_results_unscanned_raises(self):
        scanner = _scanner()

        with pytest.raises(KeyError):
            scanner.results("does-not-exist")

    def test_findings_unscanned_raises(self):
        scanner = _scanner()

        with pytest.raises(KeyError):
            scanner.findings("does-not-exist")


# --- Critical finding detection ---------------------------------------


class TestCriticalFindingDetection:

    def test_tls_disabled_is_critical(self):
        scanner = _scanner()
        scanner.register_scanner(
            "config", scanner_type="Configuration Validation"
        )

        results = scanner.scan(
            "d1", {"configuration": {"tls_enabled": False}}
        )

        assert results[0].status == "FAILED"
        findings = scanner.findings("d1")
        assert findings[0].severity == "CRITICAL"

    def test_publishes_critical_finding_detected(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("critical_finding_detected", events.append)
        scanner = _scanner(event_bus=bus)
        scanner.register_scanner(
            "config", scanner_type="Configuration Validation"
        )

        scanner.scan("d1", {"configuration": {"tls_enabled": False}})

        assert len(events) == 1

    def test_container_image_many_cves_is_critical(self):
        scanner = _scanner()
        scanner.register_scanner(
            "image", scanner_type="Container Image Check"
        )

        results = scanner.scan(
            "d1", {"container_image": {"unpatched_cves": 5}}
        )

        assert results[0].status == "FAILED"
        assert scanner.findings("d1")[0].severity == "CRITICAL"

    def test_container_image_few_cves_is_high_not_critical(self):
        scanner = _scanner()
        scanner.register_scanner(
            "image", scanner_type="Container Image Check"
        )

        scanner.scan("d1", {"container_image": {"unpatched_cves": 1}})

        assert scanner.findings("d1")[0].severity == "HIGH"

    def test_secret_detection_without_vault_uses_pattern_heuristic(self):
        scanner = _scanner()
        scanner.register_scanner(
            "secrets", scanner_type="Secret Detection"
        )

        results = scanner.scan(
            "d1", {"files": {"config.yaml": "password=hunter2"}}
        )

        assert results[0].status == "FAILED"
        assert scanner.findings("d1")[0].severity == "HIGH"

    def test_secret_detection_with_vault_finds_leaked_secret(self):
        from backend.observability.deployment_governance_secret_vault import (  # noqa: E501
            DeploymentSecretVault,
        )

        vault = DeploymentSecretVault(clock=_clock, environment={})
        vault.store("db-password", "hunter2-super-secret")

        scanner = _scanner(secret_vault=vault)
        scanner.register_scanner(
            "secrets", scanner_type="Secret Detection"
        )

        results = scanner.scan(
            "d1",
            {"files": {"config.yaml": "db_pass=hunter2-super-secret"}},
        )

        assert results[0].status == "FAILED"
        finding = scanner.findings("d1")[0]
        assert finding.severity == "CRITICAL"
        assert "hunter2-super-secret" not in finding.description

    def test_secret_detection_with_vault_no_leak_passes(self):
        from backend.observability.deployment_governance_secret_vault import (  # noqa: E501
            DeploymentSecretVault,
        )

        vault = DeploymentSecretVault(clock=_clock, environment={})
        vault.store("db-password", "hunter2-super-secret")

        scanner = _scanner(secret_vault=vault)
        scanner.register_scanner(
            "secrets", scanner_type="Secret Detection"
        )

        results = scanner.scan(
            "d1", {"files": {"config.yaml": "no secrets here"}}
        )

        assert results[0].status == "PASSED"

    def test_dependency_check_finding(self):
        scanner = _scanner()
        scanner.register_scanner(
            "deps", scanner_type="Dependency Check"
        )

        results = scanner.scan(
            "d1",
            {
                "dependencies": [
                    {"name": "left-pad", "known_vulnerable": True},
                ]
            },
        )

        assert results[0].status == "FAILED"
        assert scanner.findings("d1")[0].category == (
            "vulnerable_dependency"
        )


# --- Plugin execution ---------------------------------------------------


class TestPluginExecution:

    def test_custom_plugin_findings_are_collected(self):
        scanner = _scanner()

        class _Plugin:
            def scan(self, deployment_id, context):
                return (
                    SecurityFinding(
                        severity="MEDIUM", category="custom",
                        description="custom finding",
                    ),
                )

        scanner.register_scanner("custom", plugin=_Plugin())

        results = scanner.scan("d1")

        assert results[0].findings == 1
        assert results[0].status == "PASSED"

    def test_plugin_raising_becomes_error_status(self):
        scanner = _scanner()

        class _BrokenPlugin:
            def scan(self, deployment_id, context):
                raise RuntimeError("boom")

        scanner.register_scanner("broken", plugin=_BrokenPlugin())

        results = scanner.scan("d1")

        assert results[0].status == "ERROR"
        assert results[0].findings == 0

    def test_publishes_scan_failed_for_broken_plugin(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("security_scan_failed", events.append)
        scanner = _scanner(event_bus=bus)

        class _BrokenPlugin:
            def scan(self, deployment_id, context):
                raise RuntimeError("boom")

        scanner.register_scanner("broken", plugin=_BrokenPlugin())

        scanner.scan("d1")

        assert len(events) == 1

    def test_one_broken_plugin_does_not_stop_others(self):
        scanner = _scanner()

        class _BrokenPlugin:
            def scan(self, deployment_id, context):
                raise RuntimeError("boom")

        scanner.register_scanner("broken", plugin=_BrokenPlugin())
        scanner.register_scanner(
            "config", scanner_type="Configuration Validation"
        )

        results = scanner.scan("d1")

        by_name = {r.scanner: r for r in results}

        assert by_name["broken"].status == "ERROR"
        assert by_name["config"].status == "PASSED"


# --- Summary generation --------------------------------------------------


class TestSummaryGeneration:

    def test_summary_of_empty_scanner(self):
        scanner = _scanner()

        summary = scanner.summary()

        assert summary.total_scanners == 0
        assert summary.total_deployments_scanned == 0
        assert summary.total_findings == 0
        assert summary.critical_findings == 0

    def test_summary_after_scans(self):
        scanner = _scanner()
        scanner.register_scanner(
            "config", scanner_type="Configuration Validation"
        )

        scanner.scan("d1", {"configuration": {"tls_enabled": False}})
        scanner.scan("d2", {"configuration": {}})

        summary = scanner.summary()

        assert summary.total_scanners == 1
        assert summary.total_deployments_scanned == 2
        assert summary.total_findings == 1
        assert summary.critical_findings == 1


# --- Risk engine integration (this commit's Update file) -------------------


class TestRiskEngineIntegration:

    def test_security_findings_factor_uses_scanner(self):
        from backend.observability.deployment_governance_risk import (
            DeploymentRiskEngine,
        )

        scanner = _scanner()
        scanner.register_scanner(
            "config", scanner_type="Configuration Validation"
        )
        scanner.scan("d1", {"configuration": {"tls_enabled": False}})

        risk_engine = DeploymentRiskEngine(
            clock=_clock, security_scanner=scanner,
        )
        risk_engine.register_rule(
            "findings", 50.0, factor="security_findings"
        )

        assessment = risk_engine.assess("d1")

        assert assessment.score == 50.0

    def test_security_findings_factor_without_scan_yet(self):
        from backend.observability.deployment_governance_risk import (
            DeploymentRiskEngine,
        )

        scanner = _scanner()
        risk_engine = DeploymentRiskEngine(
            clock=_clock, security_scanner=scanner,
        )
        risk_engine.register_rule(
            "findings", 50.0, factor="security_findings"
        )

        assessment = risk_engine.assess("never-scanned")

        assert assessment.score == 0.0


# --- Clear -------------------------------------------------------------


class TestClear:

    def test_clear_removes_scanners_and_cache(self):
        scanner = _scanner()
        scanner.register_scanner(
            "config", scanner_type="Configuration Validation"
        )
        scanner.scan("d1")

        scanner.clear()

        assert scanner.scan("d2") == ()

        with pytest.raises(KeyError):
            scanner.results("d1")


# --- Singleton ---------------------------------------------------------


class TestSingleton:

    def test_get_security_scanner_returns_same_instance(self):
        assert get_security_scanner() is get_security_scanner()


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceSecurityScanApi:

    def test_post_scan(self, client):
        get_security_scanner().register_scanner(
            "api-config", scanner_type="Configuration Validation"
        )

        response = client.post(
            "/governance/security/scan",
            params={
                "deployment_id": "api-d-1",
                "context": '{"configuration": {"debug": true}}',
            },
        )

        assert response.status_code == 200
        assert response.json()[0]["status"] == "FAILED"

    def test_get_results(self, client):
        client.post(
            "/governance/security/scan",
            params={"deployment_id": "api-d-2"},
        )

        response = client.get("/governance/security/scan/api-d-2")

        assert response.status_code == 200

    def test_get_unknown_returns_404(self, client):
        response = client.get(
            "/governance/security/scan/does-not-exist"
        )

        assert response.status_code == 404

    def test_get_summary(self, client):
        client.post(
            "/governance/security/scan",
            params={"deployment_id": "api-d-3"},
        )

        response = client.get("/governance/security/scan/summary")

        assert response.status_code == 200
        assert response.json()["total_deployments_scanned"] >= 1
