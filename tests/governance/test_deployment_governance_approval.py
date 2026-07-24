from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_approval import (
    APPROVAL_STATUSES,
    ApprovalDecision,
    ApprovalRequest,
    DeploymentApprovalEngine,
    get_approval_engine,
)
from backend.observability.deployment_governance_event_bus import (
    GovernanceEventBus,
)
from backend.observability.deployment_governance_rbac import (
    DeploymentRBACEngine,
)

BASE_TIME = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


def _engine(**kwargs) -> DeploymentApprovalEngine:
    return DeploymentApprovalEngine(clock=_clock, **kwargs)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """
    The approval engine is a process-wide singleton wired to the
    process-wide RBAC engine; most tests below construct their own
    fresh engine instead (see _engine), and only the singleton and API
    tests touch the shared instance, matching
    test_deployment_governance_rbac.py's own fixture.
    """

    def _reset():
        get_approval_engine().clear()
        get_approval_engine()._rbac_engine.clear()

    _reset()
    yield
    _reset()


# --- Models --------------------------------------------------------------


class TestApprovalRequest:

    def test_rejects_empty_request_id(self):
        with pytest.raises(
            ValueError, match="request_id must not be empty"
        ):
            ApprovalRequest(
                request_id="", deployment_id="d1", operation="deploy",
                requester="alice", status="PENDING",
            )

    def test_rejects_invalid_status(self):
        with pytest.raises(ValueError, match="status must be one of"):
            ApprovalRequest(
                request_id="r1", deployment_id="d1", operation="deploy",
                requester="alice", status="BOGUS",
            )

    def test_to_dict(self):
        request = ApprovalRequest(
            request_id="r1", deployment_id="d1", operation="deploy",
            requester="alice", status="PENDING",
        )

        assert request.to_dict() == {
            "request_id": "r1",
            "deployment_id": "d1",
            "operation": "deploy",
            "requester": "alice",
            "status": "PENDING",
        }

    def test_every_status_is_in_approval_statuses(self):
        assert set(APPROVAL_STATUSES) == {
            "PENDING", "APPROVED", "REJECTED", "CANCELLED",
        }


class TestApprovalDecision:

    def test_rejects_empty_approver(self):
        with pytest.raises(
            ValueError, match="approver must not be empty"
        ):
            ApprovalDecision(
                approver="", approved=True, reason=None,
                decided_at=BASE_TIME,
            )

    def test_rejects_naive_decided_at(self):
        with pytest.raises(
            ValueError, match="decided_at must be timezone-aware"
        ):
            ApprovalDecision(
                approver="alice", approved=True, reason=None,
                decided_at=datetime(2026, 7, 24, 12, 0, 0),
            )

    def test_to_dict(self):
        decision = ApprovalDecision(
            approver="alice", approved=True, reason="looks good",
            decided_at=BASE_TIME,
        )

        assert decision.to_dict() == {
            "approver": "alice",
            "approved": True,
            "reason": "looks good",
            "decided_at": BASE_TIME.isoformat(),
        }


# --- Request creation ----------------------------------------------------


class TestRequestCreation:

    def test_create_request(self):
        engine = _engine()

        request = engine.create_request("d1", "deploy", "alice")

        assert request.deployment_id == "d1"
        assert request.operation == "deploy"
        assert request.requester == "alice"
        assert request.status == "PENDING"

    def test_rejects_empty_deployment_id(self):
        engine = _engine()

        with pytest.raises(
            ValueError, match="deployment_id must not be empty"
        ):
            engine.create_request("", "deploy", "alice")

    def test_publishes_approval_requested(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("approval_requested", events.append)
        engine = _engine(event_bus=bus)

        engine.create_request("d1", "deploy", "alice")

        assert len(events) == 1

    def test_one_active_approval_per_deployment_operation(self):
        engine = _engine()
        engine.create_request("d1", "deploy", "alice")

        with pytest.raises(ValueError, match="already has an active"):
            engine.create_request("d1", "deploy", "bob")

    def test_different_operation_is_allowed(self):
        engine = _engine()
        engine.create_request("d1", "deploy", "alice")

        request = engine.create_request("d1", "rollback", "bob")

        assert request.operation == "rollback"

    def test_new_request_allowed_after_prior_one_decided(self):
        engine = _engine()
        rbac = DeploymentRBACEngine(clock=_clock)
        rbac.assign_role("carol", "Release Manager")
        engine.set_rbac_engine(rbac)

        first = engine.create_request("d1", "deploy", "alice")
        engine.approve(first.request_id, "carol")

        second = engine.create_request("d1", "deploy", "alice")

        assert second.status == "PENDING"


# --- Approval flow -------------------------------------------------------


class TestApprovalFlow:

    def test_approve_transitions_to_approved(self):
        engine = _engine()
        request = engine.create_request("d1", "deploy", "alice")

        approved = engine.approve(request.request_id, "carol")

        assert approved.status == "APPROVED"

    def test_approve_records_decision(self):
        engine = _engine()
        request = engine.create_request("d1", "deploy", "alice")

        engine.approve(request.request_id, "carol", reason="ship it")

        decision = engine.decision(request.request_id)

        assert decision.approver == "carol"
        assert decision.approved is True
        assert decision.reason == "ship it"

    def test_approve_is_idempotent(self):
        engine = _engine()
        request = engine.create_request("d1", "deploy", "alice")
        engine.approve(request.request_id, "carol")

        approved_again = engine.approve(request.request_id, "carol")

        assert approved_again.status == "APPROVED"

    def test_approve_unknown_request_raises(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.approve("does-not-exist", "carol")

    def test_approve_already_rejected_raises(self):
        engine = _engine()
        request = engine.create_request("d1", "deploy", "alice")
        engine.reject(request.request_id, "carol")

        with pytest.raises(ValueError, match="already in status"):
            engine.approve(request.request_id, "carol")

    def test_approve_already_cancelled_raises(self):
        engine = _engine()
        request = engine.create_request("d1", "deploy", "alice")
        engine.cancel(request.request_id)

        with pytest.raises(ValueError, match="already in status"):
            engine.approve(request.request_id, "carol")

    def test_publishes_approval_granted(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("approval_granted", events.append)
        engine = _engine(event_bus=bus)
        request = engine.create_request("d1", "deploy", "alice")

        engine.approve(request.request_id, "carol")

        assert len(events) == 1


# --- Rejection flow --------------------------------------------------------


class TestRejectionFlow:

    def test_reject_transitions_to_rejected(self):
        engine = _engine()
        request = engine.create_request("d1", "deploy", "alice")

        rejected = engine.reject(
            request.request_id, "carol", reason="not ready"
        )

        assert rejected.status == "REJECTED"

    def test_reject_records_decision(self):
        engine = _engine()
        request = engine.create_request("d1", "deploy", "alice")

        engine.reject(request.request_id, "carol", reason="not ready")

        decision = engine.decision(request.request_id)

        assert decision.approved is False
        assert decision.reason == "not ready"

    def test_reject_is_idempotent(self):
        engine = _engine()
        request = engine.create_request("d1", "deploy", "alice")
        engine.reject(request.request_id, "carol")

        rejected_again = engine.reject(request.request_id, "carol")

        assert rejected_again.status == "REJECTED"

    def test_reject_already_approved_raises(self):
        engine = _engine()
        request = engine.create_request("d1", "deploy", "alice")
        engine.approve(request.request_id, "carol")

        with pytest.raises(ValueError, match="already in status"):
            engine.reject(request.request_id, "carol")

    def test_publishes_approval_rejected(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("approval_rejected", events.append)
        engine = _engine(event_bus=bus)
        request = engine.create_request("d1", "deploy", "alice")

        engine.reject(request.request_id, "carol")

        assert len(events) == 1


# --- Unauthorized approval -------------------------------------------------


class TestUnauthorizedApproval:

    def test_approve_denied_without_permission(self):
        rbac = DeploymentRBACEngine(clock=_clock)
        rbac.assign_role("mallory", "Read Only")
        engine = _engine(rbac_engine=rbac)
        request = engine.create_request("d1", "deploy", "alice")

        with pytest.raises(PermissionError):
            engine.approve(request.request_id, "mallory")

    def test_reject_denied_without_permission(self):
        rbac = DeploymentRBACEngine(clock=_clock)
        rbac.assign_role("mallory", "Read Only")
        engine = _engine(rbac_engine=rbac)
        request = engine.create_request("d1", "deploy", "alice")

        with pytest.raises(PermissionError):
            engine.reject(request.request_id, "mallory")

    def test_request_stays_pending_after_denied_approval(self):
        rbac = DeploymentRBACEngine(clock=_clock)
        rbac.assign_role("mallory", "Read Only")
        engine = _engine(rbac_engine=rbac)
        request = engine.create_request("d1", "deploy", "alice")

        with pytest.raises(PermissionError):
            engine.approve(request.request_id, "mallory")

        assert engine.get(request.request_id).status == "PENDING"

    def test_approve_allowed_with_permission(self):
        rbac = DeploymentRBACEngine(clock=_clock)
        rbac.assign_role("carol", "Release Manager")
        engine = _engine(rbac_engine=rbac)
        request = engine.create_request("d1", "deploy", "alice")

        approved = engine.approve(request.request_id, "carol")

        assert approved.status == "APPROVED"

    def test_no_rbac_engine_allows_any_approver(self):
        engine = _engine()
        request = engine.create_request("d1", "deploy", "alice")

        approved = engine.approve(request.request_id, "anyone")

        assert approved.status == "APPROVED"

    def test_cancel_has_no_permission_check(self):
        rbac = DeploymentRBACEngine(clock=_clock)
        engine = _engine(rbac_engine=rbac)
        request = engine.create_request("d1", "deploy", "alice")

        cancelled = engine.cancel(request.request_id)

        assert cancelled.status == "CANCELLED"


# --- Cancel ----------------------------------------------------------------


class TestCancel:

    def test_cancel_transitions_to_cancelled(self):
        engine = _engine()
        request = engine.create_request("d1", "deploy", "alice")

        cancelled = engine.cancel(request.request_id)

        assert cancelled.status == "CANCELLED"

    def test_cancel_is_idempotent(self):
        engine = _engine()
        request = engine.create_request("d1", "deploy", "alice")
        engine.cancel(request.request_id)

        cancelled_again = engine.cancel(request.request_id)

        assert cancelled_again.status == "CANCELLED"

    def test_cancel_unknown_raises(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.cancel("does-not-exist")

    def test_cancel_already_approved_raises(self):
        engine = _engine()
        request = engine.create_request("d1", "deploy", "alice")
        engine.approve(request.request_id, "carol")

        with pytest.raises(ValueError, match="already in status"):
            engine.cancel(request.request_id)

    def test_publishes_approval_cancelled(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("approval_cancelled", events.append)
        engine = _engine(event_bus=bus)
        request = engine.create_request("d1", "deploy", "alice")

        engine.cancel(request.request_id)

        assert len(events) == 1


# --- Pending request listing ------------------------------------------------


class TestPendingRequestListing:

    def test_list_pending_returns_only_pending(self):
        engine = _engine()
        first = engine.create_request("d1", "deploy", "alice")
        second = engine.create_request("d2", "deploy", "alice")
        engine.approve(first.request_id, "carol")

        pending = engine.list_pending()

        assert [r.request_id for r in pending] == [second.request_id]

    def test_list_pending_ordered_by_creation(self):
        engine = _engine()
        first = engine.create_request("d1", "deploy", "alice")
        second = engine.create_request("d2", "deploy", "alice")

        pending = engine.list_pending()

        assert [r.request_id for r in pending] == [
            first.request_id, second.request_id,
        ]

    def test_list_pending_empty_when_none_pending(self):
        engine = _engine()

        assert engine.list_pending() == ()

    def test_list_returns_every_status(self):
        engine = _engine()
        first = engine.create_request("d1", "deploy", "alice")
        second = engine.create_request("d2", "deploy", "alice")
        engine.approve(first.request_id, "carol")

        all_requests = engine.list()

        assert {r.request_id for r in all_requests} == {
            first.request_id, second.request_id,
        }


# --- get() and decision() ---------------------------------------------------


class TestGetAndDecision:

    def test_get_unknown_raises(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.get("does-not-exist")

    def test_decision_before_decided_raises(self):
        engine = _engine()
        request = engine.create_request("d1", "deploy", "alice")

        with pytest.raises(KeyError):
            engine.decision(request.request_id)

    def test_decision_unknown_request_raises(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.decision("does-not-exist")


# --- Clear -------------------------------------------------------------


class TestClear:

    def test_clear_removes_every_request(self):
        engine = _engine()
        engine.create_request("d1", "deploy", "alice")

        engine.clear()

        assert engine.list() == ()


# --- Singleton wiring --------------------------------------------------


class TestSingleton:

    def test_get_approval_engine_returns_same_instance(self):
        assert get_approval_engine() is get_approval_engine()

    def test_singleton_is_wired_into_rbac_engine(self):
        from backend.observability.deployment_governance_rbac import (
            get_rbac_engine,
        )

        assert get_approval_engine()._rbac_engine is get_rbac_engine()


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceApprovalApi:

    def test_post_creates_request(self, client):
        response = client.post(
            "/governance/security/approvals",
            params={
                "deployment_id": "api-d-1", "operation": "deploy",
                "requester": "alice",
            },
        )

        assert response.status_code == 200
        assert response.json()["status"] == "PENDING"

    def test_post_duplicate_active_returns_409(self, client):
        client.post(
            "/governance/security/approvals",
            params={
                "deployment_id": "api-d-2", "operation": "deploy",
                "requester": "alice",
            },
        )

        response = client.post(
            "/governance/security/approvals",
            params={
                "deployment_id": "api-d-2", "operation": "deploy",
                "requester": "bob",
            },
        )

        assert response.status_code == 409

    def test_get_request_by_id(self, client):
        create_response = client.post(
            "/governance/security/approvals",
            params={
                "deployment_id": "api-d-3", "operation": "deploy",
                "requester": "alice",
            },
        )

        request_id = create_response.json()["request_id"]

        response = client.get(
            f"/governance/security/approvals/{request_id}"
        )

        assert response.status_code == 200
        assert response.json()["request_id"] == request_id

    def test_get_unknown_returns_404(self, client):
        response = client.get(
            "/governance/security/approvals/does-not-exist"
        )

        assert response.status_code == 404

    def test_get_pending_lists_request(self, client):
        create_response = client.post(
            "/governance/security/approvals",
            params={
                "deployment_id": "api-d-4", "operation": "deploy",
                "requester": "alice",
            },
        )

        request_id = create_response.json()["request_id"]

        response = client.get("/governance/security/approvals/pending")

        assert response.status_code == 200
        assert any(
            r["request_id"] == request_id for r in response.json()
        )

    def test_post_approve_denied_returns_403(self, client):
        from backend.observability.deployment_governance_rbac import (
            get_rbac_engine,
        )

        get_rbac_engine().assign_role("api-mallory", "Read Only")

        create_response = client.post(
            "/governance/security/approvals",
            params={
                "deployment_id": "api-d-5", "operation": "deploy",
                "requester": "alice",
            },
        )

        request_id = create_response.json()["request_id"]

        response = client.post(
            f"/governance/security/approvals/{request_id}/approve",
            params={"approver": "api-mallory"},
        )

        assert response.status_code == 403

    def test_post_approve_allowed_with_permission(self, client):
        from backend.observability.deployment_governance_rbac import (
            get_rbac_engine,
        )

        get_rbac_engine().assign_role("api-carol", "Release Manager")

        create_response = client.post(
            "/governance/security/approvals",
            params={
                "deployment_id": "api-d-6", "operation": "deploy",
                "requester": "alice",
            },
        )

        request_id = create_response.json()["request_id"]

        response = client.post(
            f"/governance/security/approvals/{request_id}/approve",
            params={"approver": "api-carol"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "APPROVED"

    def test_post_reject(self, client):
        from backend.observability.deployment_governance_rbac import (
            get_rbac_engine,
        )

        get_rbac_engine().assign_role("api-carol-2", "Release Manager")

        create_response = client.post(
            "/governance/security/approvals",
            params={
                "deployment_id": "api-d-7", "operation": "deploy",
                "requester": "alice",
            },
        )

        request_id = create_response.json()["request_id"]

        response = client.post(
            f"/governance/security/approvals/{request_id}/reject",
            params={"approver": "api-carol-2", "reason": "not ready"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "REJECTED"

    def test_post_approve_unknown_returns_404(self, client):
        response = client.post(
            "/governance/security/approvals/does-not-exist/approve",
            params={"approver": "carol"},
        )

        assert response.status_code == 404
