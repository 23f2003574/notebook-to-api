import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.gateway.middleware import (
    AuthenticationMiddleware,
    CompressionMiddleware,
    CORSMiddleware,
    LoggingMiddleware,
    Middleware,
    MiddlewareAlreadyRegisteredError,
    MiddlewareContext,
    MiddlewarePipeline,
    UnknownMiddlewareError,
    get_middleware_pipeline,
    router as middleware_router,
)


@pytest.fixture
def pipeline() -> MiddlewarePipeline:
    return MiddlewarePipeline()


@pytest.fixture
def client(pipeline: MiddlewarePipeline) -> TestClient:
    app = FastAPI()
    app.include_router(middleware_router)
    app.dependency_overrides[get_middleware_pipeline] = lambda: pipeline
    return TestClient(app)


def test_register_creates_middleware(pipeline: MiddlewarePipeline):
    middleware = pipeline.register("logging", before=lambda ctx: None)

    assert isinstance(middleware, Middleware)
    assert middleware.name == "logging"


def test_register_requires_at_least_one_hook(pipeline: MiddlewarePipeline):
    with pytest.raises(ValueError):
        pipeline.register("noop")


def test_register_rejects_empty_name(pipeline: MiddlewarePipeline):
    with pytest.raises(ValueError):
        pipeline.register("", before=lambda ctx: None)


def test_register_rejects_duplicate_name(pipeline: MiddlewarePipeline):
    pipeline.register("logging", before=lambda ctx: None)

    with pytest.raises(MiddlewareAlreadyRegisteredError):
        pipeline.register("logging", before=lambda ctx: None)


def test_remove_deletes_middleware(pipeline: MiddlewarePipeline):
    pipeline.register("logging", before=lambda ctx: None)

    pipeline.remove("logging")

    assert pipeline.list_middleware() == []


def test_remove_unknown_middleware_raises(pipeline: MiddlewarePipeline):
    with pytest.raises(UnknownMiddlewareError):
        pipeline.remove("does-not-exist")


def test_execute_before_runs_hooks_in_registration_order(pipeline: MiddlewarePipeline):
    order = []
    pipeline.register("first", before=lambda ctx: order.append("first"))
    pipeline.register("second", before=lambda ctx: order.append("second"))

    context = MiddlewareContext(path="/notebooks", method="GET")
    pipeline.execute_before(context)

    assert order == ["first", "second"]
    assert context.executed == ["first", "second"]


def test_execute_before_respects_explicit_priority(pipeline: MiddlewarePipeline):
    order = []
    pipeline.register("second", before=lambda ctx: order.append("second"), priority=10)
    pipeline.register("first", before=lambda ctx: order.append("first"), priority=1)

    context = MiddlewareContext(path="/notebooks", method="GET")
    pipeline.execute_before(context)

    assert order == ["first", "second"]


def test_execute_after_runs_hooks_in_reverse_order(pipeline: MiddlewarePipeline):
    order = []
    pipeline.register("first", before=lambda ctx: None, after=lambda ctx: order.append("first"))
    pipeline.register("second", before=lambda ctx: None, after=lambda ctx: order.append("second"))

    context = MiddlewareContext(path="/notebooks", method="GET")
    pipeline.execute_before(context)
    pipeline.execute_after(context)

    assert order == ["second", "first"]


def test_execute_before_short_circuits_remaining_middleware(pipeline: MiddlewarePipeline):
    order = []

    def halting_middleware(ctx: MiddlewareContext) -> None:
        order.append("halt")
        ctx.short_circuited = True
        ctx.response = {"error": "unauthorized"}

    pipeline.register("halt", before=halting_middleware)
    pipeline.register("never", before=lambda ctx: order.append("never"))

    context = MiddlewareContext(path="/notebooks", method="GET")
    pipeline.execute_before(context)

    assert order == ["halt"]
    assert context.short_circuited is True
    assert context.response == {"error": "unauthorized"}
    assert context.executed == ["halt"]


def test_execute_after_only_runs_for_executed_middleware(pipeline: MiddlewarePipeline):
    order = []
    pipeline.register(
        "halt",
        before=lambda ctx: setattr(ctx, "short_circuited", True),
        after=lambda ctx: order.append("halt"),
    )
    pipeline.register("never", before=lambda ctx: None, after=lambda ctx: order.append("never"))

    context = MiddlewareContext(path="/notebooks", method="GET")
    pipeline.execute_before(context)
    pipeline.execute_after(context)

    assert order == ["halt"]


def test_execute_before_records_timings(pipeline: MiddlewarePipeline):
    pipeline.register("logging", before=lambda ctx: None)

    context = MiddlewareContext(path="/notebooks", method="GET")
    pipeline.execute_before(context)

    assert "logging.before" in context.timings
    assert context.timings["logging.before"] >= 0


def test_authentication_middleware_short_circuits_missing_token(pipeline: MiddlewarePipeline):
    auth = AuthenticationMiddleware(required_token="secret")
    pipeline.register("authentication", before=auth.before)

    context = MiddlewareContext(path="/notebooks", method="GET", payload={})
    pipeline.execute_before(context)

    assert context.short_circuited is True
    assert context.response == {"error": "unauthorized"}


def test_authentication_middleware_allows_valid_token(pipeline: MiddlewarePipeline):
    auth = AuthenticationMiddleware(required_token="secret")
    pipeline.register("authentication", before=auth.before)

    context = MiddlewareContext(path="/notebooks", method="GET", payload={"token": "secret"})
    pipeline.execute_before(context)

    assert context.short_circuited is False


def test_logging_middleware_records_before_and_after(pipeline: MiddlewarePipeline):
    logger = LoggingMiddleware()
    pipeline.register("logging", before=logger.before, after=logger.after)

    context = MiddlewareContext(path="/notebooks", method="GET")
    pipeline.execute_before(context)
    pipeline.execute_after(context)

    assert [entry["phase"] for entry in logger.entries] == ["before", "after"]


def test_cors_middleware_sets_headers(pipeline: MiddlewarePipeline):
    cors = CORSMiddleware(allowed_origins=("https://example.com",))
    pipeline.register("cors", after=cors.after)

    context = MiddlewareContext(path="/notebooks", method="GET")
    pipeline.execute_before(context)
    pipeline.execute_after(context)

    assert context.state["cors_headers"] == {"Access-Control-Allow-Origin": "https://example.com"}


def test_compression_middleware_flags_large_payloads(pipeline: MiddlewarePipeline):
    compression = CompressionMiddleware(minimum_size=5)
    pipeline.register("compression", after=compression.after)

    context = MiddlewareContext(path="/notebooks", method="GET", payload={"value": "a lot of data"})
    pipeline.execute_before(context)
    pipeline.execute_after(context)

    assert context.state["compressed"] is True


def test_api_register_and_list_middleware(client: TestClient):
    response = client.post("/gateway/middleware", json={"type": "logging"})
    assert response.status_code == 201
    assert response.json()["name"] == "logging"

    listed = client.get("/gateway/middleware")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_api_register_unknown_type_returns_422(client: TestClient):
    response = client.post("/gateway/middleware", json={"type": "does-not-exist"})

    assert response.status_code == 422


def test_api_register_duplicate_returns_409(client: TestClient):
    client.post("/gateway/middleware", json={"type": "logging"})
    response = client.post("/gateway/middleware", json={"type": "logging"})

    assert response.status_code == 409


def test_api_register_with_custom_name(client: TestClient):
    response = client.post(
        "/gateway/middleware", json={"type": "cors", "name": "my-cors", "config": {"allowed_origins": ["*"]}}
    )

    assert response.status_code == 201
    assert response.json()["name"] == "my-cors"


def test_api_delete_removes_middleware(client: TestClient):
    client.post("/gateway/middleware", json={"type": "logging"})

    response = client.delete("/gateway/middleware/logging")
    assert response.status_code == 204

    listed = client.get("/gateway/middleware")
    assert listed.json() == []


def test_api_delete_unknown_middleware_returns_404(client: TestClient):
    response = client.delete("/gateway/middleware/does-not-exist")

    assert response.status_code == 404
