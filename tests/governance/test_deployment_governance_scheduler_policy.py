from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_scheduler_policy import (
    GovernanceSchedulerPolicyEngine,
    SchedulerPolicy,
    SchedulerPolicyDecision,
)

BASE_TIME = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


@pytest.fixture(autouse=True)
def _reset_singletons():
    """
    The scheduler policy engine and scheduler metrics collector are
    both process-wide singletons wired together (every evaluate() call
    records into the shared metrics collector), so tests that touch
    either (directly or via the API) must not leak state into other
    tests.
    """

    from backend.observability.deployment_governance_lifecycle import (
        get_lifecycle_manager,
    )
    from backend.observability.deployment_governance_scheduler_metrics import (
        get_scheduler_metrics,
    )
    from backend.observability.deployment_governance_scheduler_policy import (
        get_scheduler_policy_engine,
    )

    def _reset():
        get_lifecycle_manager().shutdown()
        get_scheduler_policy_engine().clear()
        get_scheduler_metrics().reset()

    _reset()
    yield
    _reset()


# --- Models --------------------------------------------------------------


class TestSchedulerPolicy:

    def test_rejects_empty_name(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            SchedulerPolicy(
                name="", priority=0, enabled=True, conditions={},
            )

    def test_to_dict(self):
        policy = SchedulerPolicy(
            name="p", priority=1, enabled=False,
            conditions={"max_concurrent": 5},
        )

        assert policy.to_dict() == {
            "name": "p",
            "priority": 1,
            "enabled": False,
            "conditions": {"max_concurrent": 5},
        }


class TestSchedulerPolicyDecision:

    def test_rejects_naive_evaluated_at(self):
        with pytest.raises(
            ValueError, match="evaluated_at must be timezone-aware"
        ):
            SchedulerPolicyDecision(
                allowed=True, policy=None, reason=None,
                evaluated_at=datetime(2026, 7, 21, 12, 0, 0),
            )

    def test_rejects_policy_set_when_allowed(self):
        with pytest.raises(
            ValueError, match="must not be set when allowed is True"
        ):
            SchedulerPolicyDecision(
                allowed=True, policy="p", reason=None,
                evaluated_at=BASE_TIME,
            )

    def test_rejects_missing_policy_when_denied(self):
        with pytest.raises(
            ValueError, match="must be set when allowed is False"
        ):
            SchedulerPolicyDecision(
                allowed=False, policy=None, reason=None,
                evaluated_at=BASE_TIME,
            )

    def test_to_dict(self):
        decision = SchedulerPolicyDecision(
            allowed=False, policy="p", reason="boom",
            evaluated_at=BASE_TIME,
        )

        assert decision.to_dict() == {
            "allowed": False,
            "policy": "p",
            "reason": "boom",
            "evaluated_at": BASE_TIME.isoformat(),
        }


# --- Policy registration -------------------------------------------------


class TestPolicyRegistration:

    def test_register_returns_policy(self):
        engine = GovernanceSchedulerPolicyEngine(clock=_clock)

        policy = engine.register("p", priority=1, conditions={"x": 1})

        assert policy.name == "p"
        assert policy.priority == 1
        assert policy.conditions == {"x": 1}

    def test_registered_policy_appears_in_list(self):
        engine = GovernanceSchedulerPolicyEngine(clock=_clock)
        engine.register("p")

        assert [p.name for p in engine.list()] == ["p"]

    def test_register_rejects_unknown_policy_type(self):
        engine = GovernanceSchedulerPolicyEngine(clock=_clock)

        with pytest.raises(
            ValueError, match="unknown built-in scheduler policy type"
        ):
            engine.register("p", policy_type="teleport")

    def test_register_accepts_custom_evaluator(self):
        engine = GovernanceSchedulerPolicyEngine(clock=_clock)

        engine.register(
            "p", evaluator=lambda policy, context: (True, "custom deny"),
        )

        decision = engine.evaluate("job-1")

        assert decision.allowed is False
        assert decision.reason == "custom deny"

    def test_remove_removes_policy(self):
        engine = GovernanceSchedulerPolicyEngine(clock=_clock)
        engine.register("p")

        engine.remove("p")

        assert engine.list() == ()

    def test_remove_unknown_raises(self):
        engine = GovernanceSchedulerPolicyEngine(clock=_clock)

        with pytest.raises(KeyError):
            engine.remove("ghost")


# --- Duplicate rejection -------------------------------------------------


def test_duplicate_policy_name_rejected():
    engine = GovernanceSchedulerPolicyEngine(clock=_clock)
    engine.register("p")

    with pytest.raises(ValueError, match="already registered"):
        engine.register("p")


# --- Priority ordering ---------------------------------------------------


class TestPriorityOrdering:

    def test_list_ordered_by_priority_then_name(self):
        engine = GovernanceSchedulerPolicyEngine(clock=_clock)
        engine.register("z", priority=1)
        engine.register("a", priority=0)
        engine.register("b", priority=0)

        assert [p.name for p in engine.list()] == ["a", "b", "z"]

    def test_lower_priority_policy_evaluated_first(self):
        engine = GovernanceSchedulerPolicyEngine(clock=_clock)
        calls = []

        engine.register(
            "second", priority=1,
            evaluator=lambda p, c: (calls.append("second") or (False, None)),
        )
        engine.register(
            "first", priority=0,
            evaluator=lambda p, c: (calls.append("first") or (False, None)),
        )

        engine.evaluate("job-1")

        assert calls == ["first", "second"]

    def test_first_deny_short_circuits_evaluation(self):
        engine = GovernanceSchedulerPolicyEngine(clock=_clock)
        calls = []

        engine.register(
            "first", priority=0,
            evaluator=lambda p, c: (calls.append("first") or (True, "deny")),
        )
        engine.register(
            "second", priority=1,
            evaluator=lambda p, c: (calls.append("second") or (False, None)),
        )

        decision = engine.evaluate("job-1")

        assert decision.policy == "first"
        assert calls == ["first"]


# --- Allow decision ------------------------------------------------------


class TestAllowDecision:

    def test_no_policies_registered_allows(self):
        engine = GovernanceSchedulerPolicyEngine(clock=_clock)

        decision = engine.evaluate("job-1")

        assert decision.allowed is True
        assert decision.policy is None
        assert decision.reason is None

    def test_non_matching_policy_allows(self):
        engine = GovernanceSchedulerPolicyEngine(clock=_clock)
        engine.register("p", conditions={"maintenance_mode": True})

        decision = engine.evaluate("job-1", {"maintenance_mode": False})

        assert decision.allowed is True


# --- Deny decision -------------------------------------------------------


class TestDenyDecision:

    def test_matching_conditions_deny(self):
        engine = GovernanceSchedulerPolicyEngine(clock=_clock)
        engine.register("p", conditions={"maintenance_mode": True})

        decision = engine.evaluate("job-1", {"maintenance_mode": True})

        assert decision.allowed is False
        assert decision.policy == "p"

    def test_max_concurrent_jobs_built_in(self):
        engine = GovernanceSchedulerPolicyEngine(clock=_clock)
        engine.register(
            "p", policy_type="max_concurrent_jobs",
            conditions={"max_concurrent": 2},
        )

        allowed = engine.evaluate("job-1", {"active_jobs": 1})
        denied = engine.evaluate("job-2", {"active_jobs": 2})

        assert allowed.allowed is True
        assert denied.allowed is False

    def test_maintenance_mode_built_in(self):
        engine = GovernanceSchedulerPolicyEngine(clock=_clock)
        engine.register("p", policy_type="maintenance_mode")

        decision = engine.evaluate("job-1", {"maintenance_mode": True})

        assert decision.allowed is False

    def test_job_enabled_built_in(self):
        engine = GovernanceSchedulerPolicyEngine(clock=_clock)
        engine.register("p", policy_type="job_enabled")

        decision = engine.evaluate("job-1", {"job_enabled": False})

        assert decision.allowed is False

    def test_dependency_satisfied_built_in(self):
        engine = GovernanceSchedulerPolicyEngine(clock=_clock)
        engine.register("p", policy_type="dependency_satisfied")

        decision = engine.evaluate("job-1", {"dependency_ready": False})

        assert decision.allowed is False

    def test_lock_acquired_built_in(self):
        engine = GovernanceSchedulerPolicyEngine(clock=_clock)
        engine.register("p", policy_type="lock_acquired")

        decision = engine.evaluate("job-1", {"lock_acquired": False})

        assert decision.allowed is False

    def test_retry_limit_not_exceeded_built_in(self):
        engine = GovernanceSchedulerPolicyEngine(clock=_clock)
        engine.register("p", policy_type="retry_limit_not_exceeded")

        decision = engine.evaluate(
            "job-1", {"retry_attempt": 3, "max_attempts": 3},
        )

        assert decision.allowed is False

    def test_retry_limit_not_exceeded_allows_when_under_limit(self):
        engine = GovernanceSchedulerPolicyEngine(clock=_clock)
        engine.register("p", policy_type="retry_limit_not_exceeded")

        decision = engine.evaluate(
            "job-1", {"retry_attempt": 1, "max_attempts": 3},
        )

        assert decision.allowed is True

    def test_execution_window_allowed_built_in_denies_outside_window(self):
        engine = GovernanceSchedulerPolicyEngine(clock=_clock)
        engine.register(
            "p", policy_type="execution_window_allowed",
            conditions={"start_hour": 9, "end_hour": 17},
        )

        decision = engine.evaluate(
            "job-1", {"current_time": BASE_TIME.replace(hour=20)},
        )

        assert decision.allowed is False

    def test_execution_window_allowed_built_in_allows_inside_window(self):
        engine = GovernanceSchedulerPolicyEngine(clock=_clock)
        engine.register(
            "p", policy_type="execution_window_allowed",
            conditions={"start_hour": 9, "end_hour": 17},
        )

        decision = engine.evaluate(
            "job-1", {"current_time": BASE_TIME.replace(hour=10)},
        )

        assert decision.allowed is True

    def test_execution_window_wraps_midnight(self):
        engine = GovernanceSchedulerPolicyEngine(clock=_clock)
        engine.register(
            "p", policy_type="execution_window_allowed",
            conditions={"start_hour": 22, "end_hour": 6},
        )

        inside = engine.evaluate(
            "job-1", {"current_time": BASE_TIME.replace(hour=23)},
        )
        outside = engine.evaluate(
            "job-2", {"current_time": BASE_TIME.replace(hour=12)},
        )

        assert inside.allowed is True
        assert outside.allowed is False

    def test_evaluate_all_returns_one_decision_per_job_in_order(self):
        engine = GovernanceSchedulerPolicyEngine(clock=_clock)
        engine.register("p", conditions={"maintenance_mode": True})

        decisions = engine.evaluate_all(
            {
                "z-job": {"maintenance_mode": False},
                "a-job": {"maintenance_mode": True},
            }
        )

        assert [d.allowed for d in decisions] == [False, True]


# --- Disabled policy handling ------------------------------------------


class TestDisabledPolicyHandling:

    def test_disabled_policy_is_ignored(self):
        engine = GovernanceSchedulerPolicyEngine(clock=_clock)
        engine.register(
            "p", enabled=False, conditions={"maintenance_mode": True},
        )

        decision = engine.evaluate("job-1", {"maintenance_mode": True})

        assert decision.allowed is True

    def test_disable_then_enable_round_trips(self):
        engine = GovernanceSchedulerPolicyEngine(clock=_clock)
        engine.register("p", conditions={"maintenance_mode": True})

        engine.disable("p")
        assert engine.evaluate(
            "job-1", {"maintenance_mode": True},
        ).allowed is True

        engine.enable("p")
        assert engine.evaluate(
            "job-1", {"maintenance_mode": True},
        ).allowed is False

    def test_enable_unknown_raises(self):
        engine = GovernanceSchedulerPolicyEngine(clock=_clock)

        with pytest.raises(KeyError):
            engine.enable("ghost")

    def test_disable_unknown_raises(self):
        engine = GovernanceSchedulerPolicyEngine(clock=_clock)

        with pytest.raises(KeyError):
            engine.disable("ghost")


def test_clear_removes_every_policy():
    engine = GovernanceSchedulerPolicyEngine(clock=_clock)
    engine.register("a")
    engine.register("b")

    engine.clear()

    assert engine.list() == ()


# --- Event publication ---------------------------------------------------


class TestEventPublication:

    def test_register_publishes_scheduler_policy_registered(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        engine = GovernanceSchedulerPolicyEngine(clock=_clock, event_bus=bus)
        engine.register("p")

        assert received == ["scheduler_policy_registered"]

    def test_remove_publishes_scheduler_policy_removed(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        engine = GovernanceSchedulerPolicyEngine(clock=_clock, event_bus=bus)
        engine.register("p")
        received.clear()

        engine.remove("p")

        assert received == ["scheduler_policy_removed"]

    def test_allow_publishes_scheduler_policy_allowed(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        engine = GovernanceSchedulerPolicyEngine(clock=_clock, event_bus=bus)
        engine.evaluate("job-1")

        assert received == ["scheduler_policy_allowed"]

    def test_deny_publishes_scheduler_policy_denied(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        engine = GovernanceSchedulerPolicyEngine(clock=_clock, event_bus=bus)
        engine.register("p", conditions={"maintenance_mode": True})
        received.clear()

        engine.evaluate("job-1", {"maintenance_mode": True})

        assert received == ["scheduler_policy_denied"]


# --- Audit integration -------------------------------------------------


class TestAuditIntegration:

    def test_allow_is_audited(self):
        from backend.observability.deployment_governance_audit import (
            AuditQuery,
            GovernanceAuditService,
        )

        audit_service = GovernanceAuditService(clock=_clock)
        engine = GovernanceSchedulerPolicyEngine(
            clock=_clock, audit_service=audit_service,
        )

        engine.evaluate("job-1")

        records = audit_service.query(
            AuditQuery(action="scheduler_policy_allowed")
        )
        assert len(records) == 1
        assert records[0].outcome == "success"
        assert records[0].resource == "job-1"

    def test_deny_is_audited(self):
        from backend.observability.deployment_governance_audit import (
            AuditQuery,
            GovernanceAuditService,
        )

        audit_service = GovernanceAuditService(clock=_clock)
        engine = GovernanceSchedulerPolicyEngine(
            clock=_clock, audit_service=audit_service,
        )
        engine.register("p", conditions={"maintenance_mode": True})

        engine.evaluate("job-1", {"maintenance_mode": True})

        records = audit_service.query(
            AuditQuery(action="scheduler_policy_denied")
        )
        assert len(records) == 1
        assert records[0].outcome == "failure"


# --- Metrics integration -------------------------------------------------


class TestMetricsIntegration:

    def test_allow_recorded_in_metrics(self):
        from backend.observability.deployment_governance_scheduler_metrics import (
            GovernanceSchedulerMetrics,
        )

        metrics = GovernanceSchedulerMetrics(clock=_clock)
        engine = GovernanceSchedulerPolicyEngine(
            clock=_clock, metrics=metrics,
        )

        engine.evaluate("job-1")

        assert metrics.policy_decisions == {"allowed": 1, "denied": 0}

    def test_deny_recorded_in_metrics(self):
        from backend.observability.deployment_governance_scheduler_metrics import (
            GovernanceSchedulerMetrics,
        )

        metrics = GovernanceSchedulerMetrics(clock=_clock)
        engine = GovernanceSchedulerPolicyEngine(
            clock=_clock, metrics=metrics,
        )
        engine.register("p", conditions={"maintenance_mode": True})

        engine.evaluate("job-1", {"maintenance_mode": True})

        assert metrics.policy_decisions == {"allowed": 0, "denied": 1}


# --- Scheduler integration -------------------------------------------


class TestSchedulerIntegration:

    def test_run_due_denies_jobs_via_policy(self):
        from backend.observability.deployment_governance_execution_manager import (
            GovernanceExecutionManager,
        )
        from backend.observability.deployment_governance_job_registry import (
            GovernanceJobRegistry,
        )
        from backend.observability.deployment_governance_scheduler import (
            GovernanceScheduler,
        )
        from backend.observability.deployment_governance_trigger_engine import (
            GovernanceTriggerEngine,
        )

        class _ControllableClock:
            def __init__(self, start):
                self.now = start

            def __call__(self):
                return self.now

        clock = _ControllableClock(BASE_TIME)

        job_registry = GovernanceJobRegistry(clock=clock)
        trigger_engine = GovernanceTriggerEngine(
            clock=clock, job_registry=job_registry,
        )
        scheduler = GovernanceScheduler(
            clock=clock, job_registry=job_registry,
            trigger_engine=trigger_engine,
        )
        execution_manager = GovernanceExecutionManager(clock=clock)
        policy_engine = GovernanceSchedulerPolicyEngine(clock=clock)
        policy_engine.register("maintenance", policy_type="maintenance_mode")

        # Force denial via a custom evaluator standing in for a "global
        # maintenance mode is on" condition the scheduler's own context
        # always reports (since run_due()'s built context does not
        # itself surface a maintenance flag).
        policy_engine.remove("maintenance")
        policy_engine.register(
            "deny-all", evaluator=lambda policy, context: (True, "denied"),
        )

        scheduler.start()
        scheduler.register("a", interval_seconds=60)
        clock.now = BASE_TIME + timedelta(seconds=61)

        results = scheduler.run_due(
            execution_manager, policy_engine=policy_engine,
        )

        assert results == ()

    def test_run_due_allows_jobs_via_policy(self):
        from backend.observability.deployment_governance_execution_manager import (
            GovernanceExecutionManager,
        )
        from backend.observability.deployment_governance_job_registry import (
            GovernanceJobRegistry,
        )
        from backend.observability.deployment_governance_scheduler import (
            GovernanceScheduler,
        )
        from backend.observability.deployment_governance_trigger_engine import (
            GovernanceTriggerEngine,
        )

        class _ControllableClock:
            def __init__(self, start):
                self.now = start

            def __call__(self):
                return self.now

        clock = _ControllableClock(BASE_TIME)

        job_registry = GovernanceJobRegistry(clock=clock)
        trigger_engine = GovernanceTriggerEngine(
            clock=clock, job_registry=job_registry,
        )
        scheduler = GovernanceScheduler(
            clock=clock, job_registry=job_registry,
            trigger_engine=trigger_engine,
        )
        execution_manager = GovernanceExecutionManager(clock=clock)
        policy_engine = GovernanceSchedulerPolicyEngine(clock=clock)

        scheduler.start()
        scheduler.register("a", interval_seconds=60)
        clock.now = BASE_TIME + timedelta(seconds=61)

        results = scheduler.run_due(
            execution_manager, policy_engine=policy_engine,
        )

        assert len(results) == 1
        assert results[0].status == "SUCCEEDED"

    def test_run_due_releases_lock_when_policy_denies(self):
        from backend.observability.deployment_governance_execution_manager import (
            GovernanceExecutionManager,
        )
        from backend.observability.deployment_governance_job_registry import (
            GovernanceJobRegistry,
        )
        from backend.observability.deployment_governance_scheduler import (
            GovernanceScheduler,
        )
        from backend.observability.deployment_governance_scheduler_locks import (
            GovernanceSchedulerLockManager,
        )
        from backend.observability.deployment_governance_trigger_engine import (
            GovernanceTriggerEngine,
        )

        class _ControllableClock:
            def __init__(self, start):
                self.now = start

            def __call__(self):
                return self.now

        clock = _ControllableClock(BASE_TIME)

        job_registry = GovernanceJobRegistry(clock=clock)
        trigger_engine = GovernanceTriggerEngine(
            clock=clock, job_registry=job_registry,
        )
        scheduler = GovernanceScheduler(
            clock=clock, job_registry=job_registry,
            trigger_engine=trigger_engine, owner_id="node-1",
        )
        execution_manager = GovernanceExecutionManager(clock=clock)
        lock_manager = GovernanceSchedulerLockManager(clock=clock)
        policy_engine = GovernanceSchedulerPolicyEngine(clock=clock)
        policy_engine.register(
            "deny-all", evaluator=lambda policy, context: (True, "denied"),
        )

        scheduler.start()
        job = scheduler.register("a", interval_seconds=60)
        clock.now = BASE_TIME + timedelta(seconds=61)

        scheduler.run_due(
            execution_manager, lock_manager=lock_manager,
            policy_engine=policy_engine,
        )

        assert lock_manager.is_locked(job.job_id) is False


# --- Singleton -------------------------------------------------------------


class TestSchedulerPolicyEngineSingleton:

    def test_get_scheduler_policy_engine_returns_same_instance(self):
        from backend.observability.deployment_governance_scheduler_policy import (
            get_scheduler_policy_engine,
        )

        assert (
            get_scheduler_policy_engine()
            is get_scheduler_policy_engine()
        )


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceSchedulerPolicyApi:

    def test_get_policies_returns_empty_list_initially(self, client) -> None:
        response = client.get("/governance/scheduler/policies")

        assert response.status_code == 200
        assert response.json() == []

    def test_post_registers_a_new_policy(self, client) -> None:
        response = client.post(
            "/governance/scheduler/policies",
            params={"name": "p", "priority": 1},
        )

        assert response.status_code == 200
        assert response.json()["name"] == "p"

    def test_post_duplicate_returns_409(self, client) -> None:
        client.post("/governance/scheduler/policies", params={"name": "p"})

        response = client.post(
            "/governance/scheduler/policies", params={"name": "p"},
        )

        assert response.status_code == 409

    def test_patch_disables_a_policy(self, client) -> None:
        client.post("/governance/scheduler/policies", params={"name": "p"})

        response = client.patch(
            "/governance/scheduler/policies/p", params={"enabled": False},
        )

        assert response.status_code == 200
        assert response.json()["enabled"] is False

    def test_patch_unknown_returns_404(self, client) -> None:
        response = client.patch(
            "/governance/scheduler/policies/ghost",
            params={"enabled": False},
        )

        assert response.status_code == 404

    def test_delete_removes_a_policy(self, client) -> None:
        client.post("/governance/scheduler/policies", params={"name": "p"})

        response = client.delete("/governance/scheduler/policies/p")

        assert response.status_code == 200
        assert response.json() == {"removed": "p"}

    def test_delete_unknown_returns_404(self, client) -> None:
        response = client.delete("/governance/scheduler/policies/ghost")

        assert response.status_code == 404

    def test_post_evaluate_allows_by_default(self, client) -> None:
        response = client.post(
            "/governance/scheduler/policies/evaluate",
            params={"job_id": "job-1"},
        )

        assert response.status_code == 200
        assert response.json()["allowed"] is True

    def test_post_evaluate_denies_when_matched(self, client) -> None:
        import json

        client.post(
            "/governance/scheduler/policies",
            params={
                "name": "p",
                "conditions": json.dumps({"maintenance_mode": True}),
            },
        )

        response = client.post(
            "/governance/scheduler/policies/evaluate",
            params={
                "job_id": "job-1",
                "context": json.dumps({"maintenance_mode": True}),
            },
        )

        assert response.status_code == 200
        assert response.json()["allowed"] is False
