from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.governance.deployment_alerts import Alert, DeploymentAlertManager
from backend.governance.deployment_metrics import DeploymentMetricsCollector
from backend.governance.deployment_notifications import (
    ConsoleChannel,
    DeploymentNotificationService,
    EmailChannel,
    NotificationChannel,
    UnknownChannelError,
    UnknownNotificationError,
    router as deployment_notifications_router,
)

BASE_TIME = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)


class _FlakyChannel(NotificationChannel):
    def __init__(self, fail_times: int) -> None:
        super().__init__("flaky")
        self._fail_times = fail_times
        self._calls = 0
        self.delivered: list[dict] = []

    def deliver(self, payload):
        self._calls += 1
        if self._calls <= self._fail_times:
            raise RuntimeError("transient failure")
        self.delivered.append(dict(payload))


class _AlwaysFailsChannel(NotificationChannel):
    def __init__(self) -> None:
        super().__init__("always_fails")
        self.calls = 0

    def deliver(self, payload):
        self.calls += 1
        raise RuntimeError("permanent failure")


def _alert(alert_id: str = "alert-1") -> Alert:
    return Alert(
        alert_id=alert_id,
        rule_name="deployment_failure",
        level="ERROR",
        message="boom",
        value=1.0,
        threshold=1.0,
        triggered_at=BASE_TIME,
    )


def _no_sleep(_seconds: float) -> None:
    return None


@pytest.fixture
def service() -> DeploymentNotificationService:
    return DeploymentNotificationService(channels=[])


def test_register_channel_makes_it_available(
    service: DeploymentNotificationService,
):
    channel = ConsoleChannel()
    service.register_channel(channel)

    assert service.get_channel("console") is channel
    assert {c["name"] for c in service.list_channels()} == {"console"}


def test_notify_delivers_successfully_to_enabled_channels(
    service: DeploymentNotificationService,
):
    channel = ConsoleChannel()
    service.register_channel(channel)

    [record] = service.notify(_alert(), timestamp=BASE_TIME, sleep=_no_sleep)

    assert record.status == "SUCCESS"
    assert record.attempts == 1
    assert record.channel == "console"
    assert channel.delivered == [record.payload]


def test_notify_skips_disabled_channels(service: DeploymentNotificationService):
    service.register_channel(EmailChannel(enabled=False))
    service.register_channel(ConsoleChannel())

    records = service.notify(_alert(), timestamp=BASE_TIME, sleep=_no_sleep)

    assert [r.channel for r in records] == ["console"]


def test_notify_targets_explicit_channel_list(
    service: DeploymentNotificationService,
):
    service.register_channel(ConsoleChannel())
    service.register_channel(EmailChannel())

    records = service.notify(
        _alert(), channels=["email"], timestamp=BASE_TIME, sleep=_no_sleep
    )

    assert [r.channel for r in records] == ["email"]


def test_notify_unknown_channel_raises(service: DeploymentNotificationService):
    with pytest.raises(UnknownChannelError):
        service.notify(_alert(), channels=["nonexistent"], sleep=_no_sleep)


def test_notify_retries_with_backoff_until_success(
    service: DeploymentNotificationService,
):
    channel = _FlakyChannel(fail_times=2)
    service.register_channel(channel)
    sleeps: list[float] = []

    [record] = service.notify(
        _alert(),
        max_attempts=3,
        backoff_seconds=0.01,
        sleep=sleeps.append,
        timestamp=BASE_TIME,
    )

    assert record.status == "SUCCESS"
    assert record.attempts == 3
    assert sleeps == [0.01, 0.02]


def test_notify_exhausts_retries_and_records_failure(
    service: DeploymentNotificationService,
):
    channel = _AlwaysFailsChannel()
    service.register_channel(channel)

    [record] = service.notify(
        _alert(), max_attempts=3, sleep=_no_sleep, timestamp=BASE_TIME
    )

    assert record.status == "FAILED"
    assert record.attempts == 3
    assert channel.calls == 3
    assert "permanent failure" in record.error


def test_retry_resends_a_previously_failed_notification(
    service: DeploymentNotificationService,
):
    channel = _FlakyChannel(fail_times=1)
    service.register_channel(channel)

    [failed] = service.notify(
        _alert(), max_attempts=1, sleep=_no_sleep, timestamp=BASE_TIME
    )
    assert failed.status == "FAILED"

    retried = service.retry(
        failed.notification_id, max_attempts=1, sleep=_no_sleep, timestamp=BASE_TIME
    )

    assert retried.status == "SUCCESS"
    assert retried.notification_id != failed.notification_id


def test_retry_unknown_notification_raises(
    service: DeploymentNotificationService,
):
    with pytest.raises(UnknownNotificationError):
        service.retry("does-not-exist", sleep=_no_sleep)


def test_history_returns_all_records_and_filters_by_channel(
    service: DeploymentNotificationService,
):
    service.register_channel(ConsoleChannel())
    service.register_channel(EmailChannel())

    service.notify(_alert("a"), timestamp=BASE_TIME, sleep=_no_sleep)
    service.notify(_alert("b"), timestamp=BASE_TIME, sleep=_no_sleep)

    assert len(service.history()) == 4
    assert len(service.history(channel="console")) == 2
    assert len(service.history(channel="email")) == 2


def test_alert_manager_evaluate_notifies_on_trigger():
    manager = DeploymentAlertManager(rules=[])
    from backend.governance.deployment_alerts import AlertRule
    from backend.governance.deployment_metrics import FAILURE_COUNT

    manager.register_rule(
        AlertRule(
            name="deployment_failure",
            level="ERROR",
            threshold=1,
            comparator="gte",
            metric=FAILURE_COUNT,
        )
    )
    collector = DeploymentMetricsCollector()
    collector.increment(FAILURE_COUNT, amount=1)
    notification_service = DeploymentNotificationService(
        channels=[ConsoleChannel()]
    )

    manager.evaluate(
        collector.snapshot(),
        timestamp=BASE_TIME,
        notification_service=notification_service,
    )

    history = notification_service.history()
    assert len(history) == 1
    assert history[0].alert_id


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(deployment_notifications_router)
    return TestClient(app)


def test_api_send_notification(client: TestClient):
    from backend.governance.deployment_notifications import (
        get_deployment_notification_service,
    )

    get_deployment_notification_service()

    response = client.post(
        "/governance/notifications/send",
        json={"alert_id": "alert-api-1", "channels": ["console"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body[0]["channel"] == "console"
    assert body[0]["alert_id"] == "alert-api-1"


def test_api_send_notification_requires_alert_id(client: TestClient):
    response = client.post("/governance/notifications/send", json={})

    assert response.status_code == 422


def test_api_history_and_channels(client: TestClient):
    client.post(
        "/governance/notifications/send",
        json={"alert_id": "alert-api-2", "channels": ["email"]},
    )

    history_response = client.get(
        "/governance/notifications/history", params={"channel": "email"}
    )
    channels_response = client.get("/governance/notifications/channels")

    assert history_response.status_code == 200
    assert any(
        r["alert_id"] == "alert-api-2" for r in history_response.json()
    )
    assert channels_response.status_code == 200
    names = {c["name"] for c in channels_response.json()}
    assert {"email", "webhook", "slack", "console"} <= names
