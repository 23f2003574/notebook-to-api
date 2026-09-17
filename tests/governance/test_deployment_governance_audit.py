from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_audit import (
    AuditChainVerification,
    AuditQuery,
    AuditRecord,
    GENESIS_HASH,
    GovernanceAuditService,
)

BASE_TIME = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


@pytest.fixture(autouse=True)
def _reset_singletons():
    """
    The lifecycle manager, event bus, event history, event router,
    and audit service are all process-wide singletons wired together,
    so tests touching any of them (directly or via the API) must not
    leak state into other tests.
    """

    from backend.observability.deployment_governance_audit import (
        get_audit_service,
    )
    from backend.observability.deployment_governance_event_bus import (
        get_event_bus,
    )
    from backend.observability.deployment_governance_event_history import (
        get_event_history,
    )
    from backend.observability.deployment_governance_lifecycle import (
        get_lifecycle_manager,
    )

    def _reset():
        get_lifecycle_manager().shutdown()
        get_event_history().purge()
        get_audit_service().purge()
        get_event_bus().clear()

    _reset()
    yield
    _reset()


# --- Model -------------------------------------------------------------


class TestAuditRecord:

    def test_rejects_sequence_below_one(self):
        with pytest.raises(ValueError, match="sequence must be >= 1"):
            AuditRecord(
                sequence=0,
                action="a",
                actor="a",
                resource="a",
                outcome="success",
                occurred_at=BASE_TIME,
                metadata={},
                previous_hash=GENESIS_HASH,
                record_hash="1" * 64,
            )

    @pytest.mark.parametrize(
        "field", ["action", "actor", "resource", "outcome"]
    )
    def test_rejects_empty_required_fields(self, field):
        kwargs = dict(
            sequence=1,
            action="a",
            actor="a",
            resource="a",
            outcome="success",
            occurred_at=BASE_TIME,
            metadata={},
            previous_hash=GENESIS_HASH,
            record_hash="1" * 64,
        )
        kwargs[field] = ""

        with pytest.raises(ValueError, match=f"{field} must not be empty"):
            AuditRecord(**kwargs)

    def test_rejects_naive_occurred_at(self):
        with pytest.raises(
            ValueError, match="occurred_at must be timezone-aware"
        ):
            AuditRecord(
                sequence=1,
                action="a",
                actor="a",
                resource="a",
                outcome="success",
                occurred_at=datetime(2026, 7, 21, 12, 0, 0),
                metadata={},
                previous_hash=GENESIS_HASH,
                record_hash="1" * 64,
            )

    def test_rejects_malformed_previous_hash(self):
        with pytest.raises(
            ValueError, match="previous_hash must be a 64-character"
        ):
            AuditRecord(
                sequence=1,
                action="a",
                actor="a",
                resource="a",
                outcome="success",
                occurred_at=BASE_TIME,
                metadata={},
                previous_hash="not-a-hash",
                record_hash="1" * 64,
            )

    def test_rejects_malformed_record_hash(self):
        with pytest.raises(
            ValueError, match="record_hash must be a 64-character"
        ):
            AuditRecord(
                sequence=1,
                action="a",
                actor="a",
                resource="a",
                outcome="success",
                occurred_at=BASE_TIME,
                metadata={},
                previous_hash=GENESIS_HASH,
                record_hash="not-a-hash",
            )

    def test_metadata_is_immutable(self):
        record = AuditRecord(
            sequence=1,
            action="a",
            actor="a",
            resource="a",
            outcome="success",
            occurred_at=BASE_TIME,
            metadata={"x": 1},
            previous_hash=GENESIS_HASH,
            record_hash="1" * 64,
        )

        with pytest.raises(TypeError):
            record.metadata["x"] = 2

    def test_to_dict(self):
        record = AuditRecord(
            sequence=1,
            action="a",
            actor="b",
            resource="c",
            outcome="success",
            occurred_at=BASE_TIME,
            metadata={"x": 1},
            previous_hash=GENESIS_HASH,
            record_hash="1" * 64,
        )

        assert record.to_dict() == {
            "sequence": 1,
            "action": "a",
            "actor": "b",
            "resource": "c",
            "outcome": "success",
            "occurred_at": BASE_TIME.isoformat(),
            "metadata": {"x": 1},
            "previous_hash": GENESIS_HASH,
            "record_hash": "1" * 64,
        }


class TestAuditQuery:

    def test_rejects_non_positive_limit(self):
        with pytest.raises(ValueError, match="limit must be greater than zero"):
            AuditQuery(limit=0)


class TestAuditChainVerification:

    def test_rejects_valid_with_broken_sequence(self):
        with pytest.raises(
            ValueError,
            match="first_broken_sequence and reason must not be set",
        ):
            AuditChainVerification(
                valid=True, checked=1, first_broken_sequence=1, reason=None
            )

    def test_rejects_invalid_without_reason(self):
        with pytest.raises(
            ValueError,
            match="first_broken_sequence and reason must be set",
        ):
            AuditChainVerification(
                valid=False, checked=1, first_broken_sequence=None, reason=None
            )

    def test_to_dict(self):
        result = AuditChainVerification(
            valid=False, checked=2, first_broken_sequence=2, reason="boom"
        )

        assert result.to_dict() == {
            "valid": False,
            "checked": 2,
            "first_broken_sequence": 2,
            "reason": "boom",
        }


# --- Append record / sequence numbering ---------------------------------


class TestGovernanceAuditServiceRecord:

    def test_record_returns_audit_record(self):
        service = GovernanceAuditService(clock=_clock)

        record = service.record(
            action="lifecycle_start", actor="system", resource="x", outcome="success"
        )

        assert record.action == "lifecycle_start"
        assert record.sequence == 1

    def test_sequence_numbers_increase_monotonically(self):
        service = GovernanceAuditService(clock=_clock)

        first = service.record(action="a", actor="x", resource="r", outcome="success")
        second = service.record(action="b", actor="x", resource="r", outcome="success")

        assert (first.sequence, second.sequence) == (1, 2)

    def test_first_record_chains_from_genesis(self):
        service = GovernanceAuditService(clock=_clock)

        record = service.record(action="a", actor="x", resource="r", outcome="success")

        assert record.previous_hash == GENESIS_HASH

    def test_sequence_is_never_reused_after_purge(self):
        service = GovernanceAuditService(clock=_clock)
        service.record(action="a", actor="x", resource="r", outcome="success")
        service.purge()

        record = service.record(action="b", actor="x", resource="r", outcome="success")

        assert record.sequence == 2

    def test_size_reflects_recorded_count(self):
        service = GovernanceAuditService(clock=_clock)
        service.record(action="a", actor="x", resource="r", outcome="success")
        service.record(action="b", actor="x", resource="r", outcome="success")

        assert service.size() == 2

    def test_get_returns_record_by_sequence(self):
        service = GovernanceAuditService(clock=_clock)
        record = service.record(action="a", actor="x", resource="r", outcome="success")

        assert service.get(record.sequence) is record

    def test_get_unknown_sequence_raises(self):
        service = GovernanceAuditService()

        with pytest.raises(LookupError):
            service.get(999)


# --- Hash chain generation -------------------------------------------


class TestHashChainGeneration:

    def test_each_record_chains_onto_the_previous_hash(self):
        service = GovernanceAuditService(clock=_clock)

        first = service.record(action="a", actor="x", resource="r", outcome="success")
        second = service.record(action="b", actor="x", resource="r", outcome="success")

        assert second.previous_hash == first.record_hash

    def test_record_hash_is_deterministic_for_identical_fields(self):
        service_a = GovernanceAuditService(clock=_clock)
        service_b = GovernanceAuditService(clock=_clock)

        record_a = service_a.record(
            action="a", actor="x", resource="r", outcome="success", metadata={"k": 1}
        )
        record_b = service_b.record(
            action="a", actor="x", resource="r", outcome="success", metadata={"k": 1}
        )

        assert record_a.record_hash == record_b.record_hash

    def test_record_hash_changes_with_metadata_key_order(self):
        # A pure JSON round-trip through the same dict regardless of
        # construction order must hash identically: sort_keys is what
        # guarantees "deterministic serialization" here.
        service = GovernanceAuditService(clock=_clock)

        record_a = service.record(
            action="a",
            actor="x",
            resource="r",
            outcome="success",
            metadata={"a": 1, "b": 2},
        )

        service_2 = GovernanceAuditService(clock=_clock)
        record_b = service_2.record(
            action="a",
            actor="x",
            resource="r",
            outcome="success",
            metadata={"b": 2, "a": 1},
        )

        assert record_a.record_hash == record_b.record_hash

    def test_record_hash_is_64_char_hex(self):
        service = GovernanceAuditService(clock=_clock)
        record = service.record(action="a", actor="x", resource="r", outcome="success")

        assert len(record.record_hash) == 64
        int(record.record_hash, 16)  # does not raise


# --- Chain verification -------------------------------------------------


class TestVerifyChain:

    def test_untampered_chain_is_valid(self):
        service = GovernanceAuditService(clock=_clock)
        service.record(action="a", actor="x", resource="r", outcome="success")
        service.record(action="b", actor="x", resource="r", outcome="success")

        result = service.verify_chain()

        assert result.valid is True
        assert result.first_broken_sequence is None

    def test_empty_chain_is_valid(self):
        service = GovernanceAuditService(clock=_clock)

        result = service.verify_chain()

        assert result.valid is True
        assert result.checked == 0

    def test_verify_chain_records_a_self_audit_entry(self):
        service = GovernanceAuditService(clock=_clock)
        service.record(action="a", actor="x", resource="r", outcome="success")

        size_before = service.size()
        service.verify_chain()

        assert service.size() == size_before + 1
        assert service.latest(1)[0].action == "audit_verification"


# --- Tamper detection ------------------------------------------------


class TestTamperDetection:

    def test_modified_record_field_is_detected(self):
        service = GovernanceAuditService(clock=_clock)
        service.record(action="a", actor="x", resource="r", outcome="success")
        record = service.record(
            action="b", actor="x", resource="r", outcome="success"
        )

        # Simulate an attacker directly rewriting stored data: change
        # a field without recomputing the hash.
        tampered = dataclasses.replace(record, outcome="tampered")
        service._records[record.sequence] = tampered

        result = service.verify_chain()

        assert result.valid is False
        assert result.first_broken_sequence == record.sequence

    def test_verification_reports_the_first_broken_record(self):
        service = GovernanceAuditService(clock=_clock)
        service.record(action="a", actor="x", resource="r", outcome="success")
        second = service.record(action="b", actor="x", resource="r", outcome="success")
        service.record(action="c", actor="x", resource="r", outcome="success")

        tampered = dataclasses.replace(second, outcome="tampered")
        service._records[second.sequence] = tampered

        result = service.verify_chain()

        assert result.first_broken_sequence == second.sequence

    def test_rewritten_previous_hash_is_also_detected(self):
        # Tampering with an earlier record and patching this record's
        # previous_hash to match is exactly what an attacker would try;
        # verify_chain recomputes expected_previous from the actual
        # preceding record's hash, not the stored previous_hash.
        service = GovernanceAuditService(clock=_clock)
        first = service.record(action="a", actor="x", resource="r", outcome="success")
        second = service.record(action="b", actor="x", resource="r", outcome="success")

        tampered_first = dataclasses.replace(first, outcome="tampered")
        # Recompute a hash for the tampered first record so its own
        # internal consistency looks fine in isolation...
        from backend.observability.deployment_governance_audit import (
            _compute_record_hash,
        )

        new_first_hash = _compute_record_hash(
            sequence=tampered_first.sequence,
            action=tampered_first.action,
            actor=tampered_first.actor,
            resource=tampered_first.resource,
            outcome=tampered_first.outcome,
            occurred_at=tampered_first.occurred_at,
            metadata=tampered_first.metadata,
            previous_hash=tampered_first.previous_hash,
        )
        tampered_first = dataclasses.replace(
            tampered_first, record_hash=new_first_hash
        )
        service._records[first.sequence] = tampered_first
        # ...but the second record's previous_hash still points at the
        # ORIGINAL first hash, which no longer matches.

        result = service.verify_chain()

        assert result.valid is False
        assert result.first_broken_sequence == second.sequence


# --- Filtering -------------------------------------------------------------


class TestGovernanceAuditServiceFiltering:

    def _service_with_mixed_records(self):
        service = GovernanceAuditService(clock=_clock)
        service.record(action="lifecycle_start", actor="system", resource="lifecycle_manager", outcome="success")
        service.record(action="route_create", actor="admin", resource="route:x", outcome="success")
        service.record(action="lifecycle_stop", actor="system", resource="lifecycle_manager", outcome="success")
        return service

    def test_filter_by_action(self):
        service = self._service_with_mixed_records()

        results = service.query(AuditQuery(action="route_create"))

        assert len(results) == 1
        assert results[0].action == "route_create"

    def test_filter_by_actor(self):
        service = self._service_with_mixed_records()

        results = service.query(AuditQuery(actor="admin"))

        assert len(results) == 1

    def test_filter_by_resource(self):
        service = self._service_with_mixed_records()

        results = service.query(AuditQuery(resource="lifecycle_manager"))

        assert len(results) == 2

    def test_query_respects_limit(self):
        service = self._service_with_mixed_records()

        assert len(service.query(AuditQuery(limit=1))) == 1

    def test_no_filters_returns_everything(self):
        service = self._service_with_mixed_records()

        assert len(service.query()) == 3


# --- Latest retrieval ------------------------------------------------------


class TestLatest:

    def test_latest_is_newest_first(self):
        service = GovernanceAuditService(clock=_clock)
        service.record(action="a", actor="x", resource="r", outcome="success")
        service.record(action="b", actor="x", resource="r", outcome="success")

        results = service.latest()

        assert [r.action for r in results] == ["b", "a"]

    def test_latest_respects_limit(self):
        service = GovernanceAuditService(clock=_clock)
        for i in range(5):
            service.record(action=str(i), actor="x", resource="r", outcome="success")

        assert len(service.latest(limit=2)) == 2


# --- Purge -------------------------------------------------------------


def test_chain_is_still_valid_after_purge():
    # The oldest surviving record's previous_hash legitimately points
    # at a now-purged predecessor, not genesis: verify_chain must not
    # mistake that for tampering.
    service = GovernanceAuditService(clock=_clock)
    service.record(action="a", actor="x", resource="r", outcome="success")
    service.purge()
    service.record(action="b", actor="x", resource="r", outcome="success")

    result = service.verify_chain()

    assert result.valid is True


def test_purge_removes_everything_but_preserves_chain_continuity():
    service = GovernanceAuditService(clock=_clock)
    first = service.record(action="a", actor="x", resource="r", outcome="success")

    purged = service.purge()

    assert purged == 1
    assert service.size() == 0

    second = service.record(action="b", actor="x", resource="r", outcome="success")

    assert second.previous_hash == first.record_hash
    assert second.sequence == 2


# --- Runtime integration -------------------------------------------------


class TestLifecycleAuditIntegration:

    def test_startup_records_lifecycle_start(self):
        from backend.observability.deployment_governance_lifecycle import (
            GovernanceLifecycleManager,
        )

        audit_service = GovernanceAuditService(clock=_clock)
        manager = GovernanceLifecycleManager(audit_service=audit_service)
        manager.register("a", start=lambda: None, stop=lambda: None)

        manager.startup()

        records = audit_service.query(AuditQuery(action="lifecycle_start"))
        assert len(records) == 1
        assert records[0].resource == "lifecycle_manager"

    def test_shutdown_records_lifecycle_stop(self):
        from backend.observability.deployment_governance_lifecycle import (
            GovernanceLifecycleManager,
        )

        audit_service = GovernanceAuditService(clock=_clock)
        manager = GovernanceLifecycleManager(audit_service=audit_service)
        manager.register("a", start=lambda: None, stop=lambda: None)

        manager.startup()
        manager.shutdown()

        records = audit_service.query(AuditQuery(action="lifecycle_stop"))
        assert len(records) == 1

    def test_restart_records_lifecycle_restart(self):
        from backend.observability.deployment_governance_lifecycle import (
            GovernanceLifecycleManager,
        )

        audit_service = GovernanceAuditService(clock=_clock)
        manager = GovernanceLifecycleManager(audit_service=audit_service)
        manager.register("a", start=lambda: None, stop=lambda: None)

        manager.startup()
        manager.restart()

        assert len(audit_service.query(AuditQuery(action="lifecycle_restart"))) == 1

    def test_reload_records_configuration_reload(self):
        from backend.observability.deployment_governance_lifecycle import (
            GovernanceLifecycleManager,
        )

        audit_service = GovernanceAuditService(clock=_clock)
        manager = GovernanceLifecycleManager(audit_service=audit_service)
        manager.register(
            "a", start=lambda: None, stop=lambda: None, reload=lambda: None
        )

        manager.startup()
        manager.reload()

        assert (
            len(audit_service.query(AuditQuery(action="configuration_reload")))
            == 1
        )

    def test_failed_startup_is_recorded_as_failure_outcome(self):
        from backend.observability.deployment_governance_lifecycle import (
            GovernanceLifecycleManager,
        )

        audit_service = GovernanceAuditService(clock=_clock)
        manager = GovernanceLifecycleManager(audit_service=audit_service)
        manager.register(
            "a",
            start=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
            stop=lambda: None,
        )

        manager.startup()

        record = audit_service.query(AuditQuery(action="lifecycle_start"))[0]
        assert record.outcome == "failure"


class TestEventRouterAuditIntegration:

    def test_register_route_records_route_create(self):
        from backend.observability.deployment_governance_event_router import (
            GovernanceEventRouter,
        )

        audit_service = GovernanceAuditService(clock=_clock)
        router = GovernanceEventRouter(audit_service=audit_service)

        router.register_route("a")

        records = audit_service.query(AuditQuery(action="route_create"))
        assert len(records) == 1
        assert records[0].resource == "route:a"

    def test_disable_route_records_route_update(self):
        from backend.observability.deployment_governance_event_router import (
            GovernanceEventRouter,
        )

        audit_service = GovernanceAuditService(clock=_clock)
        router = GovernanceEventRouter(audit_service=audit_service)
        router.register_route("a")

        router.disable_route("a")

        assert len(audit_service.query(AuditQuery(action="route_update"))) == 1

    def test_remove_route_records_route_delete(self):
        from backend.observability.deployment_governance_event_router import (
            GovernanceEventRouter,
        )

        audit_service = GovernanceAuditService(clock=_clock)
        router = GovernanceEventRouter(audit_service=audit_service)
        router.register_route("a")

        router.remove_route("a")

        assert len(audit_service.query(AuditQuery(action="route_delete"))) == 1


class TestEventHistoryAuditIntegration:

    def test_replay_records_event_replay(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )
        from backend.observability.deployment_governance_event_history import (
            GovernanceEventHistory,
        )

        audit_service = GovernanceAuditService(clock=_clock)
        history = GovernanceEventHistory(
            clock=_clock, audit_service=audit_service
        )
        bus = GovernanceEventBus()
        bus.subscribe_all(history._handle_bus_event)
        bus.publish("component_started", source="a")

        history.replay(None, bus)

        records = audit_service.query(AuditQuery(action="event_replay"))
        assert len(records) == 1
        assert records[0].metadata["count"] == 1


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceAuditApi:

    def test_audit_endpoint_returns_recorded_actions(self, client) -> None:
        client.post("/governance/lifecycle/start")

        response = client.get("/governance/audit")

        assert response.status_code == 200

        payload = response.json()

        assert any(r["action"] == "lifecycle_start" for r in payload)

    def test_audit_endpoint_filters_by_action(self, client) -> None:
        client.post("/governance/lifecycle/start")
        client.post("/governance/lifecycle/stop")

        response = client.get("/governance/audit?action=lifecycle_stop")

        payload = response.json()

        assert len(payload) == 1
        assert payload[0]["action"] == "lifecycle_stop"

    def test_latest_endpoint_respects_limit(self, client) -> None:
        client.post("/governance/lifecycle/start")
        client.post("/governance/lifecycle/stop")
        client.post("/governance/lifecycle/start")

        response = client.get("/governance/audit/latest?limit=1")

        assert response.status_code == 200
        assert len(response.json()) == 1

    def test_verify_endpoint_reports_valid_chain(self, client) -> None:
        client.post("/governance/lifecycle/start")

        response = client.get("/governance/audit/verify")

        assert response.status_code == 200

        payload = response.json()

        assert payload["valid"] is True
        assert payload["first_broken_sequence"] is None
