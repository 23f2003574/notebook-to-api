from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_audit import (
    GovernanceAuditService,
)
from backend.observability.deployment_governance_audit_trail import (
    RECORDED_AUDIT_ACTION_CATEGORIES,
    AuditEvent,
    AuditQuery,
    DeploymentAuditService,
    get_audit_trail_service,
)

BASE_TIME = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


def _service(**kwargs) -> DeploymentAuditService:
    return DeploymentAuditService(
        audit_service=GovernanceAuditService(clock=_clock), **kwargs
    )


@pytest.fixture(autouse=True)
def _reset_singleton():
    """
    The audit trail service wraps the process-wide GovernanceAuditService
    singleton; most tests below construct their own isolated service
    instead (see _service) to avoid depending on that singleton's
    ever-growing sequence counter (purge() deliberately does not reset
    it — see GovernanceAuditService.purge's own docstring). Only the
    singleton and API tests touch the shared instance.
    """

    def _reset():
        from backend.observability.deployment_governance_audit import (
            get_audit_service,
        )

        get_audit_service().purge()

    _reset()
    yield
    _reset()


# --- Models --------------------------------------------------------------


class TestAuditEvent:

    def test_rejects_empty_event_id(self):
        with pytest.raises(ValueError, match="event_id must not be empty"):
            AuditEvent(
                event_id="", actor="alice", action="deploy",
                resource="d1", timestamp=BASE_TIME,
            )

    def test_rejects_empty_actor(self):
        with pytest.raises(ValueError, match="actor must not be empty"):
            AuditEvent(
                event_id="1", actor="", action="deploy",
                resource="d1", timestamp=BASE_TIME,
            )

    def test_rejects_naive_timestamp(self):
        with pytest.raises(
            ValueError, match="timestamp must be timezone-aware"
        ):
            AuditEvent(
                event_id="1", actor="alice", action="deploy",
                resource="d1", timestamp=datetime(2026, 7, 24, 12, 0, 0),
            )

    def test_to_dict(self):
        event = AuditEvent(
            event_id="1", actor="alice", action="deploy", resource="d1",
            timestamp=BASE_TIME,
        )

        assert event.to_dict() == {
            "event_id": "1",
            "actor": "alice",
            "action": "deploy",
            "resource": "d1",
            "timestamp": BASE_TIME.isoformat(),
        }


class TestAuditQuery:

    def test_defaults_to_unfiltered(self):
        query = AuditQuery()

        assert query.actor is None
        assert query.action is None
        assert query.resource is None

    def test_to_dict(self):
        query = AuditQuery(actor="alice", action="deploy", resource="d1")

        assert query.to_dict() == {
            "actor": "alice", "action": "deploy", "resource": "d1",
        }


class TestRecordedAuditActionCategories:

    def test_contains_expected_categories(self):
        assert set(RECORDED_AUDIT_ACTION_CATEGORIES) == {
            "Authentication", "Authorization", "Approval",
            "Deployment", "Rollback", "Policy", "Configuration",
        }


# --- Record event ----------------------------------------------------------


class TestRecordEvent:

    def test_record_returns_audit_event(self):
        service = _service()

        event = service.record(
            actor="alice", action="deploy", resource="d1"
        )

        assert event.actor == "alice"
        assert event.action == "deploy"
        assert event.resource == "d1"
        assert event.timestamp == BASE_TIME

    def test_record_assigns_increasing_event_ids(self):
        service = _service()

        first = service.record(actor="alice", action="deploy", resource="d1")
        second = service.record(actor="bob", action="deploy", resource="d2")

        assert int(second.event_id) > int(first.event_id)


# --- Retrieve event ----------------------------------------------------


class TestRetrieveEvent:

    def test_get_returns_recorded_event(self):
        service = _service()
        recorded = service.record(
            actor="alice", action="deploy", resource="d1"
        )

        fetched = service.get(recorded.event_id)

        assert fetched == recorded

    def test_get_unknown_raises(self):
        service = _service()

        with pytest.raises(KeyError):
            service.get("does-not-exist")

    def test_get_non_numeric_event_id_raises_key_error(self):
        service = _service()

        with pytest.raises(KeyError):
            service.get("not-a-number")


# --- Filter by actor / action --------------------------------------------


class TestFiltering:

    def test_filter_by_actor(self):
        service = _service()
        service.record(actor="alice", action="deploy", resource="d1")
        service.record(actor="bob", action="deploy", resource="d2")

        results = service.search(AuditQuery(actor="alice"))

        assert [e.actor for e in results] == ["alice"]

    def test_filter_by_action(self):
        service = _service()
        service.record(actor="alice", action="deploy", resource="d1")
        service.record(actor="alice", action="rollback", resource="d1")

        results = service.search(AuditQuery(action="rollback"))

        assert [e.action for e in results] == ["rollback"]

    def test_filter_by_resource(self):
        service = _service()
        service.record(actor="alice", action="deploy", resource="d1")
        service.record(actor="alice", action="deploy", resource="d2")

        results = service.search(AuditQuery(resource="d2"))

        assert [e.resource for e in results] == ["d2"]

    def test_unfiltered_search_returns_everything(self):
        service = _service()
        service.record(actor="alice", action="deploy", resource="d1")
        service.record(actor="bob", action="rollback", resource="d2")

        results = service.search(AuditQuery())

        assert len(results) == 2

    def test_list_returns_everything_ordered_by_timestamp(self):
        clock_box = {"now": BASE_TIME}
        service = DeploymentAuditService(
            audit_service=GovernanceAuditService(
                clock=lambda: clock_box["now"]
            )
        )
        service.record(actor="alice", action="deploy", resource="d1")
        clock_box["now"] = BASE_TIME + timedelta(seconds=1)
        service.record(actor="bob", action="rollback", resource="d2")

        results = service.list()

        assert [e.actor for e in results] == ["alice", "bob"]

    def test_search_with_no_matches_returns_empty(self):
        service = _service()
        service.record(actor="alice", action="deploy", resource="d1")

        results = service.search(AuditQuery(actor="does-not-exist"))

        assert results == ()


# --- Export audit log ----------------------------------------------------


class TestExport:

    def test_export_returns_every_event_as_dict(self):
        service = _service()
        service.record(actor="alice", action="deploy", resource="d1")
        service.record(actor="bob", action="rollback", resource="d2")

        exported = service.export()

        assert len(exported) == 2
        assert all(isinstance(item, dict) for item in exported)
        assert {item["actor"] for item in exported} == {"alice", "bob"}

    def test_export_of_empty_log_is_empty(self):
        service = _service()

        assert service.export() == ()


# --- RBAC / approval integration (this commit's Update files) --------------


class TestRbacAuditIntegration:

    def test_register_role_is_recorded(self):
        from backend.observability.deployment_governance_rbac import (
            DeploymentRBACEngine,
        )

        underlying = GovernanceAuditService(clock=_clock)
        rbac = DeploymentRBACEngine(clock=_clock, audit_service=underlying)
        service = DeploymentAuditService(audit_service=underlying)

        rbac.register_role("Custom", ["deployment.read"])

        results = service.search(AuditQuery(action="role_registered"))

        assert len(results) == 1
        assert results[0].resource == "Custom"

    def test_assign_role_is_recorded(self):
        from backend.observability.deployment_governance_rbac import (
            DeploymentRBACEngine,
        )

        underlying = GovernanceAuditService(clock=_clock)
        rbac = DeploymentRBACEngine(clock=_clock, audit_service=underlying)
        service = DeploymentAuditService(audit_service=underlying)

        rbac.assign_role("p1", "Developer")

        results = service.search(AuditQuery(action="role_assigned"))

        assert len(results) == 1
        assert results[0].resource == "p1"


class TestApprovalAuditIntegration:

    def test_create_request_is_recorded(self):
        from backend.observability.deployment_governance_approval import (
            DeploymentApprovalEngine,
        )

        underlying = GovernanceAuditService(clock=_clock)
        approval = DeploymentApprovalEngine(
            clock=_clock, audit_service=underlying
        )
        service = DeploymentAuditService(audit_service=underlying)

        approval.create_request("d1", "deploy", "alice")

        results = service.search(AuditQuery(action="approval_requested"))

        assert len(results) == 1
        assert results[0].actor == "alice"

    def test_approve_is_recorded(self):
        from backend.observability.deployment_governance_approval import (
            DeploymentApprovalEngine,
        )

        underlying = GovernanceAuditService(clock=_clock)
        approval = DeploymentApprovalEngine(
            clock=_clock, audit_service=underlying
        )
        service = DeploymentAuditService(audit_service=underlying)

        request = approval.create_request("d1", "deploy", "alice")
        approval.approve(request.request_id, "carol")

        results = service.search(AuditQuery(action="approval_granted"))

        assert len(results) == 1
        assert results[0].actor == "carol"


# --- Singleton ---------------------------------------------------------


class TestSingleton:

    def test_get_audit_trail_service_returns_same_instance(self):
        assert get_audit_trail_service() is get_audit_trail_service()

    def test_singleton_wraps_the_shared_governance_audit_service(self):
        from backend.observability.deployment_governance_audit import (
            get_audit_service,
        )

        assert (
            get_audit_trail_service()._audit_service
            is get_audit_service()
        )


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceSecurityAuditApi:

    def test_post_records_event(self, client):
        response = client.post(
            "/governance/security/audit",
            params={
                "actor": "api-alice", "action": "deploy",
                "resource": "api-d-1",
            },
        )

        assert response.status_code == 200
        assert response.json()["actor"] == "api-alice"

    def test_get_returns_recorded_event(self, client):
        create_response = client.post(
            "/governance/security/audit",
            params={
                "actor": "api-alice", "action": "deploy",
                "resource": "api-d-2",
            },
        )

        event_id = create_response.json()["event_id"]

        response = client.get(f"/governance/security/audit/{event_id}")

        assert response.status_code == 200
        assert response.json()["event_id"] == event_id

    def test_get_unknown_returns_404(self, client):
        response = client.get(
            "/governance/security/audit/does-not-exist"
        )

        assert response.status_code == 404

    def test_get_list_includes_recorded_event(self, client):
        create_response = client.post(
            "/governance/security/audit",
            params={
                "actor": "api-alice", "action": "deploy",
                "resource": "api-d-3",
            },
        )

        event_id = create_response.json()["event_id"]

        response = client.get("/governance/security/audit")

        assert response.status_code == 200
        assert any(
            e["event_id"] == event_id for e in response.json()
        )

    def test_post_search_filters_by_actor(self, client):
        client.post(
            "/governance/security/audit",
            params={
                "actor": "api-filter-actor", "action": "deploy",
                "resource": "api-d-4",
            },
        )

        response = client.post(
            "/governance/security/audit/search",
            params={"actor": "api-filter-actor"},
        )

        assert response.status_code == 200
        assert all(
            e["actor"] == "api-filter-actor" for e in response.json()
        )
        assert len(response.json()) >= 1
