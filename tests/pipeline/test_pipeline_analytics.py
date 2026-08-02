import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.pipeline.data_sources import ConnectionProfile, DataSourceManager, SourceType, get_data_source_manager
from backend.pipeline.etl_engine import ETLWorkflowEngine, get_etl_workflow_engine
from backend.pipeline.pipeline_analytics import (
    PipelineAnalyticsService,
    PipelineMetrics,
    PipelineTrend,
    get_pipeline_analytics_service,
    router as pipeline_analytics_router,
)
from backend.pipeline.pipeline_executor import (
    PipelineExecutionEngine,
    get_pipeline_execution_engine,
    router as pipeline_executor_router,
)
from backend.pipeline.transformation_engine import DataTransformationEngine, get_data_transformation_engine

ROWS = [{"region": "east", "amount": 10}, {"region": "west", "amount": 5}]


@pytest.fixture
def service() -> PipelineAnalyticsService:
    return PipelineAnalyticsService()


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
def client(service, executor, workflows, sources) -> TestClient:
    app = FastAPI()
    app.include_router(pipeline_analytics_router)
    app.include_router(pipeline_executor_router)
    app.dependency_overrides[get_pipeline_analytics_service] = lambda: service
    app.dependency_overrides[get_pipeline_execution_engine] = lambda: executor
    app.dependency_overrides[get_etl_workflow_engine] = lambda: workflows
    app.dependency_overrides[get_data_source_manager] = lambda: sources
    app.dependency_overrides[get_data_transformation_engine] = lambda: DataTransformationEngine()
    return TestClient(app)


def test_record_stores_metric(service: PipelineAnalyticsService):
    metric = service.record("orders-etl", "success", 120.0, 10)

    assert isinstance(metric, PipelineMetrics)
    assert metric.workflow_name == "orders-etl"
    assert metric.status == "success"


def test_list_records_filters_by_workflow(service: PipelineAnalyticsService):
    service.record("orders-etl", "success", 100.0, 5)
    service.record("other-etl", "success", 100.0, 5)

    records = service.list_records("orders-etl")

    assert len(records) == 1
    assert records[0].workflow_name == "orders-etl"


def test_summary_computes_success_and_failure_rate(service: PipelineAnalyticsService):
    service.record("orders-etl", "success", 100.0, 10)
    service.record("orders-etl", "success", 200.0, 20)
    service.record("orders-etl", "failed", 50.0, 0)

    summary = service.summary("orders-etl")

    assert summary["execution_count"] == 3
    assert summary["success_rate"] == pytest.approx(2 / 3)
    assert summary["failure_rate"] == pytest.approx(1 / 3)


def test_summary_computes_average_runtime(service: PipelineAnalyticsService):
    service.record("orders-etl", "success", 100.0, 10)
    service.record("orders-etl", "success", 300.0, 10)

    summary = service.summary("orders-etl")

    assert summary["average_runtime_ms"] == pytest.approx(200.0)


def test_summary_computes_data_throughput(service: PipelineAnalyticsService):
    service.record("orders-etl", "success", 1000.0, 100)

    summary = service.summary("orders-etl")

    assert summary["data_throughput_rows_per_second"] == pytest.approx(100.0)


def test_summary_with_no_records_has_zero_rates(service: PipelineAnalyticsService):
    summary = service.summary("does-not-exist")

    assert summary["execution_count"] == 0
    assert summary["success_rate"] == 0.0
    assert summary["average_runtime_ms"] is None
    assert summary["data_throughput_rows_per_second"] is None


def test_trends_buckets_by_day(service: PipelineAnalyticsService):
    from datetime import datetime, timezone

    service.record("orders-etl", "success", 100.0, 10, recorded_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    service.record("orders-etl", "success", 200.0, 10, recorded_at=datetime(2026, 1, 1, 5, tzinfo=timezone.utc))
    service.record("orders-etl", "failed", 50.0, 0, recorded_at=datetime(2026, 1, 2, tzinfo=timezone.utc))

    trends = service.trends("orders-etl")

    assert isinstance(trends[0], PipelineTrend)
    assert [trend.execution_count for trend in trends] == [2, 1]
    assert trends[1].success_rate == 0.0


def test_trends_rejects_unsupported_bucket(service: PipelineAnalyticsService):
    with pytest.raises(ValueError):
        service.trends(bucket="month")


def test_export_json_includes_summary_trends_and_records(service: PipelineAnalyticsService):
    service.record("orders-etl", "success", 100.0, 10)

    export = service.export("orders-etl")

    assert "summary" in export
    assert "trends" in export
    assert "records" in export
    assert len(export["records"]) == 1


def test_export_csv_returns_string_with_header(service: PipelineAnalyticsService):
    service.record("orders-etl", "success", 100.0, 10)

    export = service.export("orders-etl", format="csv")

    assert isinstance(export, str)
    assert "workflow_name" in export.splitlines()[0]


def test_export_rejects_unsupported_format(service: PipelineAnalyticsService):
    with pytest.raises(ValueError):
        service.export(format="xml")


def test_executor_execute_records_metric_when_analytics_provided(
    service: PipelineAnalyticsService, executor: PipelineExecutionEngine, workflows, sources
):
    run = executor.submit("orders-etl", ROWS)

    executor.execute(
        run.run_id,
        workflows=workflows,
        sources=sources,
        transformation_engine=DataTransformationEngine(),
        analytics=service,
    )

    records = service.list_records("orders-etl")
    assert len(records) == 1
    assert records[0].status == "success"
    assert records[0].row_count == 2


def test_executor_execute_without_analytics_does_not_error(executor: PipelineExecutionEngine, workflows, sources):
    run = executor.submit("orders-etl", ROWS)

    finished = executor.execute(run.run_id, workflows=workflows, sources=sources, transformation_engine=DataTransformationEngine())

    assert finished.state.value == "succeeded"


def test_api_list_metrics(client: TestClient, service: PipelineAnalyticsService):
    service.record("orders-etl", "success", 100.0, 10)

    response = client.get("/pipelines/analytics")

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_api_summary_endpoint(client: TestClient, service: PipelineAnalyticsService):
    service.record("orders-etl", "success", 100.0, 10)

    response = client.get("/pipelines/analytics/summary", params={"workflow_name": "orders-etl"})

    assert response.status_code == 200
    assert response.json()["execution_count"] == 1


def test_api_trends_endpoint(client: TestClient, service: PipelineAnalyticsService):
    service.record("orders-etl", "success", 100.0, 10)

    response = client.get("/pipelines/analytics/trends")

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_api_trends_invalid_bucket_returns_422(client: TestClient):
    response = client.get("/pipelines/analytics/trends", params={"bucket": "month"})

    assert response.status_code == 422


def test_api_execute_records_analytics(client: TestClient, service: PipelineAnalyticsService):
    client.post("/pipelines/execute", json={"workflow_name": "orders-etl", "rows": ROWS})

    records = service.list_records("orders-etl")
    assert len(records) == 1
