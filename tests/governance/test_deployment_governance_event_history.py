from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_event_bus import (
    GovernanceEvent,
    GovernanceEventBus,
)
from backend.observability.deployment_governance_event_history import (
    EventQuery,
    GovernanceEventHistory,
    StoredGovernanceEvent,
    get_event_history,
)

BASE_TIME = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


def _event(
    event_type="component_started",
    source="a",
    payload=None,
    occurred_at=BASE_TIME,
    event_id="1",
) -> GovernanceEvent:
    return GovernanceEvent(
        event_id=event_id,
        event_type=event_type,
        source=source,
        payload=payload or {},
        occurred_at=occurred_at,
    )


@pytest.fixture(autouse=True)
def _reset_singletons():
    """
    The event history and event bus are process-wide singletons, so
    tests that touch get_event_history()/get_event_bus() (directly or
    via the API endpoints) must not leak stored events or
    subscriptions into other tests.
    """

    from backend.observability.deployment_governance_event_bus import (
        get_event_bus,
    )

    get_event_history().purge()
    get_event_bus().clear()

    yield

    get_event_history().purge()
    get_event_bus().clear()


# --- Model -------------------------------------------------------------


def test_stored_event_rejects_sequence_below_one():
    with pytest.raises(ValueError, match="sequence must be >= 1"):
        StoredGovernanceEvent(sequence=0, event=_event())


def test_stored_event_to_dict():
    event = _event()
    stored = StoredGovernanceEvent(sequence=1, event=event)

    assert stored.to_dict() == {
        "sequence": 1,
        "event": event.to_dict(),
    }


class TestEventQuery:

    def test_rejects_naive_start_time(self):
        with pytest.raises(
            ValueError, match="start_time must be timezone-aware"
        ):
            EventQuery(start_time=datetime(2026, 7, 21, 12, 0, 0))

    def test_rejects_naive_end_time(self):
        with pytest.raises(
            ValueError, match="end_time must be timezone-aware"
        ):
            EventQuery(end_time=datetime(2026, 7, 21, 12, 0, 0))

    def test_rejects_non_positive_limit(self):
        with pytest.raises(ValueError, match="limit must be greater than zero"):
            EventQuery(limit=0)


# --- Append events / sequence numbering ---------------------------------


class TestGovernanceEventHistoryAppend:

    def test_append_returns_stored_event(self):
        history = GovernanceEventHistory()
        event = _event()

        stored = history.append(event)

        assert stored.event is event
        assert stored.sequence == 1

    def test_sequence_numbers_increase_monotonically(self):
        history = GovernanceEventHistory()

        first = history.append(_event(event_id="1"))
        second = history.append(_event(event_id="2"))
        third = history.append(_event(event_id="3"))

        assert (first.sequence, second.sequence, third.sequence) == (
            1,
            2,
            3,
        )

    def test_size_reflects_appended_count(self):
        history = GovernanceEventHistory()
        history.append(_event(event_id="1"))
        history.append(_event(event_id="2"))

        assert history.size() == 2

    def test_get_returns_stored_event_by_sequence(self):
        history = GovernanceEventHistory()
        stored = history.append(_event())

        assert history.get(stored.sequence) is stored

    def test_get_unknown_sequence_raises(self):
        history = GovernanceEventHistory()

        with pytest.raises(LookupError):
            history.get(999)

    def test_sequence_is_never_reused_after_purge(self):
        history = GovernanceEventHistory()
        history.append(_event(event_id="1"))
        history.purge()

        stored = history.append(_event(event_id="2"))

        assert stored.sequence == 2


# --- Filtering -------------------------------------------------------------


class TestGovernanceEventHistoryFiltering:

    def _history_with_mixed_events(self):
        history = GovernanceEventHistory()
        history.append(
            _event(
                event_type="component_started",
                source="a",
                occurred_at=BASE_TIME,
                event_id="1",
            )
        )
        history.append(
            _event(
                event_type="component_stopped",
                source="b",
                occurred_at=BASE_TIME + timedelta(seconds=10),
                event_id="2",
            )
        )
        history.append(
            _event(
                event_type="component_started",
                source="b",
                occurred_at=BASE_TIME + timedelta(seconds=20),
                event_id="3",
            )
        )
        return history

    def test_filter_by_event_type(self):
        history = self._history_with_mixed_events()

        results = history.query(EventQuery(event_type="component_started"))

        assert {r.event.event_id for r in results} == {"1", "3"}

    def test_filter_by_source(self):
        history = self._history_with_mixed_events()

        results = history.query(EventQuery(source="b"))

        assert {r.event.event_id for r in results} == {"2", "3"}

    def test_filter_by_time_range(self):
        history = self._history_with_mixed_events()

        results = history.query(
            EventQuery(
                start_time=BASE_TIME + timedelta(seconds=5),
                end_time=BASE_TIME + timedelta(seconds=15),
            )
        )

        assert {r.event.event_id for r in results} == {"2"}

    def test_combined_filters(self):
        history = self._history_with_mixed_events()

        results = history.query(
            EventQuery(event_type="component_started", source="b")
        )

        assert {r.event.event_id for r in results} == {"3"}

    def test_query_respects_limit(self):
        history = self._history_with_mixed_events()

        results = history.query(EventQuery(limit=1))

        assert len(results) == 1

    def test_no_filters_returns_everything(self):
        history = self._history_with_mixed_events()

        results = history.query()

        assert len(results) == 3


# --- Latest retrieval ------------------------------------------------------


class TestGovernanceEventHistoryLatest:

    def test_latest_is_newest_first(self):
        history = GovernanceEventHistory()
        history.append(_event(event_id="1"))
        history.append(_event(event_id="2"))

        results = history.latest()

        assert [r.event.event_id for r in results] == ["2", "1"]

    def test_latest_respects_limit(self):
        history = GovernanceEventHistory()
        for i in range(5):
            history.append(_event(event_id=str(i)))

        assert len(history.latest(limit=2)) == 2


# --- Replay ordering ------------------------------------------------------


class TestGovernanceEventHistoryReplay:

    def test_replay_dispatches_in_chronological_order(self):
        history = GovernanceEventHistory()
        history.append(
            _event(event_id="1", occurred_at=BASE_TIME)
        )
        history.append(
            _event(
                event_id="2", occurred_at=BASE_TIME + timedelta(seconds=1)
            )
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_id))

        history.replay(None, bus)

        assert received == ["1", "2"]

    def test_replay_returns_replayed_events_in_order(self):
        history = GovernanceEventHistory()
        history.append(_event(event_id="1"))
        history.append(
            _event(event_id="2", occurred_at=BASE_TIME + timedelta(seconds=1))
        )

        bus = GovernanceEventBus()

        replayed = history.replay(None, bus)

        assert [e.event_id for e in replayed] == ["1", "2"]

    def test_replay_respects_query_filters(self):
        history = GovernanceEventHistory()
        history.append(_event(event_type="component_started", event_id="1"))
        history.append(_event(event_type="component_stopped", event_id="2"))

        bus = GovernanceEventBus()

        replayed = history.replay(
            EventQuery(event_type="component_stopped"), bus
        )

        assert [e.event_id for e in replayed] == ["2"]

    def test_replay_does_not_modify_history(self):
        history = GovernanceEventHistory()
        history.append(_event(event_id="1"))

        bus = GovernanceEventBus()

        size_before = history.size()
        history.replay(None, bus)

        assert history.size() == size_before

    def test_replay_does_not_re_persist_via_auto_subscribed_bus(self):
        history = GovernanceEventHistory()
        bus = GovernanceEventBus()
        bus.subscribe_all(history._handle_bus_event)

        bus.publish("component_started", source="a")
        assert history.size() == 1

        history.replay(None, bus)

        assert history.size() == 1

    def test_replay_still_notifies_other_subscribers(self):
        history = GovernanceEventHistory()
        history.append(_event(event_id="1"))

        bus = GovernanceEventBus()
        bus.subscribe_all(history._handle_bus_event)

        received = []
        bus.subscribe_all(lambda e: received.append(e.event_id))

        history.replay(None, bus)

        assert received == ["1"]
        assert history.size() == 1


# --- Purge -------------------------------------------------------------


class TestGovernanceEventHistoryPurge:

    def test_purge_removes_everything(self):
        history = GovernanceEventHistory()
        history.append(_event(event_id="1"))
        history.append(_event(event_id="2"))

        history.purge()

        assert history.size() == 0

    def test_purge_returns_count_removed(self):
        history = GovernanceEventHistory()
        history.append(_event(event_id="1"))
        history.append(_event(event_id="2"))

        assert history.purge() == 2

    def test_purge_on_empty_history_returns_zero(self):
        history = GovernanceEventHistory()

        assert history.purge() == 0


# --- Retention enforcement -----------------------------------------------


class TestGovernanceEventHistoryRetention:

    def test_oldest_entries_evicted_once_max_entries_exceeded(self):
        history = GovernanceEventHistory(max_entries=2)
        history.append(_event(event_id="1"))
        history.append(_event(event_id="2"))
        history.append(_event(event_id="3"))

        assert history.size() == 2
        assert [
            r.event.event_id for r in history.query()
        ] == ["3", "2"]

    def test_unlimited_by_default(self):
        history = GovernanceEventHistory()

        for i in range(50):
            history.append(_event(event_id=str(i)))

        assert history.size() == 50


# --- Event bus wiring (wildcard subscribe + dispatch) -------------------


class TestEventBusWildcardAndDispatch:

    def test_subscribe_all_receives_every_event_type(self):
        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        bus.publish("component_started", source="a")
        bus.publish("component_stopped", source="a")

        assert received == ["component_started", "component_stopped"]

    def test_dispatch_notifies_specific_and_wildcard_subscribers(self):
        bus = GovernanceEventBus()
        specific = []
        wildcard = []
        bus.subscribe("component_started", lambda e: specific.append(e))
        bus.subscribe_all(lambda e: wildcard.append(e))

        event = GovernanceEvent(
            event_id="1",
            event_type="component_started",
            source="a",
            payload={},
            occurred_at=BASE_TIME,
        )

        bus.dispatch(event)

        assert specific == [event]
        assert wildcard == [event]


# --- Logging integration -------------------------------------------------


class TestEventBusLogHandler:

    def test_event_bus_log_handler_logs_every_event(self):
        from unittest.mock import Mock

        from backend.observability.deployment_governance_logging import (
            event_bus_log_handler,
        )

        logger = Mock()

        handler = event_bus_log_handler(logger)
        handler(_event(event_type="component_started", source="a"))

        logger.info.assert_called_once()
        args, kwargs = logger.info.call_args
        assert args[0] == "event_bus"
        assert args[1] == "component_started"
        assert kwargs["source"] == "a"


# --- Auto-persistence (singleton) -----------------------------------------


class TestEventHistorySingletonAutoPersistence:

    def test_published_events_are_automatically_persisted(self):
        from backend.observability.deployment_governance_event_bus import (
            get_event_bus,
        )

        history = get_event_history()

        get_event_bus().publish("component_started", source="x")

        assert history.size() == 1

    def test_get_event_history_returns_same_instance(self):
        assert get_event_history() is get_event_history()

    def test_rewires_after_bus_clear(self):
        from backend.observability.deployment_governance_event_bus import (
            get_event_bus,
        )

        get_event_history()
        get_event_bus().clear()

        # get_event_history() must detect the wildcard subscription
        # is gone and re-establish it, or this event is silently lost.
        history = get_event_history()
        get_event_bus().publish("component_started", source="x")

        assert history.size() == 1


# --- API endpoints -----------------------------------------------------


def _setup_sqlite_env(monkeypatch, tmp_path, name: str) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / name),
    )


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceEventHistoryApi:

    def test_events_endpoint_returns_persisted_events(
        self, client, monkeypatch, tmp_path
    ) -> None:
        _setup_sqlite_env(monkeypatch, tmp_path, "api-events.db")

        from backend.observability.deployment_governance_event_bus import (
            get_event_bus,
        )

        get_event_history()
        get_event_bus().publish("component_started", source="a")

        response = client.get("/governance/events")

        assert response.status_code == 200

        payload = response.json()

        assert len(payload) == 1
        assert payload[0]["event"]["source"] == "a"

    def test_events_endpoint_filters_by_event_type(
        self, client, monkeypatch, tmp_path
    ) -> None:
        _setup_sqlite_env(
            monkeypatch, tmp_path, "api-events-filter.db"
        )

        from backend.observability.deployment_governance_event_bus import (
            get_event_bus,
        )

        get_event_history()
        get_event_bus().publish("component_started", source="a")
        get_event_bus().publish("component_stopped", source="a")

        response = client.get(
            "/governance/events?event_type=component_stopped"
        )

        payload = response.json()

        assert len(payload) == 1
        assert payload[0]["event"]["event_type"] == "component_stopped"

    def test_latest_endpoint_respects_limit(
        self, client, monkeypatch, tmp_path
    ) -> None:
        _setup_sqlite_env(monkeypatch, tmp_path, "api-events-latest.db")

        from backend.observability.deployment_governance_event_bus import (
            get_event_bus,
        )

        get_event_history()

        for i in range(5):
            get_event_bus().publish("component_started", source=str(i))

        response = client.get("/governance/events/latest?limit=2")

        assert response.status_code == 200
        assert len(response.json()) == 2

    def test_replay_endpoint_replays_matching_events(
        self, client, monkeypatch, tmp_path
    ) -> None:
        _setup_sqlite_env(monkeypatch, tmp_path, "api-events-replay.db")

        from backend.observability.deployment_governance_event_bus import (
            get_event_bus,
        )

        get_event_history()
        get_event_bus().publish("component_started", source="a")

        response = client.post(
            "/governance/events/replay?event_type=component_started"
        )

        assert response.status_code == 200

        payload = response.json()

        assert len(payload) == 1
        assert payload[0]["source"] == "a"

        # Replaying must not add new stored entries.
        status_response = client.get("/governance/events")
        assert len(status_response.json()) == 1

    def test_delete_endpoint_purges_all_events(
        self, client, monkeypatch, tmp_path
    ) -> None:
        _setup_sqlite_env(monkeypatch, tmp_path, "api-events-delete.db")

        from backend.observability.deployment_governance_event_bus import (
            get_event_bus,
        )

        get_event_history()
        get_event_bus().publish("component_started", source="a")

        response = client.delete("/governance/events")

        assert response.status_code == 200
        assert response.json() == {"purged": 1}

        status_response = client.get("/governance/events")
        assert status_response.json() == []
