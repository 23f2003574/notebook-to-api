import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.pipeline.dashboard import (
    PipelineDashboardAPI,
    get_pipeline_dashboard_api,
    router as dashboard_router,
)
from backend.pipeline.data_sources import ConnectionProfile, DataSourceManager, SourceType
from backend.pipeline.etl_engine import ETLWorkflowEngine
from backend.pipeline.pipeline_analytics import PipelineAnalyticsService
from backend.pipeline.pipeline_executor import PipelineExecutionEngine
from backend.pipeline.pipeline_scheduler import PipelineScheduler, ScheduleTrigger, TriggerType
from backend.pipeline.schema_registry import SchemaRegistry
from backend.pipeline.transformation_engine import DataTransformationEngine

ROWS = [{"region": "east", "amount": 10}, {"region": "west", "amount": 5}]


@pytest.fixture
def sources() -> DataSourceManager:
    manager = DataSourceManager()
    manager.register("orders-db", ConnectionProfile(source_type=SourceType.SQL, uri="postgres://db/orders"))
    return manager


@pytest.fixture
def workflows(sources: DataSourceManager) -> ETLWorkflowEngine:
    engine = ETLWorkflowEngine()
    engine.register_workflow("orders-etl", "orders-db", [], "warehouse.orders")
    return engine


@pytest.fixture
def executor() -> PipelineExecutionEngine:
    return PipelineExecutionEngine()


@pytest.fixture
def scheduler() -> PipelineScheduler:
    return PipelineScheduler()


@pytest.fixture
def analytics() -> PipelineAnalyticsService:
    return PipelineAnalyticsService()


@pytest.fixture
def schemas() -> SchemaRegistry:
    return SchemaRegistry()


@pytest.fixture
def dashboard(
    executor: PipelineExecutionEngine,
    scheduler: PipelineScheduler,
    analytics: PipelineAnalyticsService,
    schemas: SchemaRegistry,
) -> PipelineDashboardAPI:
    return PipelineDashboardAPI(executor, scheduler, analytics, schemas)


@pytest.fixture
def client(dashboard: PipelineDashboardAPI) -> TestClient:
    app = FastAPI()
    app.include_router(dashboard_router)
    app.dependency_overrides[get_pipeline_dashboard_api] = lambda: dashboard
    return TestClient(app)


def test_executions_section_empty_by_default(dashboard: PipelineDashboardAPI):
    section = dashboard.executions()

    assert section["total_runs"] == 0
    assert section["by_state"] == {}
    assert section["recent_runs"] == []
    assert "generated_at" in section


def test_executions_section_counts_by_state(
    dashboard: PipelineDashboardAPI, executor: PipelineExecutionEngine, workflows, sources
):
    run = executor.submit("orders-etl", ROWS)
    executor.execute(run.run_id, workflows=workflows, sources=sources, transformation_engine=DataTransformationEngine())
    executor.submit("orders-etl", ROWS)  # left queued

    section = dashboard.executions()

    assert section["total_runs"] == 2
    assert section["by_state"] == {"succeeded": 1, "queued": 1}
    assert len(section["recent_runs"]) == 2


def test_schedules_section_empty_by_default(dashboard: PipelineDashboardAPI):
    section = dashboard.schedules()

    assert section["total_schedules"] == 0
    assert section["active_count"] == 0
    assert section["upcoming"] == []


def test_schedules_section_reports_active_and_upcoming(
    dashboard: PipelineDashboardAPI, scheduler: PipelineScheduler, workflows
):
    scheduler.schedule("orders-etl", ScheduleTrigger(trigger_type=TriggerType.INTERVAL, interval_seconds=60), workflows=workflows)
    manual = scheduler.schedule("orders-etl", ScheduleTrigger(trigger_type=TriggerType.MANUAL), workflows=workflows)
    to_cancel = scheduler.schedule("orders-etl", ScheduleTrigger(trigger_type=TriggerType.INTERVAL, interval_seconds=120), workflows=workflows)
    scheduler.cancel(to_cancel.schedule_id)

    section = dashboard.schedules()

    assert section["total_schedules"] == 3
    assert section["active_count"] == 2
    upcoming_ids = {entry["schedule_id"] for entry in section["upcoming"]}
    assert manual.schedule_id not in upcoming_ids
    assert to_cancel.schedule_id not in upcoming_ids


def test_analytics_section_includes_summary_and_recent_activity(
    dashboard: PipelineDashboardAPI, analytics: PipelineAnalyticsService
):
    analytics.record("orders-etl", "success", 100.0, 10)
    analytics.record("orders-etl", "failed", 50.0, 0)

    section = dashboard.analytics()

    assert section["execution_count"] == 2
    assert len(section["recent_activity"]) == 2


def test_overview_combines_all_sections(
    dashboard: PipelineDashboardAPI,
    executor: PipelineExecutionEngine,
    workflows,
    sources,
    scheduler: PipelineScheduler,
    analytics: PipelineAnalyticsService,
    schemas: SchemaRegistry,
):
    run = executor.submit("orders-etl", ROWS)
    executor.execute(run.run_id, workflows=workflows, sources=sources, transformation_engine=DataTransformationEngine())
    scheduler.schedule("orders-etl", ScheduleTrigger(trigger_type=TriggerType.INTERVAL, interval_seconds=60), workflows=workflows)
    analytics.record("orders-etl", "success", 100.0, 10)
    schemas.register("orders", [{"name": "region", "type": "str", "nullable": False}])

    overview = dashboard.overview()

    assert set(overview.keys()) == {"executions", "schedules", "analytics", "validation", "generated_at"}
    assert overview["executions"]["total_runs"] == 1
    assert overview["schedules"]["total_schedules"] == 1
    assert overview["analytics"]["execution_count"] == 1
    assert overview["validation"]["schemas_registered"] == 1


def test_validation_section_counts_schema_versions(dashboard: PipelineDashboardAPI, schemas: SchemaRegistry):
    schemas.register("orders", [{"name": "region", "type": "str", "nullable": False}])
    schemas.update("orders", [{"name": "region", "type": "str", "nullable": False}, {"name": "currency", "type": "str", "nullable": True}])

    overview = dashboard.overview()

    assert overview["validation"]["schemas_registered"] == 1
    assert overview["validation"]["total_versions"] == 2


# --- API tests -------------------------------------------------------------


def test_api_overview(client: TestClient, analytics: PipelineAnalyticsService):
    analytics.record("orders-etl", "success", 100.0, 10)

    response = client.get("/pipelines/dashboard")

    assert response.status_code == 200
    assert response.json()["analytics"]["execution_count"] == 1


def test_api_executions_endpoint(client: TestClient, executor: PipelineExecutionEngine):
    executor.submit("orders-etl", ROWS)

    response = client.get("/pipelines/dashboard/executions")

    assert response.status_code == 200
    assert response.json()["total_runs"] == 1


def test_api_schedules_endpoint(client: TestClient, scheduler: PipelineScheduler, workflows):
    scheduler.schedule("orders-etl", ScheduleTrigger(trigger_type=TriggerType.INTERVAL, interval_seconds=60), workflows=workflows)

    response = client.get("/pipelines/dashboard/schedules")

    assert response.status_code == 200
    assert response.json()["total_schedules"] == 1


def test_api_analytics_endpoint(client: TestClient, analytics: PipelineAnalyticsService):
    analytics.record("orders-etl", "success", 100.0, 10)

    response = client.get("/pipelines/dashboard/analytics")

    assert response.status_code == 200
    assert response.json()["execution_count"] == 1
