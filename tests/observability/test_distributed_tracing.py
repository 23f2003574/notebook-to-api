import pytest

from backend.observability.distributed_tracing import DistributedTracingEngine
from backend.observability.telemetry_collector import TelemetryCollector


@pytest.fixture
def engine():
    return DistributedTracingEngine()


class TestSpanLifecycle:
    def test_start_span_creates_context(self, engine):
        context = engine.start_span("GET /users", "http")

        assert context.trace_id
        assert context.span_id
        assert context.parent_span_id is None

    def test_start_span_rejects_unknown_type(self, engine):
        with pytest.raises(ValueError):
            engine.start_span("bad_span", "unknown_type")

    def test_finish_span_sets_duration(self, engine):
        context = engine.start_span("query users", "database")

        span = engine.finish_span(context.span_id)

        assert span.end_time is not None
        assert span.duration_ms is not None
        assert span.duration_ms >= 0

    def test_finish_span_twice_raises(self, engine):
        context = engine.start_span("query users", "database")
        engine.finish_span(context.span_id)

        with pytest.raises(ValueError):
            engine.finish_span(context.span_id)

    def test_finish_unknown_span_raises(self, engine):
        with pytest.raises(KeyError):
            engine.finish_span("does-not-exist")


class TestParentChildLinkage:
    def test_start_span_with_parent(self, engine):
        parent = engine.start_span("handle request", "http")
        child = engine.start_span(
            "run inference", "inference", trace_id=parent.trace_id, parent_span_id=parent.span_id
        )

        assert child.parent_span_id == parent.span_id
        assert child.trace_id == parent.trace_id

    def test_start_span_with_unknown_parent_raises(self, engine):
        with pytest.raises(KeyError):
            engine.start_span("orphan", "worker", parent_span_id="missing")

    def test_link_reassigns_parent(self, engine):
        parent = engine.start_span("handle request", "http")
        other_parent = engine.start_span("pipeline step", "pipeline")
        child = engine.start_span("worker task", "worker")

        linked = engine.link(other_parent.span_id, child.span_id)

        assert linked.parent_span_id == other_parent.span_id

    def test_link_unknown_span_raises(self, engine):
        parent = engine.start_span("handle request", "http")

        with pytest.raises(KeyError):
            engine.link(parent.span_id, "missing-child")


class TestContextPropagation:
    def test_child_spans_share_trace_id(self, engine):
        parent = engine.start_span("handle request", "http")
        child = engine.start_span(
            "query db", "database", trace_id=parent.trace_id, parent_span_id=parent.span_id
        )

        assert child.trace_id == parent.trace_id

    def test_collect_traces_correlates_with_telemetry(self, engine):
        collector = TelemetryCollector()
        parent = engine.start_span("handle request", "http")
        engine.finish_span(parent.span_id)

        records = collector.collect_traces(engine, parent.trace_id)

        assert len(records) == 1
        assert records[0].source == "traces"
        assert records[0].payload["trace_id"] == parent.trace_id


class TestTraceReconstruction:
    def test_trace_returns_all_spans_in_order(self, engine):
        parent = engine.start_span("handle request", "http")
        child_one = engine.start_span(
            "query db", "database", trace_id=parent.trace_id, parent_span_id=parent.span_id
        )
        child_two = engine.start_span(
            "run inference", "inference", trace_id=parent.trace_id, parent_span_id=parent.span_id
        )

        spans = engine.trace(parent.trace_id)

        assert [span.span_id for span in spans] == [
            parent.span_id,
            child_one.span_id,
            child_two.span_id,
        ]

    def test_trace_unknown_trace_id_returns_empty(self, engine):
        assert engine.trace("unknown-trace") == []
