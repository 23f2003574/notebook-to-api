from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_artifact_integrity import (
    BUILT_IN_VERIFICATION_ALGORITHMS,
    DeploymentIntegrityVerifier,
    IntegrityReport,
    IntegrityRule,
    IntegrityVerificationSummary,
    get_artifact_integrity_verifier,
)
from backend.observability.deployment_governance_event_bus import (
    GovernanceEventBus,
)

BASE_TIME = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


def _verifier(**kwargs) -> DeploymentIntegrityVerifier:
    return DeploymentIntegrityVerifier(clock=_clock, **kwargs)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """
    The integrity verifier is a process-wide singleton; most tests
    below construct their own fresh verifier instead (see _verifier),
    and only the singleton and API tests touch the shared instance,
    matching test_deployment_governance_risk.py's own fixture.
    """

    def _reset():
        get_artifact_integrity_verifier().clear()

    _reset()
    yield
    _reset()


# --- Models --------------------------------------------------------------


class TestIntegrityReport:

    def test_rejects_empty_artifact_id(self):
        with pytest.raises(
            ValueError, match="artifact_id must not be empty"
        ):
            IntegrityReport(artifact_id="", verified=True, checksum="abc")

    def test_rejects_empty_checksum(self):
        with pytest.raises(
            ValueError, match="checksum must not be empty"
        ):
            IntegrityReport(artifact_id="a1", verified=True, checksum="")

    def test_to_dict(self):
        report = IntegrityReport(
            artifact_id="a1", verified=True, checksum="abc123"
        )

        assert report.to_dict() == {
            "artifact_id": "a1", "verified": True, "checksum": "abc123",
        }


class TestIntegrityRule:

    def test_rejects_empty_name(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            IntegrityRule(name="", algorithm="SHA-256", enabled=True)

    def test_rejects_empty_algorithm(self):
        with pytest.raises(
            ValueError, match="algorithm must not be empty"
        ):
            IntegrityRule(name="r", algorithm="", enabled=True)

    def test_to_dict(self):
        rule = IntegrityRule(name="r", algorithm="SHA-256", enabled=True)

        assert rule.to_dict() == {
            "name": "r", "algorithm": "SHA-256", "enabled": True,
        }


class TestIntegrityVerificationSummary:

    def test_rejects_mismatched_rule_counts(self):
        with pytest.raises(
            ValueError, match="enabled_rules \\+ disabled_rules"
        ):
            IntegrityVerificationSummary(
                total_rules=2, enabled_rules=2, disabled_rules=1,
                total_artifacts_verified=0, total_verifications=0,
                failed_verifications=0,
            )

    def test_rejects_failed_exceeding_total(self):
        with pytest.raises(
            ValueError, match="failed_verifications must not exceed"
        ):
            IntegrityVerificationSummary(
                total_rules=0, enabled_rules=0, disabled_rules=0,
                total_artifacts_verified=1, total_verifications=1,
                failed_verifications=2,
            )

    def test_to_dict(self):
        summary = IntegrityVerificationSummary(
            total_rules=1, enabled_rules=1, disabled_rules=0,
            total_artifacts_verified=1, total_verifications=2,
            failed_verifications=1,
        )

        assert summary.to_dict() == {
            "total_rules": 1, "enabled_rules": 1, "disabled_rules": 0,
            "total_artifacts_verified": 1, "total_verifications": 2,
            "failed_verifications": 1,
        }


class TestConstants:

    def test_built_in_algorithms(self):
        assert set(BUILT_IN_VERIFICATION_ALGORITHMS) == {
            "SHA-256", "SHA-512", "Artifact Size",
            "Manifest Consistency",
        }


# --- Checksum verification ---------------------------------------------


class TestChecksumVerification:

    def test_checksum_is_always_sha256_of_content(self):
        verifier = _verifier()

        report = verifier.verify("a1", "hello world")

        assert report.checksum == hashlib.sha256(
            b"hello world"
        ).hexdigest()

    def test_no_rules_trivially_verifies(self):
        verifier = _verifier()

        report = verifier.verify("a1", "hello world")

        assert report.verified is True

    def test_sha256_rule_matches_expected(self):
        verifier = _verifier()
        verifier.register_rule("checksum", "SHA-256")
        content = "hello world"
        expected = hashlib.sha256(content.encode()).hexdigest()

        report = verifier.verify(
            "a1", content, {"expected_sha256": expected}
        )

        assert report.verified is True

    def test_sha512_rule_matches_expected(self):
        verifier = _verifier()
        verifier.register_rule("checksum512", "SHA-512")
        content = "hello world"
        expected = hashlib.sha512(content.encode()).hexdigest()

        report = verifier.verify(
            "a1", content, {"expected_sha512": expected}
        )

        assert report.verified is True

    def test_rejects_empty_artifact_id(self):
        verifier = _verifier()

        with pytest.raises(
            ValueError, match="artifact_id must not be empty"
        ):
            verifier.verify("", "content")

    def test_publishes_integrity_verified(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("integrity_verified", events.append)
        verifier = _verifier(event_bus=bus)

        verifier.verify("a1", "content")

        assert len(events) == 1


# --- Failed verification -------------------------------------------------


class TestFailedVerification:

    def test_sha256_mismatch_fails(self):
        verifier = _verifier()
        verifier.register_rule("checksum", "SHA-256")

        report = verifier.verify(
            "a1", "hello world", {"expected_sha256": "wrong"}
        )

        assert report.verified is False

    def test_artifact_size_exceeded_fails(self):
        verifier = _verifier()
        verifier.register_rule("size", "Artifact Size")

        report = verifier.verify(
            "a1", "0123456789", {"max_size_bytes": 5}
        )

        assert report.verified is False

    def test_artifact_size_within_limit_passes(self):
        verifier = _verifier()
        verifier.register_rule("size", "Artifact Size")

        report = verifier.verify(
            "a1", "01234", {"max_size_bytes": 10}
        )

        assert report.verified is True

    def test_manifest_consistency_mismatch_fails(self):
        verifier = _verifier()
        verifier.register_rule("manifest", "Manifest Consistency")

        report = verifier.verify(
            "a1", "content",
            {
                "manifest": {"file.txt": "expected-hash"},
                "files": {"file.txt": "actual content"},
            },
        )

        assert report.verified is False

    def test_manifest_consistency_matching_passes(self):
        verifier = _verifier()
        verifier.register_rule("manifest", "Manifest Consistency")
        file_content = "actual content"
        expected_hash = hashlib.sha256(file_content.encode()).hexdigest()

        report = verifier.verify(
            "a1", "content",
            {
                "manifest": {"file.txt": expected_hash},
                "files": {"file.txt": file_content},
            },
        )

        assert report.verified is True

    def test_manifest_consistency_missing_file_fails(self):
        verifier = _verifier()
        verifier.register_rule("manifest", "Manifest Consistency")

        report = verifier.verify(
            "a1", "content",
            {
                "manifest": {"missing.txt": "some-hash"},
                "files": {},
            },
        )

        assert report.verified is False

    def test_one_failed_rule_fails_overall_verification(self):
        verifier = _verifier()
        verifier.register_rule("checksum", "SHA-256")
        verifier.register_rule("size", "Artifact Size")

        report = verifier.verify(
            "a1", "hello world",
            {"expected_sha256": "wrong", "max_size_bytes": 1000},
        )

        assert report.verified is False

    def test_publishes_integrity_failed(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("integrity_failed", events.append)
        verifier = _verifier(event_bus=bus)
        verifier.register_rule("checksum", "SHA-256")

        verifier.verify("a1", "content", {"expected_sha256": "wrong"})

        assert len(events) == 1


# --- Rule registration ---------------------------------------------------


class TestRuleRegistration:

    def test_register_built_in(self):
        verifier = _verifier()

        rule = verifier.register_rule("checksum", "SHA-256")

        assert rule.name == "checksum"
        assert rule.algorithm == "SHA-256"
        assert rule.enabled is True
        assert rule in verifier.list()

    def test_register_disabled(self):
        verifier = _verifier()

        rule = verifier.register_rule(
            "checksum", "SHA-256", enabled=False
        )

        assert rule.enabled is False

    def test_register_unknown_algorithm_without_evaluator_raises(self):
        verifier = _verifier()

        with pytest.raises(ValueError, match="unknown built-in"):
            verifier.register_rule("r1", "does-not-exist")

    def test_register_with_custom_evaluator(self):
        verifier = _verifier()

        def _always_fails(rule, content, context):
            return False

        verifier.register_rule(
            "custom", "Custom Signature", evaluator=_always_fails
        )

        report = verifier.verify("a1", "content")

        assert report.verified is False

    def test_duplicate_name_raises(self):
        verifier = _verifier()
        verifier.register_rule("checksum", "SHA-256")

        with pytest.raises(ValueError, match="already registered"):
            verifier.register_rule("checksum", "SHA-512")

    def test_publishes_verification_rule_registered(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("verification_rule_registered", events.append)
        verifier = _verifier(event_bus=bus)

        verifier.register_rule("checksum", "SHA-256")

        assert len(events) == 1

    def test_remove_rule(self):
        verifier = _verifier()
        verifier.register_rule("checksum", "SHA-256")

        verifier.remove_rule("checksum")

        assert verifier.list() == ()

    def test_remove_unknown_raises(self):
        verifier = _verifier()

        with pytest.raises(KeyError):
            verifier.remove_rule("does-not-exist")

    def test_disabled_rule_is_ignored(self):
        verifier = _verifier()
        verifier.register_rule("checksum", "SHA-256", enabled=False)

        report = verifier.verify(
            "a1", "content", {"expected_sha256": "wrong"}
        )

        assert report.verified is True

    def test_list_ordered_by_name(self):
        verifier = _verifier()
        verifier.register_rule("zeta", "SHA-256")
        verifier.register_rule("alpha", "SHA-256")

        names = [r.name for r in verifier.list()]

        assert names == ["alpha", "zeta"]


# --- History retrieval ---------------------------------------------------


class TestHistoryRetrieval:

    def test_history_accumulates_reports(self):
        verifier = _verifier()

        verifier.verify("a1", "v1")
        verifier.verify("a1", "v2")

        history = verifier.history("a1")

        assert len(history) == 2

    def test_history_unknown_artifact_raises(self):
        verifier = _verifier()

        with pytest.raises(KeyError):
            verifier.history("does-not-exist")

    def test_verify_all(self):
        verifier = _verifier()

        results = verifier.verify_all(
            {
                "a2": {"content": "v2"},
                "a1": {"content": "v1", "context": {}},
            }
        )

        assert list(results.keys()) == ["a1", "a2"]
        assert results["a1"].artifact_id == "a1"


# --- Summary generation --------------------------------------------------


class TestSummaryGeneration:

    def test_summary_of_empty_verifier(self):
        verifier = _verifier()

        summary = verifier.summary()

        assert summary.total_rules == 0
        assert summary.total_artifacts_verified == 0
        assert summary.total_verifications == 0
        assert summary.failed_verifications == 0

    def test_summary_after_verifications(self):
        verifier = _verifier()
        verifier.register_rule("checksum", "SHA-256")

        verifier.verify("a1", "content", {"expected_sha256": "wrong"})
        verifier.verify("a2", "content")

        summary = verifier.summary()

        assert summary.total_rules == 1
        assert summary.enabled_rules == 1
        assert summary.total_artifacts_verified == 2
        assert summary.total_verifications == 2
        assert summary.failed_verifications == 1


# --- Security scanner / risk engine integration (this commit's Update) -----


class TestSecurityScannerIntegration:

    def test_integrity_failed_true_after_failed_verification(self):
        from backend.observability.deployment_governance_security_scanner import (  # noqa: E501
            DeploymentSecurityScanner,
        )

        verifier = _verifier()
        verifier.register_rule("checksum", "SHA-256")
        verifier.verify("a1", "content", {"expected_sha256": "wrong"})

        scanner = DeploymentSecurityScanner(
            clock=_clock, integrity_verifier=verifier,
        )

        assert scanner.integrity_failed("a1") is True

    def test_integrity_failed_false_when_never_verified(self):
        from backend.observability.deployment_governance_security_scanner import (  # noqa: E501
            DeploymentSecurityScanner,
        )

        verifier = _verifier()
        scanner = DeploymentSecurityScanner(
            clock=_clock, integrity_verifier=verifier,
        )

        assert scanner.integrity_failed("never-verified") is False

    def test_integrity_failed_false_without_verifier(self):
        from backend.observability.deployment_governance_security_scanner import (  # noqa: E501
            DeploymentSecurityScanner,
        )

        scanner = DeploymentSecurityScanner(clock=_clock)

        assert scanner.integrity_failed("a1") is False

    def test_risk_engine_integrity_failures_factor(self):
        from backend.observability.deployment_governance_risk import (
            DeploymentRiskEngine,
        )
        from backend.observability.deployment_governance_security_scanner import (  # noqa: E501
            DeploymentSecurityScanner,
        )

        verifier = _verifier()
        verifier.register_rule("checksum", "SHA-256")
        verifier.verify("a1", "content", {"expected_sha256": "wrong"})

        scanner = DeploymentSecurityScanner(
            clock=_clock, integrity_verifier=verifier,
        )

        risk_engine = DeploymentRiskEngine(
            clock=_clock, security_scanner=scanner,
        )
        risk_engine.register_rule(
            "integrity", 40.0, factor="integrity_failures"
        )

        assessment = risk_engine.assess("a1")

        assert assessment.score == 40.0


# --- Clear -------------------------------------------------------------


class TestClear:

    def test_clear_removes_rules_and_history(self):
        verifier = _verifier()
        verifier.register_rule("checksum", "SHA-256")
        verifier.verify("a1", "content")

        verifier.clear()

        assert verifier.list() == ()

        with pytest.raises(KeyError):
            verifier.history("a1")


# --- Singleton ---------------------------------------------------------


class TestSingleton:

    def test_get_artifact_integrity_verifier_returns_same_instance(self):
        assert (
            get_artifact_integrity_verifier()
            is get_artifact_integrity_verifier()
        )


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceIntegrityApi:

    def test_post_verify(self, client):
        response = client.post(
            "/governance/security/integrity/verify",
            params={"artifact_id": "api-a-1", "content": "hello"},
        )

        assert response.status_code == 200
        assert response.json()["artifact_id"] == "api-a-1"
        assert response.json()["verified"] is True

    def test_get_history(self, client):
        client.post(
            "/governance/security/integrity/verify",
            params={"artifact_id": "api-a-2", "content": "hello"},
        )

        response = client.get(
            "/governance/security/integrity/api-a-2"
        )

        assert response.status_code == 200
        assert len(response.json()) == 1

    def test_get_unknown_returns_404(self, client):
        response = client.get(
            "/governance/security/integrity/does-not-exist"
        )

        assert response.status_code == 404

    def test_get_summary(self, client):
        client.post(
            "/governance/security/integrity/verify",
            params={"artifact_id": "api-a-3", "content": "hello"},
        )

        response = client.get(
            "/governance/security/integrity/summary"
        )

        assert response.status_code == 200
        assert response.json()["total_verifications"] >= 1
