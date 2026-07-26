from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.governance.deployment_automation import (
    AutomationRule,
    DeploymentAutomationEngine,
    TriggerCondition,
    UnknownRuleError,
    router as deployment_automation_router,
)
from backend.governance.deployment_scheduler import DeploymentScheduler

BASE_TIME = datetime(2026, 7, 26, 9, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def engine() -> DeploymentAutomationEngine:
    return DeploymentAutomationEngine()


def test_register_rule_creates_rule(engine: DeploymentAutomationEngine):
    rule = engine.register_rule("deploy-on-push", "svc-a", "git_push", timestamp=BASE_TIME)

    assert isinstance(rule, AutomationRule)
    assert rule.enabled is True
    assert rule.trigger_type == "git_push"


def test_register_rule_requires_name(engine: DeploymentAutomationEngine):
    with pytest.raises(ValueError):
        engine.register_rule("", "svc-a", "git_push")


def test_register_rule_requires_pipeline(engine: DeploymentAutomationEngine):
    with pytest.raises(ValueError):
        engine.register_rule("rule", "", "git_push")


def test_register_rule_rejects_unknown_trigger_type(engine: DeploymentAutomationEngine):
    with pytest.raises(ValueError):
        engine.register_rule("rule", "svc-a", "carrier_pigeon")


def test_condition_requires_field():
    with pytest.raises(ValueError):
        TriggerCondition(field="")


def test_condition_rejects_unknown_operator():
    with pytest.raises(ValueError):
        TriggerCondition(field="branch", operator="matches_regex")


def test_condition_matches_equals():
    condition = TriggerCondition(field="branch", operator="equals", value="main")

    assert condition.matches({"branch": "main"}) is True
    assert condition.matches({"branch": "dev"}) is False


def test_condition_matches_not_equals():
    condition = TriggerCondition(field="branch", operator="not_equals", value="main")

    assert condition.matches({"branch": "dev"}) is True
    assert condition.matches({"branch": "main"}) is False


def test_condition_matches_in():
    condition = TriggerCondition(field="branch", operator="in", value=("main", "release"))

    assert condition.matches({"branch": "release"}) is True
    assert condition.matches({"branch": "dev"}) is False


def test_condition_matches_contains():
    condition = TriggerCondition(field="labels", operator="contains", value="deploy")

    assert condition.matches({"labels": ["deploy", "urgent"]}) is True
    assert condition.matches({"labels": ["urgent"]}) is False


def test_remove_rule_deletes(engine: DeploymentAutomationEngine):
    rule = engine.register_rule("deploy-on-push", "svc-a", "git_push")

    engine.remove_rule(rule.rule_id)

    assert engine.list_rules() == ()


def test_remove_rule_unknown_raises(engine: DeploymentAutomationEngine):
    with pytest.raises(UnknownRuleError):
        engine.remove_rule("does-not-exist")


def test_list_rules_orders_by_priority(engine: DeploymentAutomationEngine):
    low = engine.register_rule("low", "svc-a", "manual", priority=0)
    high = engine.register_rule("high", "svc-a", "manual", priority=10)

    ordered = [rule.rule_id for rule in engine.list_rules()]

    assert ordered == [high.rule_id, low.rule_id]


def test_evaluate_returns_matching_enabled_rules(engine: DeploymentAutomationEngine):
    rule = engine.register_rule(
        "deploy-on-main-push",
        "svc-a",
        "git_push",
        conditions=[TriggerCondition(field="branch", value="main")],
    )

    matched = engine.evaluate("git_push", {"branch": "main"})

    assert matched == (rule,)


def test_evaluate_excludes_disabled_rules(engine: DeploymentAutomationEngine):
    engine.register_rule("deploy-on-push", "svc-a", "git_push", enabled=False)

    assert engine.evaluate("git_push", {}) == ()


def test_evaluate_excludes_wrong_trigger_type(engine: DeploymentAutomationEngine):
    engine.register_rule("deploy-on-release", "svc-a", "release_created")

    assert engine.evaluate("git_push", {}) == ()


def test_evaluate_requires_all_conditions_to_match(engine: DeploymentAutomationEngine):
    engine.register_rule(
        "deploy-on-main-push",
        "svc-a",
        "git_push",
        conditions=[
            TriggerCondition(field="branch", value="main"),
            TriggerCondition(field="verified", value=True),
        ],
    )

    assert engine.evaluate("git_push", {"branch": "main", "verified": False}) == ()
    assert len(engine.evaluate("git_push", {"branch": "main", "verified": True})) == 1


def test_evaluate_unknown_trigger_type_raises(engine: DeploymentAutomationEngine):
    with pytest.raises(ValueError):
        engine.evaluate("carrier_pigeon", {})


def test_trigger_dispatches_matching_rules_via_scheduler(engine: DeploymentAutomationEngine):
    scheduler = DeploymentScheduler()
    engine.register_rule(
        "deploy-on-main-push",
        "svc-a",
        "git_push",
        conditions=[TriggerCondition(field="branch", value="main")],
    )

    dispatched = engine.trigger(
        "git_push", {"branch": "main"}, scheduler=scheduler, timestamp=BASE_TIME
    )

    assert len(dispatched) == 1
    assert dispatched[0].pipeline == "svc-a"
    assert len(scheduler.pending()) == 1


def test_trigger_dispatches_in_priority_order(engine: DeploymentAutomationEngine):
    scheduler = DeploymentScheduler()
    engine.register_rule("low", "svc-low", "manual", priority=0)
    engine.register_rule("high", "svc-high", "manual", priority=10)

    dispatched = engine.trigger("manual", {}, scheduler=scheduler, timestamp=BASE_TIME)

    assert [deployment.pipeline for deployment in dispatched] == ["svc-high", "svc-low"]


def test_trigger_ignores_non_matching_rules(engine: DeploymentAutomationEngine):
    scheduler = DeploymentScheduler()
    engine.register_rule(
        "deploy-on-main-push",
        "svc-a",
        "git_push",
        conditions=[TriggerCondition(field="branch", value="main")],
    )

    dispatched = engine.trigger(
        "git_push", {"branch": "dev"}, scheduler=scheduler, timestamp=BASE_TIME
    )

    assert dispatched == ()


def test_trigger_requires_scheduler(engine: DeploymentAutomationEngine):
    engine.register_rule("rule", "svc-a", "manual")

    with pytest.raises(ValueError):
        engine.trigger("manual", {})


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(deployment_automation_router)
    return TestClient(app)


def test_api_create_and_list_rule(client: TestClient):
    create_response = client.post(
        "/governance/automation/rules",
        json={"name": "deploy-on-push-api-1", "pipeline": "svc-api-1", "trigger_type": "git_push"},
    )
    list_response = client.get("/governance/automation/rules")

    assert create_response.status_code == 200
    assert list_response.status_code == 200
    assert any(r["name"] == "deploy-on-push-api-1" for r in list_response.json())


def test_api_create_requires_fields(client: TestClient):
    response = client.post("/governance/automation/rules", json={"name": "rule"})

    assert response.status_code == 422


def test_api_create_rejects_unknown_trigger_type(client: TestClient):
    response = client.post(
        "/governance/automation/rules",
        json={"name": "rule", "pipeline": "svc-api-2", "trigger_type": "carrier_pigeon"},
    )

    assert response.status_code == 422


def test_api_evaluate_returns_matching_rules(client: TestClient):
    client.post(
        "/governance/automation/rules",
        json={
            "name": "deploy-on-main-push-api",
            "pipeline": "svc-api-3",
            "trigger_type": "git_push",
            "conditions": [{"field": "branch", "operator": "equals", "value": "main"}],
        },
    )

    response = client.post(
        "/governance/automation/evaluate",
        json={"trigger_type": "git_push", "payload": {"branch": "main"}},
    )

    assert response.status_code == 200
    assert any(r["name"] == "deploy-on-main-push-api" for r in response.json())


def test_api_evaluate_requires_trigger_type(client: TestClient):
    response = client.post("/governance/automation/evaluate", json={})

    assert response.status_code == 422


def test_api_evaluate_unknown_trigger_type_returns_422(client: TestClient):
    response = client.post(
        "/governance/automation/evaluate", json={"trigger_type": "carrier_pigeon"}
    )

    assert response.status_code == 422
