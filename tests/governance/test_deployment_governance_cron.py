from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_cron import (
    CronEvaluation,
    CronTrigger,
    GovernanceCronScheduler,
)

# A Wednesday at 08:00 UTC.
BASE_TIME = datetime(2026, 7, 22, 8, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


@pytest.fixture(autouse=True)
def _reset_singletons():
    """
    The cron scheduler, job persistence layer, and job registry are
    all process-wide singletons wired together (job persistence now
    persists cron triggers too), so tests that touch any of them
    (directly or via the API) must not leak state into other tests.
    """

    from backend.observability.deployment_governance_cron import (
        get_cron_scheduler,
    )
    from backend.observability.deployment_governance_job_persistence import (
        get_job_persistence,
    )
    from backend.observability.deployment_governance_job_registry import (
        get_job_registry,
    )
    from backend.observability.deployment_governance_lifecycle import (
        get_lifecycle_manager,
    )

    def _reset():
        get_lifecycle_manager().shutdown()
        get_job_persistence().clear()
        get_cron_scheduler().clear()
        get_job_registry().clear()

    _reset()
    yield
    _reset()


# --- Models --------------------------------------------------------------


class TestCronTrigger:

    def test_rejects_empty_trigger_id(self):
        with pytest.raises(ValueError, match="trigger_id must not be empty"):
            CronTrigger(
                trigger_id="", job_id="job-1", expression="* * * * *",
                timezone="UTC", enabled=True, next_run=None,
            )

    def test_rejects_empty_expression(self):
        with pytest.raises(ValueError, match="expression must not be empty"):
            CronTrigger(
                trigger_id="t-1", job_id="job-1", expression="",
                timezone="UTC", enabled=True, next_run=None,
            )

    def test_rejects_naive_next_run(self):
        with pytest.raises(
            ValueError, match="next_run must be timezone-aware"
        ):
            CronTrigger(
                trigger_id="t-1", job_id="job-1", expression="* * * * *",
                timezone="UTC", enabled=True,
                next_run=datetime(2026, 7, 22, 8, 0, 0),
            )

    def test_to_dict(self):
        trigger = CronTrigger(
            trigger_id="t-1", job_id="job-1", expression="0 9 * * *",
            timezone="UTC", enabled=True, next_run=BASE_TIME,
        )

        assert trigger.to_dict() == {
            "trigger_id": "t-1",
            "job_id": "job-1",
            "expression": "0 9 * * *",
            "timezone": "UTC",
            "enabled": True,
            "next_run": BASE_TIME.isoformat(),
        }


class TestCronEvaluation:

    def test_rejects_naive_evaluated_at(self):
        with pytest.raises(
            ValueError, match="evaluated_at must be timezone-aware"
        ):
            CronEvaluation(
                trigger_id="t-1", matched=True,
                evaluated_at=datetime(2026, 7, 22, 8, 0, 0),
                next_run=None,
            )

    def test_to_dict(self):
        evaluation = CronEvaluation(
            trigger_id="t-1", matched=True, evaluated_at=BASE_TIME,
            next_run=BASE_TIME + timedelta(minutes=1),
        )

        assert evaluation.to_dict() == {
            "trigger_id": "t-1",
            "matched": True,
            "evaluated_at": BASE_TIME.isoformat(),
            "next_run": (BASE_TIME + timedelta(minutes=1)).isoformat(),
        }


# --- Valid expression -----------------------------------------------


class TestValidExpression:

    def test_register_accepts_a_valid_expression(self):
        scheduler = GovernanceCronScheduler(clock=_clock)

        trigger = scheduler.register("job-1", expression="0 9 * * *")

        assert trigger.expression == "0 9 * * *"
        assert trigger.next_run is not None

    def test_validate_returns_true_for_valid_expression(self):
        scheduler = GovernanceCronScheduler(clock=_clock)

        assert scheduler.validate("*/5 * * * *") is True


# --- Invalid expression rejection -------------------------------------


class TestInvalidExpressionRejection:

    def test_register_rejects_wrong_field_count(self):
        scheduler = GovernanceCronScheduler(clock=_clock)

        with pytest.raises(ValueError, match="exactly 5 fields"):
            scheduler.register("job-1", expression="* * * *")

    def test_register_rejects_out_of_range_value(self):
        scheduler = GovernanceCronScheduler(clock=_clock)

        with pytest.raises(ValueError, match="invalid"):
            scheduler.register("job-1", expression="60 * * * *")

    def test_register_rejects_garbage_value(self):
        scheduler = GovernanceCronScheduler(clock=_clock)

        with pytest.raises(ValueError, match="invalid"):
            scheduler.register("job-1", expression="abc * * * *")

    def test_register_rejects_unknown_timezone(self):
        scheduler = GovernanceCronScheduler(clock=_clock)

        with pytest.raises(ValueError, match="unknown timezone"):
            scheduler.register(
                "job-1", expression="* * * * *", timezone="Not/ARealZone",
            )

    def test_validate_returns_false_for_invalid_expression(self):
        scheduler = GovernanceCronScheduler(clock=_clock)

        assert scheduler.validate("* * * *") is False

    def test_invalid_expression_is_not_registered(self):
        scheduler = GovernanceCronScheduler(clock=_clock)

        with pytest.raises(ValueError):
            scheduler.register("job-1", expression="not a cron")

        assert scheduler.list() == ()


# --- Wildcard scheduling -------------------------------------------------


class TestWildcardScheduling:

    def test_every_minute_fires_one_minute_later(self):
        scheduler = GovernanceCronScheduler(clock=_clock)

        trigger = scheduler.register("job-1", expression="* * * * *")

        assert trigger.next_run == BASE_TIME + timedelta(minutes=1)


# --- List scheduling ---------------------------------------------------


class TestListScheduling:

    def test_comma_separated_minutes(self):
        scheduler = GovernanceCronScheduler(clock=_clock)

        # BASE_TIME is 08:00; "0,30" should next fire at 08:30.
        trigger = scheduler.register("job-1", expression="0,30 * * * *")

        assert trigger.next_run == BASE_TIME + timedelta(minutes=30)


# --- Range scheduling ----------------------------------------------------


class TestRangeScheduling:

    def test_hour_range_restricts_matching_hours(self):
        scheduler = GovernanceCronScheduler(clock=_clock)

        # BASE_TIME is Wed 08:00; hours 9-17 means next match is 09:00.
        trigger = scheduler.register("job-1", expression="0 9-17 * * *")

        assert trigger.next_run == BASE_TIME.replace(
            hour=9, minute=0
        ) + timedelta(hours=0)


# --- Step scheduling ---------------------------------------------------


class TestStepScheduling:

    def test_step_values_every_15_minutes(self):
        scheduler = GovernanceCronScheduler(clock=_clock)

        # BASE_TIME is 08:00:00; "*/15" next fires at 08:15.
        trigger = scheduler.register("job-1", expression="*/15 * * * *")

        assert trigger.next_run == BASE_TIME + timedelta(minutes=15)

    def test_stepped_hour_field(self):
        scheduler = GovernanceCronScheduler(clock=_clock)

        # Every 6 hours: 0, 6, 12, 18. BASE_TIME is 08:00, so next is 12:00.
        trigger = scheduler.register("job-1", expression="0 */6 * * *")

        assert trigger.next_run == BASE_TIME.replace(hour=12, minute=0)


# --- Next execution calculation -------------------------------------


class TestNextExecutionCalculation:

    def test_next_run_getter_matches_registration(self):
        scheduler = GovernanceCronScheduler(clock=_clock)
        trigger = scheduler.register("job-1", expression="0 9 * * *")

        assert scheduler.next_run(trigger.trigger_id) == trigger.next_run

    def test_next_run_unknown_trigger_raises(self):
        scheduler = GovernanceCronScheduler(clock=_clock)

        with pytest.raises(KeyError):
            scheduler.next_run("ghost")

    def test_disabled_trigger_has_no_next_run(self):
        scheduler = GovernanceCronScheduler(clock=_clock)

        trigger = scheduler.register(
            "job-1", expression="0 9 * * *", enabled=False,
        )

        assert trigger.next_run is None

    def test_reschedule_advances_past_a_fired_occurrence(self):
        scheduler = GovernanceCronScheduler(clock=_clock)
        trigger = scheduler.register("job-1", expression="0 9 * * *")
        fire_time = trigger.next_run

        updated = scheduler.reschedule(trigger.trigger_id, at=fire_time)

        assert updated.next_run > fire_time

    def test_reschedule_unknown_trigger_raises(self):
        scheduler = GovernanceCronScheduler(clock=_clock)

        with pytest.raises(KeyError):
            scheduler.reschedule("ghost")

    def test_evaluate_matches_at_next_run(self):
        scheduler = GovernanceCronScheduler(clock=_clock)
        trigger = scheduler.register("job-1", expression="0 9 * * *")

        evaluation = scheduler.evaluate(
            trigger.trigger_id, at=trigger.next_run,
        )

        assert evaluation.matched is True
        assert evaluation.next_run > trigger.next_run

    def test_evaluate_does_not_match_before_next_run(self):
        scheduler = GovernanceCronScheduler(clock=_clock)
        trigger = scheduler.register("job-1", expression="0 9 * * *")

        evaluation = scheduler.evaluate(trigger.trigger_id, at=BASE_TIME)

        assert evaluation.matched is False

    def test_evaluate_does_not_mutate_stored_next_run(self):
        scheduler = GovernanceCronScheduler(clock=_clock)
        trigger = scheduler.register("job-1", expression="0 9 * * *")

        scheduler.evaluate(trigger.trigger_id, at=trigger.next_run)

        assert scheduler.next_run(trigger.trigger_id) == trigger.next_run

    def test_evaluate_unknown_trigger_raises(self):
        scheduler = GovernanceCronScheduler(clock=_clock)

        with pytest.raises(KeyError):
            scheduler.evaluate("ghost")

    def test_disabled_trigger_never_matches(self):
        scheduler = GovernanceCronScheduler(clock=_clock)
        trigger = scheduler.register(
            "job-1", expression="* * * * *", enabled=False,
        )

        evaluation = scheduler.evaluate(
            trigger.trigger_id, at=BASE_TIME + timedelta(days=1),
        )

        assert evaluation.matched is False


# --- Timezone-aware scheduling ---------------------------------------


class TestTimezoneAwareScheduling:

    def test_non_utc_timezone_computes_correct_utc_next_run(self):
        scheduler = GovernanceCronScheduler(clock=_clock)

        # 09:00 America/New_York in July is UTC-4 (EDT) -> 13:00 UTC.
        trigger = scheduler.register(
            "job-1", expression="0 9 * * *", timezone="America/New_York",
        )

        assert trigger.next_run.hour == 13
        assert trigger.next_run.tzinfo is not None


# --- Deterministic ordering -----------------------------------------------


class TestDeterministicOrdering:

    def test_list_ordered_by_next_run_then_trigger_id(self):
        scheduler = GovernanceCronScheduler(clock=_clock)
        far = scheduler.register("job-1", expression="0 20 * * *")
        near = scheduler.register("job-2", expression="*/5 * * * *")

        assert [t.trigger_id for t in scheduler.list()] == [
            near.trigger_id, far.trigger_id,
        ]


# --- Job existence validation -------------------------------------------


class TestJobExistenceValidation:

    def test_validates_job_exists_when_job_registry_given(self):
        from backend.observability.deployment_governance_job_registry import (
            GovernanceJobRegistry,
        )

        registry = GovernanceJobRegistry(clock=_clock)
        scheduler = GovernanceCronScheduler(
            clock=_clock, job_registry=registry,
        )

        with pytest.raises(ValueError, match="is not registered"):
            scheduler.register("ghost-job", expression="* * * * *")

    def test_no_job_registry_skips_validation(self):
        scheduler = GovernanceCronScheduler(clock=_clock)

        trigger = scheduler.register("ghost-job", expression="* * * * *")

        assert trigger.job_id == "ghost-job"


# --- Removal / clear ----------------------------------------------------


class TestRemoval:

    def test_remove_removes_trigger(self):
        scheduler = GovernanceCronScheduler(clock=_clock)
        trigger = scheduler.register("job-1", expression="* * * * *")

        scheduler.remove(trigger.trigger_id)

        assert scheduler.list() == ()

    def test_remove_unknown_trigger_raises(self):
        scheduler = GovernanceCronScheduler(clock=_clock)

        with pytest.raises(KeyError):
            scheduler.remove("ghost")


def test_clear_removes_every_trigger():
    scheduler = GovernanceCronScheduler(clock=_clock)
    scheduler.register("job-1", expression="* * * * *")
    scheduler.register("job-2", expression="0 9 * * *")

    scheduler.clear()

    assert scheduler.list() == ()


# --- Event publication ---------------------------------------------------


class TestEventPublication:

    def test_registration_publishes_cron_registered(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        scheduler = GovernanceCronScheduler(clock=_clock, event_bus=bus)
        scheduler.register("job-1", expression="* * * * *")

        assert received == ["cron_registered"]

    def test_remove_publishes_cron_removed(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        scheduler = GovernanceCronScheduler(clock=_clock, event_bus=bus)
        trigger = scheduler.register("job-1", expression="* * * * *")
        received.clear()

        scheduler.remove(trigger.trigger_id)

        assert received == ["cron_removed"]

    def test_evaluate_match_publishes_cron_triggered(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        scheduler = GovernanceCronScheduler(clock=_clock, event_bus=bus)
        trigger = scheduler.register("job-1", expression="0 9 * * *")
        received.clear()

        scheduler.evaluate(trigger.trigger_id, at=trigger.next_run)

        assert received == ["cron_triggered"]

    def test_reschedule_publishes_cron_rescheduled(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        scheduler = GovernanceCronScheduler(clock=_clock, event_bus=bus)
        trigger = scheduler.register("job-1", expression="0 9 * * *")
        received.clear()

        scheduler.reschedule(trigger.trigger_id)

        assert received == ["cron_rescheduled"]


# --- Persistence round-trip -----------------------------------------


class TestPersistenceRoundTrip:

    def test_cron_triggers_persist_and_restore_via_file(self, tmp_path):
        from backend.observability.deployment_governance_job_persistence import (
            GovernanceJobPersistence,
        )
        from backend.observability.deployment_governance_job_registry import (
            GovernanceJobRegistry,
        )

        path = tmp_path / "snapshot.json"

        source_registry = GovernanceJobRegistry(clock=_clock)
        source_registry.register("job-1", "a")
        source_cron = GovernanceCronScheduler(
            clock=_clock, job_registry=source_registry,
        )
        source_cron.register("job-1", expression="0 9 * * *")

        GovernanceJobPersistence(
            clock=_clock, job_registry=source_registry,
            cron_scheduler=source_cron, path=path,
        ).save()

        target_registry = GovernanceJobRegistry(clock=_clock)
        target_registry.register("job-1", "a")
        target_cron = GovernanceCronScheduler(
            clock=_clock, job_registry=target_registry,
        )

        result = GovernanceJobPersistence(
            clock=_clock, job_registry=target_registry,
            cron_scheduler=target_cron, path=path,
        ).load()

        assert result.success is True

        restored = target_cron.list()
        assert len(restored) == 1
        assert restored[0].expression == "0 9 * * *"

    def test_document_without_cron_triggers_key_still_loads(
        self, tmp_path,
    ):
        import json

        from backend.observability.deployment_governance_job_persistence import (
            CURRENT_SCHEMA_VERSION,
            GovernanceJobPersistence,
        )

        path = tmp_path / "snapshot.json"
        path.write_text(
            json.dumps(
                {
                    "version": CURRENT_SCHEMA_VERSION,
                    "created_at": BASE_TIME.isoformat(),
                    "jobs": [],
                    "triggers": [],
                    "pending_retries": [],
                }
            )
        )

        cron = GovernanceCronScheduler(clock=_clock)
        result = GovernanceJobPersistence(
            clock=_clock, cron_scheduler=cron, path=path,
        ).load()

        assert result.success is True
        assert cron.list() == ()


# --- Singleton -------------------------------------------------------------


class TestCronSchedulerSingleton:

    def test_get_cron_scheduler_returns_same_instance(self):
        from backend.observability.deployment_governance_cron import (
            get_cron_scheduler,
        )

        assert get_cron_scheduler() is get_cron_scheduler()


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


@pytest.fixture(autouse=True)
def _registered_job(_reset_singletons):
    """
    The API builds its cron scheduler wired to the shared job registry
    singleton, which validates job_id at registration time, so every
    API test needs "job-1" to actually exist there first. Explicitly
    depends on _reset_singletons so its setup (which clears the
    registry) always runs first.
    """

    from backend.observability.deployment_governance_job_registry import (
        get_job_registry,
    )

    get_job_registry().register("job-1", "a")


class TestGovernanceCronApi:

    def test_get_cron_returns_empty_list_initially(self, client) -> None:
        response = client.get("/governance/cron")

        assert response.status_code == 200
        assert response.json() == []

    def test_post_validate_valid_expression(self, client) -> None:
        response = client.post(
            "/governance/cron/validate",
            params={"expression": "0 9 * * *"},
        )

        assert response.status_code == 200
        assert response.json() == {
            "expression": "0 9 * * *", "valid": True,
        }

    def test_post_validate_invalid_expression(self, client) -> None:
        response = client.post(
            "/governance/cron/validate", params={"expression": "* * * *"},
        )

        assert response.status_code == 200
        assert response.json()["valid"] is False

    def test_post_cron_registers_a_new_trigger(self, client) -> None:
        response = client.post(
            "/governance/cron",
            params={"job_id": "job-1", "expression": "0 9 * * *"},
        )

        assert response.status_code == 200

        payload = response.json()
        assert payload["job_id"] == "job-1"
        assert payload["expression"] == "0 9 * * *"

    def test_post_cron_invalid_expression_returns_409(self, client) -> None:
        response = client.post(
            "/governance/cron",
            params={"job_id": "job-1", "expression": "not a cron"},
        )

        assert response.status_code == 409

    def test_get_cron_trigger_by_id(self, client) -> None:
        create_response = client.post(
            "/governance/cron",
            params={"job_id": "job-1", "expression": "0 9 * * *"},
        )
        trigger_id = create_response.json()["trigger_id"]

        response = client.get(f"/governance/cron/{trigger_id}")

        assert response.status_code == 200
        assert response.json()["trigger_id"] == trigger_id

    def test_get_unknown_cron_trigger_returns_404(self, client) -> None:
        response = client.get("/governance/cron/ghost")

        assert response.status_code == 404

    def test_patch_cron_trigger_reschedules(self, client) -> None:
        create_response = client.post(
            "/governance/cron",
            params={"job_id": "job-1", "expression": "0 9 * * *"},
        )
        trigger_id = create_response.json()["trigger_id"]
        original_next_run = create_response.json()["next_run"]

        response = client.patch(f"/governance/cron/{trigger_id}")

        assert response.status_code == 200
        assert response.json()["next_run"] == original_next_run

    def test_patch_unknown_cron_trigger_returns_404(self, client) -> None:
        response = client.patch("/governance/cron/ghost")

        assert response.status_code == 404

    def test_delete_cron_trigger(self, client) -> None:
        create_response = client.post(
            "/governance/cron",
            params={"job_id": "job-1", "expression": "0 9 * * *"},
        )
        trigger_id = create_response.json()["trigger_id"]

        response = client.delete(f"/governance/cron/{trigger_id}")

        assert response.status_code == 200
        assert response.json() == {"removed": trigger_id}

    def test_delete_unknown_cron_trigger_returns_404(self, client) -> None:
        response = client.delete("/governance/cron/ghost")

        assert response.status_code == 404
